"""Unit tests for PMGConfigFlow and PMGOptionsFlow (config_flow.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.proxmox_mail_gateway.api import (
    PMGAccessForbiddenError,
    PMGAuthError,
    PMGConnectionError,
    PMGError,
    PMGSSLError,
    PMGTimeoutError,
)
from custom_components.proxmox_mail_gateway.config_flow import (
    PMGConfigFlow,
    PMGOptionsFlow,
    _clean_host_and_port,
    _normalize_username,
    _test_connection,
)
from custom_components.proxmox_mail_gateway.const import (
    CONF_ENABLE_NODE_SENSORS,
    CONF_ENABLE_POSTFIX_SENSORS,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_USER_INPUT: dict[str, Any] = {
    CONF_HOST: "192.168.222.30",
    CONF_PORT: 8006,
    CONF_USERNAME: "homeassistant@pmg",
    CONF_PASSWORD: "secret_password",
    CONF_VERIFY_SSL: False,
}


def _make_hass() -> HomeAssistant:
    hass = HomeAssistant()
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    return hass


def _make_flow() -> PMGConfigFlow:
    flow = PMGConfigFlow()
    flow.hass = _make_hass()
    flow.context = {}
    return flow


def _make_entry(options: dict[str, Any] | None = None) -> ConfigEntry:
    return ConfigEntry(
        entry_id="test-entry",
        domain="proxmox_mail_gateway",
        title="PMG test",
        data={
            CONF_HOST: "192.168.222.30",
            CONF_PORT: 8006,
            CONF_USERNAME: "homeassistant@pmg",
            CONF_PASSWORD: "current_secret",
            CONF_VERIFY_SSL: False,
        },
        options=options or {},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Sanitization & Normalization helpers
# ══════════════════════════════════════════════════════════════════════════════


class TestSanitizationHelpers:
    """Tests for _clean_host_and_port and _normalize_username."""

    def test_clean_host_strips_scheme_and_slashes(self) -> None:
        host, port = _clean_host_and_port("https://192.168.222.30:8006/")
        assert host == "192.168.222.30"
        assert port == 8006

    def test_clean_host_plain_ip(self) -> None:
        host, port = _clean_host_and_port("192.168.222.30")
        assert host == "192.168.222.30"
        assert port == 8006

    def test_normalize_username_appends_pmg_realm(self) -> None:
        assert _normalize_username("homeassistant") == "homeassistant@pmg"
        assert _normalize_username("admin") == "admin@pmg"

    def test_normalize_username_preserves_existing_realm(self) -> None:
        assert _normalize_username("homeassistant@pmg") == "homeassistant@pmg"
        assert _normalize_username("user@custom") == "user@custom"

    def test_normalize_username_defaults_root_to_pam(self) -> None:
        assert _normalize_username("root") == "root@pam"


# ══════════════════════════════════════════════════════════════════════════════
# _test_connection helper
# ══════════════════════════════════════════════════════════════════════════════


class TestConnectionHelper:
    """Tests for the _test_connection standalone function."""

    async def test_returns_none_on_success(self) -> None:
        """_test_connection returns (None, '') when login & permissions succeed."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(return_value=None)
            mock_client.get_spam_status = AsyncMock(return_value=MagicMock())
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", False, MagicMock()
            )

        assert key is None
        assert detail == ""

    async def test_returns_ssl_error_on_pmg_ssl_error(self) -> None:
        """PMGSSLError → 'ssl_error' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGSSLError("self signed cert"))
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", True, MagicMock()
            )

        assert key == "ssl_error"
        assert "self signed cert" in detail

    async def test_returns_timeout_on_pmg_timeout(self) -> None:
        """PMGTimeoutError → 'timeout_connect' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGTimeoutError("timed out 30s"))
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", False, MagicMock()
            )

        assert key == "timeout_connect"
        assert "timed out" in detail

    async def test_returns_insufficient_permissions(self) -> None:
        """PMGAccessForbiddenError → 'insufficient_permissions' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(return_value=None)
            mock_client.get_spam_status = AsyncMock(
                side_effect=PMGAccessForbiddenError("HTTP 403 Forbidden")
            )
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", False, MagicMock()
            )

        assert key == "insufficient_permissions"
        assert "403" in detail

    async def test_returns_invalid_auth_on_pmg_auth_error(self) -> None:
        """PMGAuthError → 'invalid_auth' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGAuthError("bad creds"))
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "wrong", False, MagicMock()
            )

        assert key == "invalid_auth"
        assert "bad creds" in detail

    async def test_returns_cannot_connect_on_connection_error(self) -> None:
        """PMGConnectionError → 'cannot_connect' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGConnectionError("unreachable"))
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", False, MagicMock()
            )

        assert key == "cannot_connect"
        assert "unreachable" in detail

    async def test_returns_unknown_on_other_pmg_error(self) -> None:
        """Generic PMGError → 'unknown' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGError("weird error"))
            mock_cls.return_value = mock_client

            key, detail = await _test_connection(
                "host", 8006, "user@pmg", "pass", False, MagicMock()
            )

        assert key == "unknown"
        assert "weird error" in detail


# ══════════════════════════════════════════════════════════════════════════════
# ConfigFlow — step_user
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigFlowStepUser:
    """Tests for PMGConfigFlow.async_step_user."""

    async def test_shows_form_when_no_input(self) -> None:
        """With no user_input, the form is shown."""
        flow = _make_flow()
        result = await flow.async_step_user(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result.get("errors", {}) == {}

    async def test_creates_entry_on_successful_connection(self) -> None:
        """Valid credentials → config entry is created."""
        flow = _make_flow()

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=(None, "")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=VALID_USER_INPUT)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_HOST] == "192.168.222.30"
        assert result["data"][CONF_PORT] == 8006
        assert CONF_PASSWORD in result["data"]

    async def test_shows_invalid_auth_error(self) -> None:
        """Wrong password → form shown with 'invalid_auth' error."""
        flow = _make_flow()

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=("invalid_auth", "bad pass")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=VALID_USER_INPUT)

        assert result["type"] == "form"
        assert result["errors"].get("base") == "invalid_auth"

    async def test_shows_cannot_connect_error(self) -> None:
        """Network failure → form shown with 'cannot_connect' error."""
        flow = _make_flow()

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=("cannot_connect", "connection refused")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=VALID_USER_INPUT)

        assert result["type"] == "form"
        assert result["errors"].get("base") == "cannot_connect"

    async def test_host_is_stripped_of_scheme_and_whitespace(self) -> None:
        """https:// and whitespace in host is cleaned."""
        flow = _make_flow()
        user_input = {**VALID_USER_INPUT, CONF_HOST: "  https://192.168.222.30/  "}

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=(None, "")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_HOST] == "192.168.222.30"

    async def test_default_port_is_8006(self) -> None:
        """DEFAULT_PORT constant is 8006."""
        assert DEFAULT_PORT == 8006


# ══════════════════════════════════════════════════════════════════════════════
# Re-authentication flow
# ══════════════════════════════════════════════════════════════════════════════


class TestReauthFlow:
    """Tests for async_step_reauth and async_step_reauth_confirm."""

    async def test_reauth_shows_all_parameters(self) -> None:
        """Reauth form shows all parameters pre-populated with current values."""
        flow = _make_flow()
        entry = _make_entry()
        flow._reauth_entry = entry

        result = await flow.async_step_reauth_confirm(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

    async def test_reauth_successful_updates_all_parameters(self) -> None:
        """Submitting updated credentials in reauth updates entry and reloads."""
        flow = _make_flow()
        entry = _make_entry()
        flow._reauth_entry = entry

        new_credentials = {
            CONF_HOST: "192.168.222.31",
            CONF_PORT: 8006,
            CONF_USERNAME: "homeassistant@pmg",
            CONF_PASSWORD: "new_super_secret",
            CONF_VERIFY_SSL: False,
        }

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=(None, "")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_reauth_confirm(user_input=new_credentials)

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        flow.hass.config_entries.async_update_entry.assert_called_once()
        flow.hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


# ══════════════════════════════════════════════════════════════════════════════
# OptionsFlow
# ══════════════════════════════════════════════════════════════════════════════


class TestOptionsFlow:
    """Tests for PMGOptionsFlow."""

    async def test_shows_form_when_no_input(self) -> None:
        """OptionsFlow shows form on initial call."""
        entry = _make_entry()
        flow = PMGOptionsFlow(entry)
        flow.hass = _make_hass()

        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

    async def test_saves_options_on_submit(self) -> None:
        """Submitted options and connection parameters are updated in the entry."""
        entry = _make_entry()
        flow = PMGOptionsFlow(entry)
        flow.hass = _make_hass()

        options_payload = {
            CONF_HOST: "192.168.222.30",
            CONF_PORT: 8006,
            CONF_USERNAME: "homeassistant@pmg",
            CONF_PASSWORD: "",  # leave empty = keep current password
            CONF_SCAN_INTERVAL: 120,
            CONF_VERIFY_SSL: False,
            CONF_ENABLE_POSTFIX_SENSORS: True,
            CONF_ENABLE_NODE_SENSORS: False,
        }
        result = await flow.async_step_init(user_input=options_payload)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_SCAN_INTERVAL] == 120

    async def test_options_flow_shows_error_when_connection_fails(self) -> None:
        """When connection parameters change and test fails, form shows error."""
        entry = _make_entry()
        flow = PMGOptionsFlow(entry)
        flow.hass = _make_hass()

        options_payload = {
            CONF_HOST: "192.168.222.99",
            CONF_PORT: 8006,
            CONF_USERNAME: "homeassistant@pmg",
            CONF_PASSWORD: "new_password",
            CONF_SCAN_INTERVAL: 60,
            CONF_VERIFY_SSL: False,
            CONF_ENABLE_POSTFIX_SENSORS: True,
            CONF_ENABLE_NODE_SENSORS: True,
        }

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=("cannot_connect", "Host unreachable")),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_init(user_input=options_payload)

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"
        assert result.get("description_placeholders", {}).get("error_detail") == "Host unreachable"

    async def test_options_flow_accessible_via_config_flow(self) -> None:
        """PMGConfigFlow.async_get_options_flow returns a PMGOptionsFlow."""
        entry = _make_entry()
        options_flow = PMGConfigFlow.async_get_options_flow(entry)
        assert isinstance(options_flow, PMGOptionsFlow)

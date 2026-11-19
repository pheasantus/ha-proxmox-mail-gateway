"""Unit tests for PMGConfigFlow and PMGOptionsFlow (config_flow.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.proxmox_mail_gateway.api import (
    PMGAuthError,
    PMGConnectionError,
    PMGError,
)
from custom_components.proxmox_mail_gateway.config_flow import (
    PMGConfigFlow,
    PMGOptionsFlow,
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
# _test_connection helper
# ══════════════════════════════════════════════════════════════════════════════


class TestConnectionHelper:
    """Tests for the _test_connection standalone function."""

    async def test_returns_none_on_success(self) -> None:
        """_test_connection returns None when login succeeds."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = await _test_connection("host", 8006, "user@pmg", "pass", False, MagicMock())

        assert result is None

    async def test_returns_invalid_auth_on_pmg_auth_error(self) -> None:
        """PMGAuthError → 'invalid_auth' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGAuthError("bad creds"))
            mock_cls.return_value = mock_client

            result = await _test_connection("host", 8006, "user@pmg", "wrong", False, MagicMock())

        assert result == "invalid_auth"

    async def test_returns_cannot_connect_on_connection_error(self) -> None:
        """PMGConnectionError → 'cannot_connect' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGConnectionError("unreachable"))
            mock_cls.return_value = mock_client

            result = await _test_connection("host", 8006, "user@pmg", "pass", False, MagicMock())

        assert result == "cannot_connect"

    async def test_returns_unknown_on_other_pmg_error(self) -> None:
        """Generic PMGError → 'unknown' error key."""
        with patch("custom_components.proxmox_mail_gateway.config_flow.PMGApiClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.login = AsyncMock(side_effect=PMGError("weird error"))
            mock_cls.return_value = mock_client

            result = await _test_connection("host", 8006, "user@pmg", "pass", False, MagicMock())

        assert result == "unknown"


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
                AsyncMock(return_value=None),
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
        # Password must be stored (never logged but must exist in data)
        assert CONF_PASSWORD in result["data"]

    async def test_shows_invalid_auth_error(self) -> None:
        """Wrong password → form shown with 'invalid_auth' error."""
        flow = _make_flow()

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value="invalid_auth"),
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
                AsyncMock(return_value="cannot_connect"),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=VALID_USER_INPUT)

        assert result["type"] == "form"
        assert result["errors"].get("base") == "cannot_connect"

    async def test_host_is_stripped_of_whitespace(self) -> None:
        """Leading/trailing whitespace in host is stripped."""
        flow = _make_flow()
        user_input = {**VALID_USER_INPUT, CONF_HOST: "  192.168.222.30  "}

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_HOST] == "192.168.222.30"

    async def test_verify_ssl_stored_in_data(self) -> None:
        """verify_ssl value from form is stored in entry data."""
        flow = _make_flow()
        user_input = {**VALID_USER_INPUT, CONF_VERIFY_SSL: True}

        with (
            patch(
                "custom_components.proxmox_mail_gateway.config_flow._test_connection",
                AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.proxmox_mail_gateway.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
        ):
            result = await flow.async_step_user(user_input=user_input)

        assert result["data"][CONF_VERIFY_SSL] is True

    async def test_default_port_is_8006(self) -> None:
        """DEFAULT_PORT constant is 8006 (required by PMG spec)."""
        assert DEFAULT_PORT == 8006


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
        """Submitted options are stored in the entry."""
        entry = _make_entry()
        flow = PMGOptionsFlow(entry)
        flow.hass = _make_hass()

        new_options = {
            CONF_SCAN_INTERVAL: 120,
            CONF_VERIFY_SSL: False,
            CONF_ENABLE_POSTFIX_SENSORS: True,
            CONF_ENABLE_NODE_SENSORS: False,
        }
        result = await flow.async_step_init(user_input=new_options)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_SCAN_INTERVAL] == 120

    async def test_pre_populates_current_options(self) -> None:
        """Form schema includes current option values as defaults."""
        entry = _make_entry(options={CONF_SCAN_INTERVAL: 300})
        flow = PMGOptionsFlow(entry)
        flow.hass = _make_hass()

        result = await flow.async_step_init(user_input=None)

        # The schema should contain the current value as a default.
        # We verify by inspecting the schema's defaults via voluptuous.
        schema = result["data_schema"]
        # Look for CONF_SCAN_INTERVAL key with default 300
        for key in schema.schema:
            if hasattr(key, "schema") and key.schema == CONF_SCAN_INTERVAL:
                assert key.default() == 300
                break

    async def test_options_flow_accessible_via_config_flow(self) -> None:
        """PMGConfigFlow.async_get_options_flow returns a PMGOptionsFlow."""
        entry = _make_entry()
        options_flow = PMGConfigFlow.async_get_options_flow(entry)
        assert isinstance(options_flow, PMGOptionsFlow)

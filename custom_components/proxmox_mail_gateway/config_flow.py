"""Config flow for Proxmox Mail Gateway integration.

Supports:
- Initial discovery & setup (``async_step_user``)
- Re-authentication with full credential editing (``async_step_reauth_confirm``)
- Comprehensive options flow with connection & sensor configuration (``PMGOptionsFlow``)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PMGApiClient, PMGAuthError, PMGConnectionError, PMGError
from .const import (
    CONF_ENABLE_NODE_SENSORS,
    CONF_ENABLE_POSTFIX_SENSORS,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_ENABLE_NODE_SENSORS,
    DEFAULT_ENABLE_POSTFIX_SENSORS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    STEP_OPTIONS,
    STEP_REAUTH,
    STEP_USER,
)

_LOGGER = logging.getLogger(__name__)


def _clean_host_and_port(host_input: str, port_input: int = DEFAULT_PORT) -> tuple[str, int]:
    """Clean host string, stripping http(s):// scheme, trailing slashes, and embedded port."""
    cleaned = host_input.strip()
    cleaned = cleaned.removeprefix("https://").removeprefix("http://").rstrip("/")
    if ":" in cleaned:
        host_part, _, port_part = cleaned.partition(":")
        cleaned = host_part
        if port_part.isdigit():
            port_input = int(port_part)
    return cleaned, port_input


def _normalize_username(username_input: str) -> str:
    """Ensure username has a realm suffix (@pmg or @pam); default to @pmg."""
    user = username_input.strip()
    if "@" not in user:
        return f"{user}@pam" if user == "root" else f"{user}@pmg"
    return user


# ── Form schemas ───────────────────────────────────────────────────────────────

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_USERNAME, default="homeassistant@pmg"): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)


# ── Helper: connection tester ──────────────────────────────────────────────────


async def _test_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    verify_ssl: bool,
    session: Any,
) -> str | None:
    """Attempt a PMG login and return an error key on failure, or ``None`` on success."""
    client = PMGApiClient(
        host=host,
        port=port,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        session=session,
    )
    try:
        await client.login()
    except PMGAuthError as exc:
        _LOGGER.warning("PMG authentication failed for user '%s': %s", username, exc)
        return "invalid_auth"
    except PMGConnectionError as exc:
        _LOGGER.warning(
            "PMG connection failed to %s:%s (verify_ssl=%s): %s", host, port, verify_ssl, exc
        )
        return "cannot_connect"
    except PMGError as exc:
        _LOGGER.warning("PMG error during connection test: %s", exc)
        return "unknown"
    except Exception as exc:
        _LOGGER.exception("PMG unexpected exception during connection test: %s", exc)
        return "unknown"
    return None


class PMGConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proxmox Mail Gateway."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._reauth_entry: ConfigEntry | None = None

    # ── Step: user (initial setup) ────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial user setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_host: str = user_input[CONF_HOST]
            raw_port: int = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            host, port = _clean_host_and_port(raw_host, raw_port)
            username: str = _normalize_username(user_input[CONF_USERNAME])
            password: str = user_input[CONF_PASSWORD]
            verify_ssl: bool = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

            # Prevent duplicate entries for the same host.
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            error_key = await _test_connection(host, port, username, password, verify_ssl, session)

            if error_key is None:
                _LOGGER.info(
                    "PMG ConfigFlow: connection verified for %s@%s:%s", username, host, port
                )
                return self.async_create_entry(
                    title=f"PMG {host}",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )
            errors["base"] = error_key

        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    # ── Step: re-auth ─────────────────────────────────────────────────────────

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Initiate re-authentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the re-authentication form with all connection fields editable."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        current_data = entry.data if entry else {}

        if user_input is not None and entry:
            raw_host: str = user_input.get(CONF_HOST, current_data.get(CONF_HOST, ""))
            raw_port: int = int(
                user_input.get(CONF_PORT, current_data.get(CONF_PORT, DEFAULT_PORT))
            )
            host, port = _clean_host_and_port(raw_host, raw_port)
            username: str = _normalize_username(
                user_input.get(CONF_USERNAME, current_data.get(CONF_USERNAME, ""))
            )
            password: str = user_input[CONF_PASSWORD]
            verify_ssl: bool = bool(
                user_input.get(
                    CONF_VERIFY_SSL, current_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                )
            )

            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            error_key = await _test_connection(
                host=host,
                port=port,
                username=username,
                password=password,
                verify_ssl=verify_ssl,
                session=session,
            )
            if error_key is None:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error_key

        reauth_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=current_data.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_USERNAME, default=current_data.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id=STEP_REAUTH,
            data_schema=reauth_schema,
            errors=errors,
        )

    # ── Options flow factory ──────────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PMGOptionsFlow:
        """Return the options flow handler for this integration."""
        return PMGOptionsFlow(config_entry)


class PMGOptionsFlow(OptionsFlow):
    """Handle options and parameter configuration for an existing PMG config entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()
        self.config_entry: ConfigEntry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the options form allowing adjustment of parameters."""
        errors: dict[str, str] = {}
        entry = self.config_entry
        current_data = entry.data
        current_opts = entry.options

        if user_input is not None:
            raw_host: str = user_input.get(CONF_HOST, current_data.get(CONF_HOST, ""))
            raw_port: int = int(
                user_input.get(CONF_PORT, current_data.get(CONF_PORT, DEFAULT_PORT))
            )
            host, port = _clean_host_and_port(raw_host, raw_port)
            username: str = _normalize_username(
                user_input.get(CONF_USERNAME, current_data.get(CONF_USERNAME, ""))
            )
            new_password: str = user_input.get(CONF_PASSWORD, "").strip()
            password: str = new_password if new_password else current_data.get(CONF_PASSWORD, "")
            verify_ssl: bool = bool(
                user_input.get(
                    CONF_VERIFY_SSL,
                    current_opts.get(
                        CONF_VERIFY_SSL, current_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                    ),
                )
            )

            # Check if any connection-level parameter changed
            conn_changed = (
                host != current_data.get(CONF_HOST)
                or port != current_data.get(CONF_PORT)
                or username != current_data.get(CONF_USERNAME)
                or bool(new_password)
                or verify_ssl != current_data.get(CONF_VERIFY_SSL)
            )

            if conn_changed:
                session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
                error_key = await _test_connection(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    verify_ssl=verify_ssl,
                    session=session,
                )
                if error_key is not None:
                    errors["base"] = error_key
                else:
                    new_data = {
                        **current_data,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: verify_ssl,
                    }
                    self.hass.config_entries.async_update_entry(entry, data=new_data)

            if not errors:
                options_data = {
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_VERIFY_SSL: verify_ssl,
                    CONF_ENABLE_POSTFIX_SENSORS: user_input[CONF_ENABLE_POSTFIX_SENSORS],
                    CONF_ENABLE_NODE_SENSORS: user_input[CONF_ENABLE_NODE_SENSORS],
                }
                return self.async_create_entry(title="", data=options_data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=current_data.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_USERNAME, default=current_data.get(CONF_USERNAME, "")): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current_opts.get(
                        CONF_VERIFY_SSL, current_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                    ),
                ): bool,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current_opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_ENABLE_POSTFIX_SENSORS,
                    default=current_opts.get(
                        CONF_ENABLE_POSTFIX_SENSORS, DEFAULT_ENABLE_POSTFIX_SENSORS
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_NODE_SENSORS,
                    default=current_opts.get(CONF_ENABLE_NODE_SENSORS, DEFAULT_ENABLE_NODE_SENSORS),
                ): bool,
            }
        )

        return self.async_show_form(step_id=STEP_OPTIONS, data_schema=schema, errors=errors)

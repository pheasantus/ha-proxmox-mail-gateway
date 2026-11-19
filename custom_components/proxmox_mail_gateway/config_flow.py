"""Config flow and options flow for Proxmox Mail Gateway.

Provides a UI-driven setup wizard that:
1. Collects host / port / credentials / TLS preference.
2. Validates the connection by attempting a live PMG login.
3. Persists credentials in the config entry's ``data`` store.
4. Exposes an OptionsFlow for tuning scan interval and optional sensors.

Security notes
--------------
* The password field uses ``vol.Required`` without logging the value.
* Connection test results are displayed only as generic error codes
  (``cannot_connect``, ``invalid_auth``) — no internal details are exposed.
* Re-authentication flow is supported via ``async_step_reauth``.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
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

# ── Form schemas ───────────────────────────────────────────────────────────────

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=3600)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Optional(CONF_ENABLE_POSTFIX_SENSORS, default=DEFAULT_ENABLE_POSTFIX_SENSORS): bool,
        vol.Optional(CONF_ENABLE_NODE_SENSORS, default=DEFAULT_ENABLE_NODE_SENSORS): bool,
    }
)


async def _test_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    verify_ssl: bool,
    session: aiohttp.ClientSession,
) -> str | None:
    """Attempt a PMG login and return an error key on failure, or ``None`` on success.

    Args:
        host:       PMG hostname or IP.
        port:       PMG API port.
        username:   PMG username.
        password:   PMG password (never logged).
        verify_ssl: Whether to verify TLS certificates.
        session:    aiohttp session to reuse.

    Returns:
        ``None`` on success, or one of ``"cannot_connect"`` / ``"invalid_auth"`` /
        ``"unknown"`` on failure.
    """
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
    except PMGAuthError:
        return "invalid_auth"
    except PMGConnectionError:
        return "cannot_connect"
    except PMGError:
        return "unknown"
    return None


class PMGConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proxmox Mail Gateway.

    Implements:
    - ``step_user``:  Initial setup form.
    - ``step_reauth_confirm``: Re-authentication after credential expiry.
    """

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._reauth_entry: ConfigEntry | None = None

    # ── Step: user (initial setup) ────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial user setup step.

        Shows the connection form on first call; validates and creates the
        config entry when *user_input* is provided.

        Args:
            user_input: Form data submitted by the user, or ``None`` to show the form.

        Returns:
            A :data:`~homeassistant.data_entry_flow.FlowResult` dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            host: str = user_input[CONF_HOST].strip()
            port: int = int(user_input[CONF_PORT])
            username: str = user_input[CONF_USERNAME].strip()
            password: str = user_input[CONF_PASSWORD]
            verify_ssl: bool = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

            # Prevent duplicate entries for the same host.
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            error_key = await _test_connection(host, port, username, password, verify_ssl, session)

            if error_key is None:
                _LOGGER.info("PMG ConfigFlow: connection verified for host '%s'", host)
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
        """Initiate re-authentication.

        Called automatically by HA when the integration raises
        :class:`~homeassistant.exceptions.ConfigEntryAuthFailed`.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the re-authentication form (password only).

        Args:
            user_input: New password submitted by the user, or ``None`` to show form.

        Returns:
            A :data:`~homeassistant.data_entry_flow.FlowResult`.
        """
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry:
            entry = self._reauth_entry
            verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            error_key = await _test_connection(
                host=entry.data[CONF_HOST],
                port=entry.data[CONF_PORT],
                username=entry.data[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                verify_ssl=verify_ssl,
                session=session,
            )
            if error_key is None:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error_key

        return self.async_show_form(
            step_id=STEP_REAUTH,
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    # ── Options flow factory ──────────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PMGOptionsFlow:
        """Return the options flow handler for this integration.

        Args:
            config_entry: The active config entry to configure options for.

        Returns:
            A :class:`PMGOptionsFlow` instance.
        """
        return PMGOptionsFlow(config_entry)


class PMGOptionsFlow(OptionsFlow):
    """Handle options configuration for an existing PMG config entry.

    Allows the user to adjust:
    - Scan interval (10–3600 s)
    - TLS certificate verification
    - Optional Postfix queue sensors
    - Optional node metrics sensors
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()
        self.config_entry: ConfigEntry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the options form.

        Args:
            user_input: Submitted option values, or ``None`` to show the form.

        Returns:
            A :data:`~homeassistant.data_entry_flow.FlowResult`.
        """
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pre-populate with current values or defaults.
        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_POSTFIX_SENSORS,
                    default=current.get(
                        CONF_ENABLE_POSTFIX_SENSORS, DEFAULT_ENABLE_POSTFIX_SENSORS
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_NODE_SENSORS,
                    default=current.get(CONF_ENABLE_NODE_SENSORS, DEFAULT_ENABLE_NODE_SENSORS),
                ): bool,
            }
        )

        return self.async_show_form(step_id=STEP_OPTIONS, data_schema=schema)

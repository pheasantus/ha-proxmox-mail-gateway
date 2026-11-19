"""Proxmox Mail Gateway integration for Home Assistant.

Registers the integration entry, forwards platform setup, and
registers the four quarantine-management services.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PMGApiClient
from .const import (
    ACTION_DELETE,
    ACTION_DELIVER,
    ACTION_WHITELIST,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
    VALID_ACTIONS,
)
from .coordinator import PMGDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# ── Service schemas ────────────────────────────────────────────────────────────
_MAIL_ID_SCHEMA = vol.Schema({vol.Required("mail_id"): cv.string})
_QUARANTINE_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("mail_id"): cv.string,
        vol.Required("action"): vol.In(list(VALID_ACTIONS)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Proxmox Mail Gateway from a config entry.

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry to set up.

    Returns:
        ``True`` on success.
    """
    verify_ssl: bool = entry.options.get(CONF_VERIFY_SSL, entry.data.get(CONF_VERIFY_SSL, False))
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    api = PMGApiClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
        session=session,
    )

    coordinator = PMGDataUpdateCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry to unload.

    Returns:
        ``True`` if all platforms unloaded successfully.
    """
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_first_coordinator(hass: HomeAssistant) -> PMGDataUpdateCoordinator:
    """Return the first active coordinator (single-instance integration).

    Raises:
        KeyError: If no PMG integration is currently configured.
    """
    entries: dict[str, PMGDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if not entries:
        raise KeyError("No Proxmox Mail Gateway integration is configured")
    return next(iter(entries.values()))


def _async_register_services(hass: HomeAssistant) -> None:
    """Register all PMG quarantine services.

    Idempotent: safe to call multiple times.
    """
    if hass.services.has_service(DOMAIN, "deliver_mail"):
        return

    async def _handle_deliver(call: ServiceCall) -> None:
        """Handle proxmox_mail_gateway.deliver_mail service."""
        await _get_first_coordinator(hass).async_perform_action(
            call.data["mail_id"], ACTION_DELIVER
        )

    async def _handle_delete(call: ServiceCall) -> None:
        """Handle proxmox_mail_gateway.delete_mail service."""
        await _get_first_coordinator(hass).async_perform_action(call.data["mail_id"], ACTION_DELETE)

    async def _handle_whitelist(call: ServiceCall) -> None:
        """Handle proxmox_mail_gateway.whitelist_mail service."""
        await _get_first_coordinator(hass).async_perform_action(
            call.data["mail_id"], ACTION_WHITELIST
        )

    async def _handle_quarantine_action(call: ServiceCall) -> None:
        """Handle proxmox_mail_gateway.quarantine_action service (generic)."""
        await _get_first_coordinator(hass).async_perform_action(
            call.data["mail_id"], call.data["action"]
        )

    hass.services.async_register(DOMAIN, "deliver_mail", _handle_deliver, schema=_MAIL_ID_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_mail", _handle_delete, schema=_MAIL_ID_SCHEMA)
    hass.services.async_register(
        DOMAIN, "whitelist_mail", _handle_whitelist, schema=_MAIL_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "quarantine_action", _handle_quarantine_action, schema=_QUARANTINE_ACTION_SCHEMA
    )
    _LOGGER.debug("PMG: registered quarantine services")

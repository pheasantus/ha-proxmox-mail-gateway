"""Button entities for Proxmox Mail Gateway integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DEFAULT_PORT, DOMAIN, MANUFACTURER, MODEL
from .coordinator import PMGDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    host: str = entry.data.get(CONF_HOST, "")
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"PMG {host}",
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "configuration_url": f"https://{host}:{entry.data.get('port', DEFAULT_PORT)}",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PMG button entities based on a config entry."""
    coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PMGDeliverSelectedButton(coordinator, entry),
            PMGDeleteSelectedButton(coordinator, entry),
            PMGWhitelistSelectedButton(coordinator, entry),
            PMGDeliverAllButton(coordinator, entry),
            PMGDeleteAllButton(coordinator, entry),
            PMGRefreshButton(coordinator, entry),
        ]
    )


class _PMGBaseButton(CoordinatorEntity[PMGDataUpdateCoordinator], ButtonEntity):
    """Base class for PMG buttons."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


class PMGDeliverSelectedButton(_PMGBaseButton):
    """Deliver the currently selected quarantined email."""

    _attr_translation_key = "deliver_selected"
    _attr_icon = "mdi:email-fast"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_deliver_selected"

    async def async_press(self) -> None:
        """Deliver selected message."""
        mail_id = self.coordinator.selected_mail_id
        if not mail_id and self.coordinator.data and self.coordinator.data.messages:
            mail_id = self.coordinator.data.messages[0].id
        if mail_id:
            _LOGGER.info("Delivering selected mail_id=%s from quarantine", mail_id)
            await self.coordinator.async_perform_action(mail_id, "deliver")


class PMGDeleteSelectedButton(_PMGBaseButton):
    """Permanently delete the currently selected quarantined email."""

    _attr_translation_key = "delete_selected"
    _attr_icon = "mdi:delete"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_delete_selected"

    async def async_press(self) -> None:
        """Delete selected message."""
        mail_id = self.coordinator.selected_mail_id
        if not mail_id and self.coordinator.data and self.coordinator.data.messages:
            mail_id = self.coordinator.data.messages[0].id
        if mail_id:
            _LOGGER.info("Deleting selected mail_id=%s from quarantine", mail_id)
            await self.coordinator.async_perform_action(mail_id, "delete")


class PMGWhitelistSelectedButton(_PMGBaseButton):
    """Whitelist sender and deliver the currently selected quarantined email."""

    _attr_translation_key = "whitelist_selected"
    _attr_icon = "mdi:shield-check"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_whitelist_selected"

    async def async_press(self) -> None:
        """Whitelist and deliver selected message."""
        mail_id = self.coordinator.selected_mail_id
        if not mail_id and self.coordinator.data and self.coordinator.data.messages:
            mail_id = self.coordinator.data.messages[0].id
        if mail_id:
            _LOGGER.info("Whitelisting and delivering selected mail_id=%s", mail_id)
            await self.coordinator.async_perform_action(mail_id, "whitelist")


class PMGDeliverAllButton(_PMGBaseButton):
    """Deliver all quarantined emails currently held."""

    _attr_translation_key = "deliver_all"
    _attr_icon = "mdi:email-multiple-outline"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_deliver_all"

    async def async_press(self) -> None:
        """Deliver all messages."""
        if self.coordinator.data and self.coordinator.data.messages:
            all_ids = ";".join(m.id for m in self.coordinator.data.messages)
            _LOGGER.info("Delivering all quarantined messages: %s", all_ids)
            await self.coordinator.async_perform_action(all_ids, "deliver")


class PMGDeleteAllButton(_PMGBaseButton):
    """Permanently delete all quarantined emails."""

    _attr_translation_key = "delete_all"
    _attr_icon = "mdi:delete-sweep"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_delete_all"

    async def async_press(self) -> None:
        """Delete all messages."""
        if self.coordinator.data and self.coordinator.data.messages:
            all_ids = ";".join(m.id for m in self.coordinator.data.messages)
            _LOGGER.info("Deleting all quarantined messages: %s", all_ids)
            await self.coordinator.async_perform_action(all_ids, "delete")


class PMGRefreshButton(_PMGBaseButton):
    """Trigger an immediate data refresh from PMG."""

    _attr_translation_key = "refresh_quarantine"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_refresh_quarantine"

    async def async_press(self) -> None:
        """Refresh data."""
        _LOGGER.debug("Manual refresh requested via button")
        await self.coordinator.async_request_refresh()

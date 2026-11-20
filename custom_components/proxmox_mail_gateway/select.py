"""Select entity for Proxmox Mail Gateway integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DEFAULT_PORT, DOMAIN, MANUFACTURER, MODEL
from .coordinator import PMGDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

EMPTY_OPTION = "Brak wiadomości w kwarantannie"


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
    """Set up PMG select entity based on a config entry."""
    coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PMGQuarantineSelect(coordinator, entry)])


class PMGQuarantineSelect(CoordinatorEntity[PMGDataUpdateCoordinator], SelectEntity):
    """Select entity allowing user to pick a quarantined email to act upon."""

    _attr_has_entity_name = True
    _attr_translation_key = "quarantine_mail"
    _attr_icon = "mdi:email-search"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_quarantine_mail_select"
        self._attr_device_info = _device_info(entry)

    @property
    def options(self) -> list[str]:
        """Return available options built from currently quarantined messages."""
        if not self.coordinator.data or not self.coordinator.data.messages:
            return [EMPTY_OPTION]
        opts: list[str] = []
        for m in self.coordinator.data.messages:
            subj = m.subject[:40] if m.subject else "(brak tematu)"
            sender = m.sender[:25] if m.sender else "(brak nadawcy)"
            opts.append(f"[{m.id}] {sender}: {subj} ({m.spam_score})")
        return opts

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        opts = self.options
        if opts == [EMPTY_OPTION]:
            return EMPTY_OPTION
        sel_id = self.coordinator.selected_mail_id
        if sel_id:
            for opt in opts:
                if f"[{sel_id}]" in opt:
                    return opt
        # Default to first available option
        if self.coordinator.data and self.coordinator.data.messages:
            first_msg = self.coordinator.data.messages[0]
            self.coordinator.selected_mail_id = first_msg.id
            for opt in opts:
                if f"[{first_msg.id}]" in opt:
                    return opt
        return opts[0] if opts else None

    async def async_select_option(self, option: str) -> None:
        """Change the selected quarantined message."""
        if "[" in option and "]" in option:
            self.coordinator.selected_mail_id = option.split("[", 1)[1].split("]", 1)[0]
        else:
            self.coordinator.selected_mail_id = None
        self.async_write_ha_state()

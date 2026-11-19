"""Sensor entities for Proxmox Mail Gateway.

Provides the following sensors:
- **Quarantine count** – total number of held messages (always enabled).
- **Average spam score** – mean spam level across all quarantined mail.
- **Postfix queue depths** – active / deferred / incoming (optional).
- **Node CPU usage** – PMG host CPU percentage (optional).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_NODE_SENSORS,
    CONF_ENABLE_POSTFIX_SENSORS,
    DEFAULT_ENABLE_NODE_SENSORS,
    DEFAULT_ENABLE_POSTFIX_SENSORS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import PMGDataUpdateCoordinator
from .models import PMGData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PMG sensor entities from a config entry.

    Args:
        hass:               The Home Assistant instance.
        entry:              The active config entry.
        async_add_entities: Callback to register new entities.
    """
    coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        PMGQuarantineCountSensor(coordinator, entry),
        PMGAvgSpamSensor(coordinator, entry),
    ]

    if entry.options.get(CONF_ENABLE_POSTFIX_SENSORS, DEFAULT_ENABLE_POSTFIX_SENSORS):
        entities.extend(
            [
                PMGPostfixQueueSensor(coordinator, entry, "active"),
                PMGPostfixQueueSensor(coordinator, entry, "deferred"),
                PMGPostfixQueueSensor(coordinator, entry, "incoming"),
            ]
        )

    if entry.options.get(CONF_ENABLE_NODE_SENSORS, DEFAULT_ENABLE_NODE_SENSORS):
        entities.append(PMGNodeCpuSensor(coordinator, entry))

    async_add_entities(entities)


# ── Base sensor ───────────────────────────────────────────────────────────────


class PMGBaseSensor(CoordinatorEntity[PMGDataUpdateCoordinator], SensorEntity):
    """Abstract base for all PMG sensors.

    Provides the shared device_info and coordinator wiring.
    """

    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: PMGDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information for grouping all PMG entities under one device."""
        host: str = self._entry.data.get("host", "unknown")
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"PMG {host}",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "configuration_url": (f"https://{host}:{self._entry.data.get('port', 8006)}"),
        }


# ── Quarantine count ──────────────────────────────────────────────────────────


class PMGQuarantineCountSensor(PMGBaseSensor):
    """Sensor reporting the number of messages held in quarantine.

    State: integer count of quarantined messages.
    Attributes:
        messages         – list of message dicts
                           (see :meth:`.models.QuarantineMessage.as_ha_attribute`)
        avg_spam_level   – mean spam score across all messages
        recipients       – unique list of recipient e-mail addresses
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_translation_key = "quarantine_count"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_quarantine_count"

    @property
    def icon(self) -> str:
        """Return a filled alert icon when messages are present, outline otherwise."""
        count = self.native_value
        return "mdi:email-alert" if count > 0 else "mdi:email-alert-outline"

    @property
    def native_value(self) -> int:
        """Return the total quarantined message count."""
        if not self.coordinator.data or not self.coordinator.data.status:
            return 0
        return self.coordinator.data.status.count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return message list and aggregate stats."""
        if not self.coordinator.data:
            return {}
        data: PMGData = self.coordinator.data
        return {
            "messages": [m.as_ha_attribute() for m in data.messages],
            "avg_spam_level": round(data.status.avg_spam, 2),
            "recipients": sorted({m.pmail for m in data.messages}),
        }


# ── Average spam score ────────────────────────────────────────────────────────


class PMGAvgSpamSensor(PMGBaseSensor):
    """Sensor reporting the average spam score across all quarantined messages.

    State: float, rounded to two decimal places.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:shield-bug-outline"
    _attr_translation_key = "avg_spam_score"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_spam_score"

    @property
    def native_value(self) -> float:
        """Return the mean spam score, or 0.0 if no data."""
        if not self.coordinator.data:
            return 0.0
        return round(self.coordinator.data.status.avg_spam, 2)


# ── Postfix queue sensors ─────────────────────────────────────────────────────


_POSTFIX_ICONS: dict[str, str] = {
    "active": "mdi:email-fast-outline",
    "deferred": "mdi:email-remove-outline",
    "incoming": "mdi:email-arrow-right-outline",
}


class PMGPostfixQueueSensor(PMGBaseSensor):
    """Sensor reporting the depth of a single Postfix mail queue.

    Args:
        queue_name: One of ``active``, ``deferred``, ``incoming``.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"

    def __init__(
        self,
        coordinator: PMGDataUpdateCoordinator,
        entry: ConfigEntry,
        queue_name: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._queue_name = queue_name
        self._attr_unique_id = f"{entry.entry_id}_postfix_{queue_name}"
        self._attr_translation_key = f"postfix_{queue_name}"
        self._attr_icon = _POSTFIX_ICONS.get(queue_name, "mdi:email-outline")

    @property
    def native_value(self) -> int:
        """Return the queue depth, or 0 if data is unavailable."""
        if not self.coordinator.data or self.coordinator.data.queues is None:
            return 0
        return getattr(self.coordinator.data.queues, self._queue_name, 0)


# ── Node CPU sensor ───────────────────────────────────────────────────────────


class PMGNodeCpuSensor(PMGBaseSensor):
    """Sensor reporting the CPU utilisation percentage of the PMG host node.

    State: float percentage (0–100), rounded to one decimal place.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:cpu-64-bit"
    _attr_translation_key = "node_cpu"

    def __init__(self, coordinator: PMGDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_node_cpu"

    @property
    def native_value(self) -> float:
        """Return CPU usage as a percentage, or 0.0 if data is unavailable."""
        if not self.coordinator.data or self.coordinator.data.node is None:
            return 0.0
        # PMG reports cpu as a fraction 0.0-1.0; convert to percentage.
        return round(self.coordinator.data.node.cpu_usage * 100, 1)

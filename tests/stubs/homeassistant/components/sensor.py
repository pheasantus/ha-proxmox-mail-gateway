"""Stub for homeassistant.components.sensor."""

from __future__ import annotations

from enum import Enum
from typing import Any


class SensorStateClass(str, Enum):
    """Sensor state class."""

    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class SensorEntity:
    """Minimal SensorEntity stub."""

    _attr_icon: str | None = None
    _attr_unique_id: str | None = None
    _attr_state_class: SensorStateClass | None = None
    _attr_native_unit_of_measurement: str | None = None
    _attr_translation_key: str | None = None
    _attr_has_entity_name: bool = False
    _attr_device_info: dict[str, Any] | None = None

    @property
    def state(self) -> Any:
        return self.native_value

    @property
    def native_value(self) -> Any:
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {}

    @property
    def icon(self) -> str | None:
        return self._attr_icon

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def device_info(self) -> dict[str, Any]:
        return self._attr_device_info or {}

    def async_write_ha_state(self) -> None:
        """No-op in stubs."""

    async def async_added_to_hass(self) -> None:
        """No-op in stubs."""

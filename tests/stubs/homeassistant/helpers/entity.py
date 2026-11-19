"""Stub for homeassistant.helpers.entity."""

from __future__ import annotations


class Entity:
    """Minimal Entity stub."""

    _attr_unique_id: str | None = None
    _attr_name: str | None = None
    _attr_icon: str | None = None

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def name(self) -> str | None:
        return self._attr_name

    def async_write_ha_state(self) -> None:
        pass

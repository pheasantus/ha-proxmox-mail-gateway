"""Stub for homeassistant.components.select."""

from __future__ import annotations

from typing import Any


class SelectEntity:
    """Minimal SelectEntity stub."""

    _attr_icon: str | None = None
    _attr_unique_id: str | None = None
    _attr_translation_key: str | None = None
    _attr_has_entity_name: bool = False
    _attr_device_info: dict[str, Any] | None = None
    _attr_options: list[str] | None = None
    _attr_current_option: str | None = None

    def __init__(self) -> None:
        if self._attr_options is None:
            self._attr_options = []

    @property
    def options(self) -> list[str]:
        return self._attr_options or []

    @property
    def current_option(self) -> str | None:
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        self.select_option(option)

    def select_option(self, option: str) -> None:
        self._attr_current_option = option

"""Stub for homeassistant.components.button."""

from __future__ import annotations

from typing import Any


class ButtonEntity:
    """Minimal ButtonEntity stub."""

    _attr_icon: str | None = None
    _attr_unique_id: str | None = None
    _attr_translation_key: str | None = None
    _attr_has_entity_name: bool = False
    _attr_device_info: dict[str, Any] | None = None

    async def async_press(self) -> None:
        """Handle button press."""
        self.press()

    def press(self) -> None:
        """Handle button press."""

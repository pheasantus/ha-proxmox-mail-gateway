"""Stub for homeassistant.config_entries."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .data_entry_flow import FlowResult


class ConfigEntry:
    """Minimal ConfigEntry stub."""

    def __init__(
        self,
        entry_id: str | None = None,
        domain: str = "",
        title: str = "",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        unique_id: str | None = None,
    ) -> None:
        self.entry_id = entry_id or str(uuid.uuid4())
        self.domain = domain
        self.title = title
        self.data = data or {}
        self.options = options or {}
        self.unique_id = unique_id
        self._on_unload: list[Callable] = []
        self._listeners: list[Callable] = []

    def add_update_listener(self, listener: Callable) -> Callable:
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    def async_on_unload(self, func: Callable) -> None:
        self._on_unload.append(func)


class ConfigFlow:
    """Minimal ConfigFlow stub."""

    VERSION: int = 1
    _flow_domain: str = ""

    def __init_subclass__(cls, domain: str = "", **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if domain:
            cls._flow_domain = domain

    def __init__(self) -> None:
        self.hass: Any = None
        self._unique_id: str | None = None
        self.context: dict[str, Any] = {}

    async def async_set_unique_id(self, unique_id: str) -> ConfigEntry | None:
        self._unique_id = unique_id
        return None

    def _abort_if_unique_id_configured(self, updates: dict[str, Any] | None = None) -> None:
        """No-op in stubs (HA deduplication not tested here)."""

    def async_create_entry(
        self,
        title: str,
        data: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> FlowResult:
        return {
            "type": "create_entry",
            "title": title,
            "data": data,
            "options": options or {},
        }

    def async_show_form(
        self,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> FlowResult:
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors or {},
            "data_schema": data_schema,
        }

    def async_abort(self, reason: str) -> FlowResult:
        return {"type": "abort", "reason": reason}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        raise NotImplementedError


class OptionsFlow:
    """Minimal OptionsFlow stub."""

    def __init__(self) -> None:
        self.hass: Any = None
        self.config_entry: ConfigEntry | None = None

    def async_create_entry(self, title: str = "", data: dict[str, Any] | None = None) -> FlowResult:
        return {"type": "create_entry", "title": title, "data": data or {}}

    def async_show_form(
        self,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors or {},
            "data_schema": data_schema,
        }

    def async_abort(self, reason: str) -> FlowResult:
        return {"type": "abort", "reason": reason}

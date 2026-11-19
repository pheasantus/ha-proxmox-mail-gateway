"""Stub for homeassistant.core."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def callback(func: Callable) -> Callable:
    """No-op marker for HA callback functions."""
    return func


class Event:
    """Stub for HA Event."""

    def __init__(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.event_type = event_type
        self.data = data or {}


class ServiceCall:
    """Stub for HA ServiceCall."""

    def __init__(self, domain: str, service: str, data: dict[str, Any] | None = None) -> None:
        self.domain = domain
        self.service = service
        self.data = data or {}


class _EventBus:
    """Minimal event bus stub."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}
        self.fired_events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any] | None = None) -> None:
        self.fired_events.append((event_type, event_data or {}))
        for listener in self._listeners.get(event_type, []):
            listener(Event(event_type, event_data))

    def async_listen(self, event_type: str, listener: Callable) -> Callable:
        self._listeners.setdefault(event_type, []).append(listener)

        def remove() -> None:
            self._listeners[event_type].remove(listener)

        return remove


class _ServiceRegistry:
    """Minimal service registry stub."""

    def __init__(self) -> None:
        self._services: dict[tuple[str, str], Callable] = {}

    def async_register(
        self,
        domain: str,
        service: str,
        handler: Callable,
        schema: Any = None,
    ) -> None:
        self._services[(domain, service)] = handler

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._services

    async def async_call(self, domain: str, service: str, data: dict | None = None) -> None:
        handler = self._services.get((domain, service))
        if handler is None:
            raise KeyError(f"Service {domain}.{service} not found")
        await handler(ServiceCall(domain, service, data))


class HomeAssistant:
    """Minimal HomeAssistant stub."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.bus = _EventBus()
        self.services = _ServiceRegistry()
        self.config_entries = MagicMock()
        self.config_entries.async_forward_entry_setups = AsyncMock(return_value=None)
        self.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        self.config_entries.async_reload = AsyncMock(return_value=None)
        self.loop = MagicMock()

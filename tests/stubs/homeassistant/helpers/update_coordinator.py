"""Stub for homeassistant.helpers.update_coordinator."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any, Generic, TypeVar

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

T = TypeVar("T")


class UpdateFailed(Exception):
    """Data update failed."""


class CoordinatorEntity(Generic[T]):
    """Base class for coordinator-backed entities."""

    def __init__(self, coordinator: T) -> None:
        self.coordinator = coordinator
        self._listeners: list[Callable] = []

    @property
    def available(self) -> bool:
        return getattr(self.coordinator, "last_update_success", True)

    def async_write_ha_state(self) -> None:
        """No-op in stubs."""

    async def async_added_to_hass(self) -> None:
        """No-op in stubs."""

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class DataUpdateCoordinator(Generic[T]):
    """Base class for DataUpdateCoordinator."""

    def __init__(
        self,
        hass: Any,
        logger: Any,
        name: str,
        update_interval: timedelta | None = None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: T | None = None
        self.last_update_success: bool = True
        self._listeners: list[Callable] = []

    async def async_config_entry_first_refresh(self) -> None:
        """Call _async_update_data() and raise ConfigEntryNotReady on failure."""
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except ConfigEntryAuthFailed:
            raise
        except UpdateFailed as exc:
            raise ConfigEntryNotReady(str(exc)) from exc

    async def async_request_refresh(self) -> None:
        """Refresh data and notify listeners."""
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except (UpdateFailed, Exception):
            self.last_update_success = False
        for listener in list(self._listeners):
            listener()

    def async_add_listener(self, listener: Callable, context: Any = None) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    async def _async_update_data(self) -> T:
        raise NotImplementedError

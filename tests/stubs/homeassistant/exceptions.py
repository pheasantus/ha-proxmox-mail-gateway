"""Stub for homeassistant.exceptions."""

from __future__ import annotations


class HomeAssistantError(Exception):
    """Base HA error."""


class ConfigEntryAuthFailed(HomeAssistantError):
    """Raised when a config entry cannot authenticate."""


class ConfigEntryNotReady(HomeAssistantError):
    """Raised when a config entry is not ready to be set up."""


class ServiceNotFound(HomeAssistantError):
    """Raised when a service is not found."""

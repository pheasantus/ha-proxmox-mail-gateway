"""Stub for homeassistant.helpers.aiohttp_client."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def async_get_clientsession(hass: Any, verify_ssl: bool = True) -> MagicMock:
    """Return a mock aiohttp.ClientSession for unit tests."""
    return MagicMock()

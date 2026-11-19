"""Stub for homeassistant.helpers.entity_platform."""

from __future__ import annotations

from collections.abc import Callable

# Type alias: a callable that takes a list of entities and optional update_before_add flag
AddEntitiesCallback = Callable[..., None]

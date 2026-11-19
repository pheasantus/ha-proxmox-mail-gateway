"""Stub for homeassistant.helpers.config_validation."""

from __future__ import annotations

import voluptuous as vol

# Mirrors the most commonly-used HA cv helpers
string = str
positive_int = vol.All(int, vol.Range(min=1))
port = vol.All(int, vol.Range(min=1, max=65535))
boolean = bool

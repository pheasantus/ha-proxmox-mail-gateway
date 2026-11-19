"""Stub for homeassistant.const."""

from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    """Available entity platforms."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"


# Standard config keys (mirrors real HA constants)
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VERIFY_SSL = "verify_ssl"
CONF_NAME = "name"

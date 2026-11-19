"""Constants for the Proxmox Mail Gateway integration."""

from __future__ import annotations

import re

# Integration identity
DOMAIN = "proxmox_mail_gateway"
MANUFACTURER = "Proxmox"
MODEL = "Mail Gateway"
VERSION = "1.0.0"

# ── Configuration keys ────────────────────────────────────────────────────────
# Standard HA config keys — values match homeassistant.const.CONF_* exactly.
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# Integration-specific config keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ENABLE_POSTFIX_SENSORS = "enable_postfix_sensors"
CONF_ENABLE_NODE_SENSORS = "enable_node_sensors"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PORT: int = 8006
# PMG ships with a self-signed certificate; warn user but default to False.
DEFAULT_VERIFY_SSL: bool = False
DEFAULT_SCAN_INTERVAL: int = 60  # seconds
DEFAULT_ENABLE_POSTFIX_SENSORS: bool = True
DEFAULT_ENABLE_NODE_SENSORS: bool = False

# ── PMG REST API paths (relative to /api2/json) ───────────────────────────────
API_PATH_PREFIX = "/api2/json"
API_LOGIN_PATH = "/access/ticket"
API_SPAM_STATUS_PATH = "/quarantine/spamstatus"
API_SPAM_USERS_PATH = "/quarantine/spamusers"
API_SPAM_LIST_PATH = "/quarantine/spam"
API_QUARANTINE_ACTION_PATH = "/quarantine/content"
API_NODE_STATUS_PATH = "/nodes/proxmox-mail-gateway/status"
API_POSTFIX_QUEUE_PATH = "/nodes/proxmox-mail-gateway/postfix/queue"

# ── Session / ticket lifecycle ────────────────────────────────────────────────
# PMG tickets are valid for 7200 s (2 h). We renew 200 s early for safety.
TICKET_VALIDITY_SECONDS: int = 7000
REQUEST_TIMEOUT_SECONDS: int = 30

# ── Input sanitization limits ─────────────────────────────────────────────────
MAX_SUBJECT_LENGTH: int = 500
MAX_SENDER_LENGTH: int = 500
MAX_MAIL_ID_LENGTH: int = 200

# mail_id may be a single token or a semicolon-separated list of tokens.
# Allowed characters: alphanumerics, underscores, hyphens, semicolons.
MAIL_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_;-]+$")

# ── HA Event Bus ──────────────────────────────────────────────────────────────
EVENT_MAIL_QUARANTINED = "pmg_mail_quarantined"

# ── Quarantine actions ────────────────────────────────────────────────────────
ACTION_DELIVER = "deliver"
ACTION_DELETE = "delete"
ACTION_WHITELIST = "whitelist"
ACTION_BLACKLIST = "blacklist"
VALID_ACTIONS: frozenset[str] = frozenset(
    {ACTION_DELIVER, ACTION_DELETE, ACTION_WHITELIST, ACTION_BLACKLIST}
)

# ── UI / Config flow steps ────────────────────────────────────────────────────
STEP_USER = "user"
STEP_REAUTH = "reauth_confirm"
STEP_OPTIONS = "init"

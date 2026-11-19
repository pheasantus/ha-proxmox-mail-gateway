"""Data models for the Proxmox Mail Gateway integration.

All models are plain dataclasses.  Every ``from_api_response`` factory
treats its input as untrusted data received from an external system and
applies sanitization before storing values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from .const import (
    MAX_MAIL_ID_LENGTH,
    MAX_SENDER_LENGTH,
    MAX_SUBJECT_LENGTH,
)

# ── Sanitization helpers ──────────────────────────────────────────────────────

#: Matches ASCII control characters (0x00-0x1F) and DEL (0x7F).
_CONTROL_CHARS_RE: re.Pattern[str] = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_string(value: object, max_length: int = 500) -> str:
    """Remove control characters and truncate a string from untrusted input.

    Args:
        value: Raw value received from the external API.
        max_length: Maximum number of characters to retain.

    Returns:
        A clean, length-bounded string safe for use in HA attributes and events.
    """
    text = str(value)
    cleaned = _CONTROL_CHARS_RE.sub("", text)
    return cleaned[:max_length]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce *value* to float; return *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce *value* to int; return *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── Domain models ─────────────────────────────────────────────────────────────


@dataclass
class QuarantineStatus:
    """Aggregate statistics for the PMG spam quarantine."""

    count: int
    avg_spam: float
    avg_bytes: float
    mbytes: float

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> QuarantineStatus:
        """Build a :class:`QuarantineStatus` from a raw PMG API dict.

        Args:
            data: The ``data`` object from ``GET /quarantine/spamstatus``.

        Returns:
            A validated :class:`QuarantineStatus` instance.
        """
        return cls(
            count=_safe_int(data.get("count", 0)),
            avg_spam=_safe_float(data.get("avgspam", 0.0)),
            avg_bytes=_safe_float(data.get("avgbytes", 0.0)),
            mbytes=_safe_float(data.get("mbytes", 0.0)),
        )


@dataclass
class QuarantineMessage:
    """A single mail message held in the PMG spam quarantine."""

    id: str
    pmail: str
    sender: str
    envelope_sender: str
    subject: str
    spam_score: float
    score_positive: float
    score_negative: float
    bytes: int
    timestamp: int
    seen: bool

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> QuarantineMessage:
        """Build a :class:`QuarantineMessage` from a raw PMG API dict.

        All string fields are sanitized to remove control characters and
        are truncated to their maximum safe lengths.

        Args:
            data: One element from the ``data`` list in
                ``GET /quarantine/spam``.

        Returns:
            A sanitized :class:`QuarantineMessage` instance.

        Raises:
            KeyError: If the mandatory ``id`` field is absent.
            ValueError: If a numeric field cannot be coerced.
        """
        # ``id`` is mandatory – callers should catch KeyError and skip the item.
        raw_id = str(data["id"])
        return cls(
            id=_sanitize_string(raw_id, MAX_MAIL_ID_LENGTH),
            pmail=_sanitize_string(data.get("pmail", ""), MAX_SENDER_LENGTH),
            sender=_sanitize_string(data.get("from", ""), MAX_SENDER_LENGTH),
            envelope_sender=_sanitize_string(data.get("envelope_sender", ""), MAX_SENDER_LENGTH),
            subject=_sanitize_string(data.get("subject", ""), MAX_SUBJECT_LENGTH),
            spam_score=_safe_float(data.get("spamlevel", 0.0)),
            score_positive=_safe_float(data.get("score-positive", 0.0)),
            score_negative=_safe_float(data.get("score-negative", 0.0)),
            bytes=_safe_int(data.get("bytes", 0)),
            timestamp=_safe_int(data.get("time", 0)),
            seen=bool(data.get("seen", False)),
        )

    def as_ha_attribute(self) -> dict[str, Any]:
        """Return a serializable dict suitable for HA ``extra_state_attributes``.

        Returns:
            Dictionary with only JSON-safe, sanitized fields.
        """
        from datetime import datetime  # noqa: PLC0415

        try:
            dt = datetime.fromtimestamp(self.timestamp, tz=UTC)
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            date_str = ""

        return {
            "id": self.id,
            "sender": self.sender,
            "envelope_sender": self.envelope_sender,
            "recipient": self.pmail,
            "subject": self.subject,
            "spam_score": self.spam_score,
            "bytes": self.bytes,
            "timestamp": self.timestamp,
            "date_str": date_str,
            "seen": self.seen,
        }


@dataclass
class PostfixQueues:
    """Current Postfix mail queue depths on the PMG node."""

    active: int
    deferred: int
    incoming: int

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> PostfixQueues:
        """Build a :class:`PostfixQueues` from ``GET …/postfix/queue`` data.

        Args:
            data: The ``data`` object from the PMG queue endpoint.

        Returns:
            A :class:`PostfixQueues` instance (fields default to 0 if absent).
        """
        return cls(
            active=_safe_int(data.get("active", 0)),
            deferred=_safe_int(data.get("deferred", 0)),
            incoming=_safe_int(data.get("incoming", 0)),
        )


@dataclass
class NodeStatus:
    """Hardware and OS metrics for the PMG node."""

    uptime: int
    cpu_usage: float  # fraction 0.0–1.0
    mem_total: int  # bytes
    mem_used: int  # bytes

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> NodeStatus:
        """Build a :class:`NodeStatus` from ``GET …/nodes/…/status`` data.

        Args:
            data: The ``data`` object from the PMG node status endpoint.

        Returns:
            A :class:`NodeStatus` instance.
        """
        memory: dict[str, Any] = data.get("memory", {}) or {}
        return cls(
            uptime=_safe_int(data.get("uptime", 0)),
            cpu_usage=_safe_float(data.get("cpu", 0.0)),
            mem_total=_safe_int(memory.get("total", 0)),
            mem_used=_safe_int(memory.get("used", 0)),
        )


@dataclass
class PMGData:
    """Aggregated snapshot of all PMG data fetched in one coordinator cycle."""

    status: QuarantineStatus
    messages: list[QuarantineMessage] = field(default_factory=list)
    queues: PostfixQueues | None = None
    node: NodeStatus | None = None

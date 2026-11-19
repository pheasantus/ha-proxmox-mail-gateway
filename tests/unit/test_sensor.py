"""Unit tests for PMG sensor entities (sensor.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.proxmox_mail_gateway.models import (
    NodeStatus,
    PMGData,
    PostfixQueues,
    QuarantineMessage,
    QuarantineStatus,
)
from custom_components.proxmox_mail_gateway.sensor import (
    PMGAvgSpamSensor,
    PMGNodeCpuSensor,
    PMGPostfixQueueSensor,
    PMGQuarantineCountSensor,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_entry(entry_id: str = "eid001") -> ConfigEntry:
    return ConfigEntry(
        entry_id=entry_id,
        domain="proxmox_mail_gateway",
        title="PMG test",
        data={"host": "192.168.222.30", "port": 8006},
        options={},
    )


def _make_message(msg_id: str, pmail: str = "user@example.com") -> QuarantineMessage:
    return QuarantineMessage(
        id=msg_id,
        pmail=pmail,
        sender="spammer@example.com",
        envelope_sender="bounce@example.com",
        subject="SPAM",
        spam_score=5.0,
        score_positive=5.0,
        score_negative=0.0,
        bytes=1024,
        timestamp=1700000000,
        seen=False,
    )


def _make_data(
    count: int = 2,
    avg_spam: float = 4.5,
    messages: list[QuarantineMessage] | None = None,
    queues: PostfixQueues | None = None,
    node: NodeStatus | None = None,
) -> PMGData:
    return PMGData(
        status=QuarantineStatus(count=count, avg_spam=avg_spam, avg_bytes=50_000.0, mbytes=0.1),
        messages=messages or [],
        queues=queues,
        node=node,
    )


def _make_coordinator(data: PMGData | None = None) -> MagicMock:
    """Return a minimal mock coordinator."""
    coord = MagicMock()
    coord.data = data or _make_data()
    coord.last_update_success = True
    return coord


# ══════════════════════════════════════════════════════════════════════════════
# PMGQuarantineCountSensor
# ══════════════════════════════════════════════════════════════════════════════


class TestQuarantineCountSensor:
    """Tests for PMGQuarantineCountSensor."""

    def test_native_value_returns_count(self) -> None:
        """native_value reflects status.count from coordinator data."""
        entry = _make_entry()
        coord = _make_coordinator(_make_data(count=5))
        sensor = PMGQuarantineCountSensor(coord, entry)
        assert sensor.native_value == 5

    def test_native_value_zero_when_no_data(self) -> None:
        """native_value is 0 when coordinator.data is None."""
        entry = _make_entry()
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGQuarantineCountSensor(coord, entry)
        assert sensor.native_value == 0

    def test_icon_filled_when_count_positive(self) -> None:
        """'mdi:email-alert' icon is returned when count > 0."""
        sensor = PMGQuarantineCountSensor(_make_coordinator(_make_data(count=3)), _make_entry())
        assert sensor.icon == "mdi:email-alert"

    def test_icon_outline_when_count_zero(self) -> None:
        """'mdi:email-alert-outline' icon is returned when count == 0."""
        sensor = PMGQuarantineCountSensor(_make_coordinator(_make_data(count=0)), _make_entry())
        assert sensor.icon == "mdi:email-alert-outline"

    def test_icon_outline_when_no_data(self) -> None:
        """'mdi:email-alert-outline' icon when coordinator.data is None."""
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGQuarantineCountSensor(coord, _make_entry())
        assert sensor.icon == "mdi:email-alert-outline"

    def test_extra_attributes_messages_list(self) -> None:
        """extra_state_attributes includes serialized message list."""
        msgs = [_make_message("ID1", "alice@ex.com"), _make_message("ID2", "bob@ex.com")]
        coord = _make_coordinator(_make_data(count=2, messages=msgs))
        sensor = PMGQuarantineCountSensor(coord, _make_entry())

        attrs = sensor.extra_state_attributes
        assert "messages" in attrs
        assert len(attrs["messages"]) == 2
        ids = {m["id"] for m in attrs["messages"]}
        assert ids == {"ID1", "ID2"}

    def test_extra_attributes_recipients_sorted(self) -> None:
        """extra_state_attributes.recipients is a sorted list of unique addresses."""
        msgs = [
            _make_message("I1", "zara@ex.com"),
            _make_message("I2", "alice@ex.com"),
            _make_message("I3", "alice@ex.com"),  # duplicate
        ]
        coord = _make_coordinator(_make_data(messages=msgs))
        sensor = PMGQuarantineCountSensor(coord, _make_entry())

        attrs = sensor.extra_state_attributes
        assert attrs["recipients"] == sorted({"zara@ex.com", "alice@ex.com"})

    def test_extra_attributes_avg_spam_level(self) -> None:
        """extra_state_attributes.avg_spam_level matches rounded status.avg_spam."""
        coord = _make_coordinator(_make_data(avg_spam=6.789))
        sensor = PMGQuarantineCountSensor(coord, _make_entry())

        attrs = sensor.extra_state_attributes
        assert attrs["avg_spam_level"] == round(6.789, 2)

    def test_extra_attributes_empty_when_no_data(self) -> None:
        """extra_state_attributes is empty dict when coordinator.data is None."""
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGQuarantineCountSensor(coord, _make_entry())
        assert sensor.extra_state_attributes == {}

    def test_unique_id_contains_entry_id(self) -> None:
        """unique_id is deterministic and scoped to the config entry."""
        entry = _make_entry("my-entry-id")
        sensor = PMGQuarantineCountSensor(_make_coordinator(), entry)
        assert sensor._attr_unique_id is not None
        assert "my-entry-id" in sensor._attr_unique_id

    def test_state_class_is_measurement(self) -> None:
        """State class is MEASUREMENT."""
        from homeassistant.components.sensor import SensorStateClass

        sensor = PMGQuarantineCountSensor(_make_coordinator(), _make_entry())
        assert sensor._attr_state_class == SensorStateClass.MEASUREMENT

    def test_device_info_has_correct_host(self) -> None:
        """device_info includes the configured PMG host."""
        sensor = PMGQuarantineCountSensor(_make_coordinator(), _make_entry())
        info = sensor.device_info
        assert "192.168.222.30" in info["name"] or "192.168.222.30" in str(info)


# ══════════════════════════════════════════════════════════════════════════════
# PMGAvgSpamSensor
# ══════════════════════════════════════════════════════════════════════════════


class TestAvgSpamSensor:
    """Tests for PMGAvgSpamSensor."""

    def test_native_value_rounded(self) -> None:
        """native_value is rounded to 2 decimal places."""
        coord = _make_coordinator(_make_data(avg_spam=3.14159))
        sensor = PMGAvgSpamSensor(coord, _make_entry())
        assert sensor.native_value == 3.14

    def test_native_value_zero_when_no_data(self) -> None:
        """native_value is 0.0 when coordinator.data is None."""
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGAvgSpamSensor(coord, _make_entry())
        assert sensor.native_value == 0.0

    def test_unique_id_distinct_from_count_sensor(self) -> None:
        """unique_id differs from the quarantine count sensor's ID."""
        entry = _make_entry("eid999")
        count_sensor = PMGQuarantineCountSensor(_make_coordinator(), entry)
        avg_sensor = PMGAvgSpamSensor(_make_coordinator(), entry)
        assert count_sensor._attr_unique_id != avg_sensor._attr_unique_id


# ══════════════════════════════════════════════════════════════════════════════
# PMGPostfixQueueSensor
# ══════════════════════════════════════════════════════════════════════════════


class TestPostfixQueueSensor:
    """Tests for PMGPostfixQueueSensor."""

    def test_native_value_active(self) -> None:
        """Active queue depth is returned correctly."""
        queues = PostfixQueues(active=7, deferred=2, incoming=1)
        coord = _make_coordinator(_make_data(queues=queues))
        sensor = PMGPostfixQueueSensor(coord, _make_entry(), "active")
        assert sensor.native_value == 7

    def test_native_value_deferred(self) -> None:
        """Deferred queue depth is returned correctly."""
        queues = PostfixQueues(active=0, deferred=3, incoming=0)
        coord = _make_coordinator(_make_data(queues=queues))
        sensor = PMGPostfixQueueSensor(coord, _make_entry(), "deferred")
        assert sensor.native_value == 3

    def test_native_value_zero_when_queues_none(self) -> None:
        """Returns 0 when queues data is None (optional sensors disabled)."""
        coord = _make_coordinator(_make_data(queues=None))
        sensor = PMGPostfixQueueSensor(coord, _make_entry(), "active")
        assert sensor.native_value == 0

    def test_native_value_zero_when_no_data(self) -> None:
        """Returns 0 when coordinator.data is None."""
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGPostfixQueueSensor(coord, _make_entry(), "active")
        assert sensor.native_value == 0

    def test_unique_id_per_queue_name(self) -> None:
        """Each queue type gets a distinct unique_id."""
        entry = _make_entry()
        s_active = PMGPostfixQueueSensor(_make_coordinator(), entry, "active")
        s_deferred = PMGPostfixQueueSensor(_make_coordinator(), entry, "deferred")
        assert s_active._attr_unique_id != s_deferred._attr_unique_id

    def test_translation_key_matches_queue_name(self) -> None:
        """translation_key contains the queue name."""
        entry = _make_entry()
        sensor = PMGPostfixQueueSensor(_make_coordinator(), entry, "incoming")
        assert sensor._attr_translation_key is not None
        assert "incoming" in sensor._attr_translation_key


# ══════════════════════════════════════════════════════════════════════════════
# PMGNodeCpuSensor
# ══════════════════════════════════════════════════════════════════════════════


class TestNodeCpuSensor:
    """Tests for PMGNodeCpuSensor."""

    def test_native_value_converts_fraction_to_percent(self) -> None:
        """cpu_usage fraction 0.0-1.0 is converted to 0-100%."""
        node = NodeStatus(
            uptime=3600, cpu_usage=0.42, mem_total=2_000_000_000, mem_used=1_000_000_000
        )
        coord = _make_coordinator(_make_data(node=node))
        sensor = PMGNodeCpuSensor(coord, _make_entry())
        assert sensor.native_value == pytest.approx(42.0, abs=0.1)

    def test_native_value_rounded_to_one_decimal(self) -> None:
        """native_value is rounded to 1 decimal place."""
        node = NodeStatus(uptime=0, cpu_usage=0.123456, mem_total=0, mem_used=0)
        coord = _make_coordinator(_make_data(node=node))
        sensor = PMGNodeCpuSensor(coord, _make_entry())
        assert sensor.native_value == 12.3

    def test_native_value_zero_when_node_none(self) -> None:
        """Returns 0.0 when node data is None."""
        coord = _make_coordinator(_make_data(node=None))
        sensor = PMGNodeCpuSensor(coord, _make_entry())
        assert sensor.native_value == 0.0

    def test_native_value_zero_when_no_data(self) -> None:
        """Returns 0.0 when coordinator.data is None."""
        coord = _make_coordinator()
        coord.data = None
        sensor = PMGNodeCpuSensor(coord, _make_entry())
        assert sensor.native_value == 0.0

    def test_unit_of_measurement_is_percent(self) -> None:
        """Unit of measurement is '%'."""
        sensor = PMGNodeCpuSensor(_make_coordinator(), _make_entry())
        assert sensor._attr_native_unit_of_measurement == "%"

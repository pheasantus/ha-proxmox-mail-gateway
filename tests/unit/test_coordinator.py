"""Unit tests for PMGDataUpdateCoordinator (coordinator.py)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.proxmox_mail_gateway.api import (
    PMGAuthError,
    PMGConnectionError,
    PMGServerError,
)
from custom_components.proxmox_mail_gateway.coordinator import PMGDataUpdateCoordinator
from custom_components.proxmox_mail_gateway.models import (
    NodeStatus,
    PMGData,
    PostfixQueues,
    QuarantineMessage,
    QuarantineStatus,
)

# ── Helpers / fixtures ────────────────────────────────────────────────────────

ENTRY_ID = "test-entry-123"


def _make_message(
    msg_id: str, pmail: str = "user@example.com", timestamp: int = 1700000000
) -> QuarantineMessage:
    """Return a minimal QuarantineMessage for tests."""
    return QuarantineMessage(
        id=msg_id,
        pmail=pmail,
        sender="spammer@example.com",
        envelope_sender="bounce@example.com",
        subject="SPAM test",
        spam_score=5.0,
        score_positive=5.0,
        score_negative=0.0,
        bytes=1000,
        timestamp=timestamp,
        seen=False,
    )


def _make_status(count: int = 1) -> QuarantineStatus:
    return QuarantineStatus(count=count, avg_spam=4.5, avg_bytes=50000.0, mbytes=0.1)


def _make_hass() -> HomeAssistant:
    """Return a minimal HomeAssistant stub."""
    return HomeAssistant()


def _make_entry(options: dict[str, Any] | None = None) -> ConfigEntry:
    return ConfigEntry(
        entry_id=ENTRY_ID,
        domain="proxmox_mail_gateway",
        title="PMG test",
        data={
            "host": "192.168.222.30",
            "port": 8006,
            "username": "homeassistant@pmg",
            "password": "secret",
        },
        options=options or {},
    )


def _make_api() -> MagicMock:
    """Return a fully mocked PMGApiClient."""
    api = MagicMock()
    api.get_spam_status = AsyncMock(return_value=_make_status())
    api.get_all_spam_messages = AsyncMock(return_value=[])
    api.get_postfix_queues = AsyncMock(return_value=PostfixQueues(active=2, deferred=0, incoming=1))
    api.get_node_status = AsyncMock(
        return_value=NodeStatus(
            uptime=3600, cpu_usage=0.15, mem_total=2_000_000_000, mem_used=800_000_000
        )
    )
    api.perform_action = AsyncMock(return_value=None)
    return api


def _make_coordinator(
    hass: HomeAssistant | None = None,
    api: MagicMock | None = None,
    options: dict[str, Any] | None = None,
) -> PMGDataUpdateCoordinator:
    return PMGDataUpdateCoordinator(
        hass=hass or _make_hass(),
        api=api or _make_api(),
        entry=_make_entry(options),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Coordinator initialisation
# ══════════════════════════════════════════════════════════════════════════════


class TestCoordinatorInit:
    """Tests for coordinator construction and default state."""

    def test_scan_interval_uses_option(self) -> None:
        """Custom scan_interval option is reflected in update_interval."""
        coord = _make_coordinator(options={"scan_interval": 120})
        assert coord.update_interval == timedelta(seconds=120)

    def test_scan_interval_uses_default(self) -> None:
        """Default scan interval (60 s) is used when option is absent."""
        coord = _make_coordinator()
        assert coord.update_interval == timedelta(seconds=60)

    def test_first_run_flag_starts_true(self) -> None:
        """_first_run is True before the first data fetch."""
        coord = _make_coordinator()
        assert coord._first_run is True

    def test_known_mail_ids_starts_empty(self) -> None:
        """_known_mail_ids is empty before the first data fetch."""
        coord = _make_coordinator()
        assert coord._known_mail_ids == set()


# ══════════════════════════════════════════════════════════════════════════════
# _async_update_data - success paths
# ══════════════════════════════════════════════════════════════════════════════


class TestAsyncUpdateDataSuccess:
    """Tests for successful data fetch scenarios."""

    async def test_returns_pmg_data_on_success(self) -> None:
        """Successful fetch returns a PMGData instance."""
        api = _make_api()
        api.get_spam_status = AsyncMock(return_value=_make_status(count=3))
        api.get_all_spam_messages = AsyncMock(return_value=[_make_message("MSG1")])

        coord = _make_coordinator(api=api)
        data = await coord._async_update_data()

        assert isinstance(data, PMGData)
        assert data.status.count == 3
        assert len(data.messages) == 1

    async def test_postfix_queues_fetched_when_enabled(self) -> None:
        """Postfix queues are fetched when option is enabled (default: True)."""
        api = _make_api()
        coord = _make_coordinator(api=api, options={"enable_postfix_sensors": True})

        data = await coord._async_update_data()

        api.get_postfix_queues.assert_called_once()
        assert data.queues is not None
        assert data.queues.active == 2

    async def test_postfix_queues_skipped_when_disabled(self) -> None:
        """Postfix queues are NOT fetched when option is disabled."""
        api = _make_api()
        coord = _make_coordinator(api=api, options={"enable_postfix_sensors": False})

        data = await coord._async_update_data()

        api.get_postfix_queues.assert_not_called()
        assert data.queues is None

    async def test_node_status_fetched_when_enabled(self) -> None:
        """Node status is fetched when option is enabled."""
        api = _make_api()
        coord = _make_coordinator(api=api, options={"enable_node_sensors": True})

        data = await coord._async_update_data()

        api.get_node_status.assert_called_once()
        assert data.node is not None
        assert abs(data.node.cpu_usage - 0.15) < 0.001

    async def test_node_status_skipped_when_disabled(self) -> None:
        """Node status is NOT fetched when option is disabled (default)."""
        api = _make_api()
        coord = _make_coordinator(api=api, options={"enable_node_sensors": False})

        data = await coord._async_update_data()

        api.get_node_status.assert_not_called()
        assert data.node is None

    async def test_postfix_error_does_not_fail_update(self) -> None:
        """A Postfix queue error is logged and ignored — update still succeeds."""
        api = _make_api()
        api.get_postfix_queues = AsyncMock(side_effect=PMGServerError("queue unavailable"))
        coord = _make_coordinator(api=api, options={"enable_postfix_sensors": True})

        data = await coord._async_update_data()

        assert data.queues is None
        assert data.status is not None  # rest of data is fine

    async def test_known_mail_ids_updated_after_fetch(self) -> None:
        """_known_mail_ids is updated with current message IDs after each fetch."""
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(
            return_value=[_make_message("MSGABC"), _make_message("MSGDEF")]
        )
        coord = _make_coordinator(api=api)

        await coord._async_update_data()

        assert coord._known_mail_ids == {"MSGABC", "MSGDEF"}

    async def test_first_run_flag_cleared_after_fetch(self) -> None:
        """_first_run is set to False after the first successful fetch."""
        coord = _make_coordinator()
        assert coord._first_run is True
        await coord._async_update_data()
        assert coord._first_run is False


# ══════════════════════════════════════════════════════════════════════════════
# _async_update_data - error paths
# ══════════════════════════════════════════════════════════════════════════════


class TestAsyncUpdateDataErrors:
    """Tests for error handling in _async_update_data."""

    async def test_auth_error_raises_config_entry_auth_failed(self) -> None:
        """PMGAuthError is translated to ConfigEntryAuthFailed."""
        api = _make_api()
        api.get_spam_status = AsyncMock(side_effect=PMGAuthError("bad creds"))
        coord = _make_coordinator(api=api)

        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_connection_error_raises_update_failed(self) -> None:
        """PMGConnectionError is translated to UpdateFailed."""
        api = _make_api()
        api.get_spam_status = AsyncMock(side_effect=PMGConnectionError("unreachable"))
        coord = _make_coordinator(api=api)

        with pytest.raises(UpdateFailed):
            await coord._async_update_data()

    async def test_server_error_raises_update_failed(self) -> None:
        """PMGServerError is translated to UpdateFailed."""
        api = _make_api()
        api.get_spam_status = AsyncMock(side_effect=PMGServerError("500"))
        coord = _make_coordinator(api=api)

        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


# ══════════════════════════════════════════════════════════════════════════════
# Event detection
# ══════════════════════════════════════════════════════════════════════════════


class TestEventDetection:
    """Tests for the pmg_mail_quarantined event firing logic."""

    async def test_no_events_on_first_run(self) -> None:
        """No events are fired on the very first data fetch."""
        hass = _make_hass()
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(return_value=[_make_message("NEW001")])
        coord = _make_coordinator(hass=hass, api=api)

        await coord._async_update_data()

        assert hass.bus.fired_events == []

    async def test_new_mail_fires_event_on_subsequent_run(self) -> None:
        """A new mail ID detected on the second run fires one event."""
        hass = _make_hass()
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(return_value=[])
        coord = _make_coordinator(hass=hass, api=api)

        # First run — no events, establishes baseline
        await coord._async_update_data()

        # Second run — new message appears
        api.get_all_spam_messages = AsyncMock(return_value=[_make_message("NEW001")])
        await coord._async_update_data()

        assert len(hass.bus.fired_events) == 1
        event_type, payload = hass.bus.fired_events[0]
        assert event_type == "pmg_mail_quarantined"
        assert payload["mail_id"] == "NEW001"

    async def test_known_mail_does_not_fire_event(self) -> None:
        """A message already seen on the previous run does not trigger an event."""
        hass = _make_hass()
        api = _make_api()
        msg = _make_message("KNOWN001")
        api.get_all_spam_messages = AsyncMock(return_value=[msg])
        coord = _make_coordinator(hass=hass, api=api)

        # Seed _known_mail_ids (simulates first run having already seen it)
        coord._first_run = False
        coord._known_mail_ids = {"KNOWN001"}

        await coord._async_update_data()

        assert hass.bus.fired_events == []

    async def test_event_payload_contains_sanitized_fields(self) -> None:
        """Event payload includes sender, recipient, subject, spam_score, time."""
        hass = _make_hass()
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(return_value=[])
        coord = _make_coordinator(hass=hass, api=api)
        await coord._async_update_data()  # establish baseline

        new_msg = _make_message("EVTTEST", pmail="user@example.com", timestamp=1700001234)
        api.get_all_spam_messages = AsyncMock(return_value=[new_msg])
        await coord._async_update_data()

        _, payload = hass.bus.fired_events[0]
        assert payload["mail_id"] == "EVTTEST"
        assert payload["recipient"] == "user@example.com"
        assert payload["spam_score"] == 5.0
        assert payload["time"] == 1700001234

    async def test_multiple_new_mails_fire_multiple_events(self) -> None:
        """Two new messages each produce one event."""
        hass = _make_hass()
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(return_value=[])
        coord = _make_coordinator(hass=hass, api=api)
        await coord._async_update_data()

        api.get_all_spam_messages = AsyncMock(
            return_value=[_make_message("MULTI1"), _make_message("MULTI2")]
        )
        await coord._async_update_data()

        assert len(hass.bus.fired_events) == 2
        ids = {ev[1]["mail_id"] for ev in hass.bus.fired_events}
        assert ids == {"MULTI1", "MULTI2"}

    async def test_removed_mail_clears_from_known_ids(self) -> None:
        """A message removed from PMG is no longer in _known_mail_ids."""
        api = _make_api()
        api.get_all_spam_messages = AsyncMock(return_value=[_make_message("GONE")])
        coord = _make_coordinator(api=api)
        await coord._async_update_data()
        assert "GONE" in coord._known_mail_ids

        # Next cycle: message is gone
        api.get_all_spam_messages = AsyncMock(return_value=[])
        await coord._async_update_data()
        assert "GONE" not in coord._known_mail_ids


# ══════════════════════════════════════════════════════════════════════════════
# async_perform_action
# ══════════════════════════════════════════════════════════════════════════════


class TestAsyncPerformAction:
    """Tests for the coordinator's quarantine action helper."""

    async def test_perform_action_calls_api(self) -> None:
        """async_perform_action delegates to api.perform_action."""
        api = _make_api()
        coord = _make_coordinator(api=api)
        # Pre-load data so async_request_refresh has something to update
        coord.data = PMGData(status=_make_status(), messages=[])

        await coord.async_perform_action("MSG001", "deliver")

        api.perform_action.assert_called_once_with("MSG001", "deliver")

    async def test_perform_action_triggers_refresh(self) -> None:
        """After perform_action, async_request_refresh is called once."""
        api = _make_api()
        coord = _make_coordinator(api=api)
        coord.data = PMGData(status=_make_status(), messages=[])

        refresh_called = False

        async def mock_refresh() -> None:
            nonlocal refresh_called
            refresh_called = True

        coord.async_request_refresh = mock_refresh  # type: ignore[method-assign]

        await coord.async_perform_action("MSG001", "delete")

        assert refresh_called is True

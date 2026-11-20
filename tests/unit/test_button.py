"""Unit tests for PMG button entities (button.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.proxmox_mail_gateway.button import (
    PMGDeleteAllButton,
    PMGDeleteSelectedButton,
    PMGDeliverAllButton,
    PMGDeliverSelectedButton,
    PMGRefreshButton,
    PMGWhitelistSelectedButton,
    async_setup_entry,
)
from custom_components.proxmox_mail_gateway.const import CONF_HOST, DOMAIN
from custom_components.proxmox_mail_gateway.coordinator import PMGDataUpdateCoordinator
from custom_components.proxmox_mail_gateway.models import (
    PMGData,
    QuarantineMessage,
    QuarantineStatus,
)


def _make_entry() -> ConfigEntry:
    return ConfigEntry(
        entry_id="entry-btn-test",
        domain=DOMAIN,
        title="PMG Test",
        data={CONF_HOST: "192.168.222.30"},
    )


def _make_msg(msg_id: str) -> QuarantineMessage:
    return QuarantineMessage(
        id=msg_id,
        pmail="user@example.com",
        sender="spam@example.com",
        envelope_sender="bounce@example.com",
        subject="Test spam",
        spam_score=5.0,
        score_positive=5.0,
        score_negative=0.0,
        bytes=1024,
        timestamp=1700000000,
        seen=False,
    )


def _make_coordinator(messages: list[QuarantineMessage] | None = None) -> MagicMock:
    coord = MagicMock(spec=PMGDataUpdateCoordinator)
    coord.data = PMGData(
        status=QuarantineStatus(
            count=len(messages or []), avg_spam=5.0, avg_bytes=100.0, mbytes=0.1
        ),
        messages=messages or [],
    )
    coord.selected_mail_id = messages[0].id if messages else None
    coord.async_perform_action = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


class TestButtonSetup:
    """Tests for async_setup_entry in button.py."""

    async def test_setup_adds_six_buttons(self, hass: HomeAssistant) -> None:
        entry = _make_entry()
        coord = _make_coordinator()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

        added: list[Any] = []
        await async_setup_entry(hass, entry, added.extend)
        assert len(added) == 6


class TestButtonPressActions:
    """Tests for async_press on individual buttons."""

    async def test_deliver_selected_button_calls_coordinator(self) -> None:
        msg1 = _make_msg("MSG001")
        coord = _make_coordinator([msg1])
        coord.selected_mail_id = "MSG001"
        btn = PMGDeliverSelectedButton(coord, _make_entry())

        await btn.async_press()
        coord.async_perform_action.assert_awaited_once_with("MSG001", "deliver")

    async def test_delete_selected_button_calls_coordinator(self) -> None:
        msg1 = _make_msg("MSG001")
        coord = _make_coordinator([msg1])
        coord.selected_mail_id = "MSG001"
        btn = PMGDeleteSelectedButton(coord, _make_entry())

        await btn.async_press()
        coord.async_perform_action.assert_awaited_once_with("MSG001", "delete")

    async def test_whitelist_selected_button_calls_coordinator(self) -> None:
        msg1 = _make_msg("MSG001")
        coord = _make_coordinator([msg1])
        coord.selected_mail_id = "MSG001"
        btn = PMGWhitelistSelectedButton(coord, _make_entry())

        await btn.async_press()
        coord.async_perform_action.assert_awaited_once_with("MSG001", "whitelist")

    async def test_deliver_all_button_joins_ids(self) -> None:
        msg1 = _make_msg("MSG001")
        msg2 = _make_msg("MSG002")
        coord = _make_coordinator([msg1, msg2])
        btn = PMGDeliverAllButton(coord, _make_entry())

        await btn.async_press()
        coord.async_perform_action.assert_awaited_once_with("MSG001;MSG002", "deliver")

    async def test_delete_all_button_joins_ids(self) -> None:
        msg1 = _make_msg("MSG001")
        msg2 = _make_msg("MSG002")
        coord = _make_coordinator([msg1, msg2])
        btn = PMGDeleteAllButton(coord, _make_entry())

        await btn.async_press()
        coord.async_perform_action.assert_awaited_once_with("MSG001;MSG002", "delete")

    async def test_refresh_button_calls_request_refresh(self) -> None:
        coord = _make_coordinator()
        btn = PMGRefreshButton(coord, _make_entry())

        await btn.async_press()
        coord.async_request_refresh.assert_awaited_once()

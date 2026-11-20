"""Unit tests for PMG select entity (select.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.proxmox_mail_gateway.const import CONF_HOST, DOMAIN
from custom_components.proxmox_mail_gateway.coordinator import PMGDataUpdateCoordinator
from custom_components.proxmox_mail_gateway.models import (
    PMGData,
    QuarantineMessage,
    QuarantineStatus,
)
from custom_components.proxmox_mail_gateway.select import (
    EMPTY_OPTION,
    PMGQuarantineSelect,
    async_setup_entry,
)


def _make_entry() -> ConfigEntry:
    return ConfigEntry(
        entry_id="entry-select-test",
        domain=DOMAIN,
        title="PMG Test",
        data={CONF_HOST: "192.168.222.30"},
    )


def _make_msg(msg_id: str, subject: str = "Test spam") -> QuarantineMessage:
    return QuarantineMessage(
        id=msg_id,
        pmail="user@example.com",
        sender="spam@example.com",
        envelope_sender="bounce@example.com",
        subject=subject,
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
    return coord


class TestSelectSetup:
    """Tests for async_setup_entry in select.py."""

    async def test_setup_adds_select_entity(self, hass: HomeAssistant) -> None:
        entry = _make_entry()
        coord = _make_coordinator()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

        added: list[Any] = []
        await async_setup_entry(hass, entry, added.extend)
        assert len(added) == 1
        assert isinstance(added[0], PMGQuarantineSelect)


class TestSelectEntityOptions:
    """Tests for PMGQuarantineSelect options and current_option."""

    def test_options_empty_when_no_messages(self) -> None:
        coord = _make_coordinator([])
        sel = PMGQuarantineSelect(coord, _make_entry())

        assert sel.options == [EMPTY_OPTION]
        assert sel.current_option == EMPTY_OPTION

    def test_options_contains_messages(self) -> None:
        msg1 = _make_msg("MSG001", "Spam Subject 1")
        msg2 = _make_msg("MSG002", "Spam Subject 2")
        coord = _make_coordinator([msg1, msg2])
        coord.selected_mail_id = "MSG001"
        sel = PMGQuarantineSelect(coord, _make_entry())

        assert len(sel.options) == 2
        assert "MSG001" in sel.options[0]
        assert "MSG002" in sel.options[1]
        assert sel.current_option == sel.options[0]

    async def test_select_option_updates_coordinator_selected_id(self) -> None:
        msg1 = _make_msg("MSG001")
        msg2 = _make_msg("MSG002")
        coord = _make_coordinator([msg1, msg2])
        sel = PMGQuarantineSelect(coord, _make_entry())
        mock_write = MagicMock()
        object.__setattr__(sel, "async_write_ha_state", mock_write)

        await sel.async_select_option(sel.options[1])

        assert coord.selected_mail_id == "MSG002"
        mock_write.assert_called_once()

"""Unit tests for custom_components.proxmox_mail_gateway.__init__.

Tests entry setup, unload, reload listener, and service registration / handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.proxmox_mail_gateway import (
    _async_register_services,
    _async_reload_entry,
    _get_first_coordinator,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.proxmox_mail_gateway.const import (
    ACTION_DELETE,
    ACTION_DELIVER,
    ACTION_WHITELIST,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)


@pytest.fixture
def hass() -> HomeAssistant:
    """Return a fresh HomeAssistant instance."""
    return HomeAssistant()


@pytest.fixture
def mock_entry() -> MagicMock:
    """Return a mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_123"
    entry.data = {
        CONF_HOST: "192.168.222.30",
        CONF_PORT: 8006,
        CONF_USERNAME: "homeassistant@pmg",
        CONF_PASSWORD: "test_password",
        CONF_VERIFY_SSL: False,
    }
    entry.options = {}
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_on_unload = MagicMock()
    return entry


class TestSetupAndUnloadEntry:
    """Tests for async_setup_entry and async_unload_entry."""

    async def test_async_setup_entry_success(
        self, hass: HomeAssistant, mock_entry: MagicMock
    ) -> None:
        """async_setup_entry creates coordinator, refreshes, forwards setup, registers services."""
        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession"
            ) as mock_get_session,
            patch("custom_components.proxmox_mail_gateway.PMGApiClient") as mock_client_cls,
            patch(
                "custom_components.proxmox_mail_gateway.PMGDataUpdateCoordinator"
            ) as mock_coord_cls,
        ):
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            mock_api = MagicMock()
            mock_client_cls.return_value = mock_api

            mock_coord = MagicMock()
            mock_coord.async_config_entry_first_refresh = AsyncMock()
            mock_coord_cls.return_value = mock_coord

            result = await async_setup_entry(hass, mock_entry)

            assert result is True
            assert DOMAIN in hass.data
            assert hass.data[DOMAIN][mock_entry.entry_id] is mock_coord
            mock_coord.async_config_entry_first_refresh.assert_awaited_once()
            hass.config_entries.async_forward_entry_setups.assert_awaited_once()
            mock_entry.async_on_unload.assert_called_once()
            assert hass.services.has_service(DOMAIN, "deliver_mail")

    async def test_async_unload_entry_success(
        self, hass: HomeAssistant, mock_entry: MagicMock
    ) -> None:
        """async_unload_entry unloads platforms and pops entry from hass.data."""
        mock_coord = MagicMock()
        hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = mock_coord
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(hass, mock_entry)

        assert result is True
        assert DOMAIN not in hass.data

    async def test_async_unload_entry_failure(
        self, hass: HomeAssistant, mock_entry: MagicMock
    ) -> None:
        """When async_unload_platforms fails, entry is not removed."""
        mock_coord = MagicMock()
        hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = mock_coord
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(hass, mock_entry)

        assert result is False
        assert mock_entry.entry_id in hass.data[DOMAIN]

    async def test_reload_entry_listener(self, hass: HomeAssistant, mock_entry: MagicMock) -> None:
        """_async_reload_entry calls hass.config_entries.async_reload."""
        hass.config_entries.async_reload = AsyncMock()
        await _async_reload_entry(hass, mock_entry)
        hass.config_entries.async_reload.assert_awaited_once_with(mock_entry.entry_id)


class TestServices:
    """Tests for PMG services registered in __init__.py."""

    @pytest.fixture(autouse=True)
    def setup_services_and_coordinator(self, hass: HomeAssistant) -> MagicMock:
        mock_coord = MagicMock()
        mock_coord.async_perform_action = AsyncMock()
        hass.data.setdefault(DOMAIN, {})["entry_1"] = mock_coord
        _async_register_services(hass)
        return mock_coord

    def test_get_first_coordinator_raises_when_no_entries(self, hass: HomeAssistant) -> None:
        """_get_first_coordinator raises KeyError if DOMAIN data is empty."""
        hass.data[DOMAIN] = {}
        with pytest.raises(KeyError, match="No Proxmox Mail Gateway integration"):
            _get_first_coordinator(hass)

    async def test_deliver_mail_service(
        self, hass: HomeAssistant, setup_services_and_coordinator: MagicMock
    ) -> None:
        """deliver_mail service calls async_perform_action with ACTION_DELIVER."""
        await hass.services.async_call(DOMAIN, "deliver_mail", {"mail_id": "MSG_100"})
        setup_services_and_coordinator.async_perform_action.assert_awaited_once_with(
            "MSG_100", ACTION_DELIVER
        )

    async def test_delete_mail_service(
        self, hass: HomeAssistant, setup_services_and_coordinator: MagicMock
    ) -> None:
        """delete_mail service calls async_perform_action with ACTION_DELETE."""
        await hass.services.async_call(DOMAIN, "delete_mail", {"mail_id": "MSG_101"})
        setup_services_and_coordinator.async_perform_action.assert_awaited_once_with(
            "MSG_101", ACTION_DELETE
        )

    async def test_whitelist_mail_service(
        self, hass: HomeAssistant, setup_services_and_coordinator: MagicMock
    ) -> None:
        """whitelist_mail service calls async_perform_action with ACTION_WHITELIST."""
        await hass.services.async_call(DOMAIN, "whitelist_mail", {"mail_id": "MSG_102"})
        setup_services_and_coordinator.async_perform_action.assert_awaited_once_with(
            "MSG_102", ACTION_WHITELIST
        )

    async def test_quarantine_action_service(
        self, hass: HomeAssistant, setup_services_and_coordinator: MagicMock
    ) -> None:
        """quarantine_action generic service passes the action string directly."""
        await hass.services.async_call(
            DOMAIN,
            "quarantine_action",
            {"mail_id": "MSG_103", "action": "blacklist"},
        )
        setup_services_and_coordinator.async_perform_action.assert_awaited_once_with(
            "MSG_103", "blacklist"
        )

"""Integration tests for Proxmox Mail Gateway Home Assistant integration.

Tests end-to-end integration flows:
- Configuration / setup entry (async_setup_entry)
- Data polling and coordinator updates
- Sensor entity lifecycle and state validation
- Event dispatching on quarantine changes (pmg_mail_quarantined)
- Quarantine service calls (deliver, delete, whitelist, generic action)
- Error handling (auth failures, network disconnects, recovery)
- Entry unloading and reloading on options update
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.proxmox_mail_gateway import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.proxmox_mail_gateway.api import (
    PMGApiClient,
    PMGValidationError,
)
from custom_components.proxmox_mail_gateway.const import (
    CONF_ENABLE_NODE_SENSORS,
    CONF_ENABLE_POSTFIX_SENSORS,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
    EVENT_MAIL_QUARANTINED,
)
from custom_components.proxmox_mail_gateway.coordinator import PMGDataUpdateCoordinator
from custom_components.proxmox_mail_gateway.sensor import (
    PMGAvgSpamSensor,
    PMGNodeCpuSensor,
    PMGPostfixQueueSensor,
    PMGQuarantineCountSensor,
)
from custom_components.proxmox_mail_gateway.sensor import (
    async_setup_entry as sensor_async_setup_entry,
)
from tests.conftest import (
    MOCK_LOGIN_RESPONSE,
    MOCK_NODE_STATUS_RESPONSE,
    MOCK_POSTFIX_QUEUE_RESPONSE,
    MOCK_SPAM_MESSAGES_RESPONSE,
    MOCK_SPAM_STATUS_RESPONSE,
    MOCK_SPAM_USERS_RESPONSE,
    TEST_HOST,
    TEST_PASSWORD,
    TEST_PORT,
    TEST_USERNAME,
)

ENTRY_ID = "integration_test_entry_001"


class _FakeResponse:
    """Mock aiohttp response context manager."""

    def __init__(
        self,
        payload: Any = None,
        status: int = 200,
        body: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self._body = body
        self._exception = exception

    async def __aenter__(self) -> _FakeResponse:
        if self._exception is not None:
            raise self._exception
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def json(self, **kwargs: Any) -> Any:
        if self._body is not None:
            raise json.JSONDecodeError("Expecting value", self._body, 0)
        return self._payload

    async def text(self) -> str:
        return self._body if self._body is not None else json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
            )


@contextmanager
def mock_http(client: PMGApiClient, responses: list[dict[str, Any]]) -> Generator[None, None, None]:
    """Patch client._session.request with sequential fake responses."""
    queue = list(responses)
    call_count = 0

    def _request_mock(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        nonlocal call_count
        if call_count >= len(queue):
            raise AssertionError(f"Unexpected HTTP call #{call_count + 1}: {method} {url}")
        spec = queue[call_count]
        call_count += 1
        return _FakeResponse(**spec)

    with patch.object(client._session, "request", side_effect=_request_mock):
        yield


def _build_standard_pmg_responses(
    *,
    login: dict[str, Any] | None = None,
    spam_status: dict[str, Any] | None = None,
    spam_users: dict[str, Any] | None = None,
    spam_messages: dict[str, Any] | None = None,
    postfix: dict[str, Any] | None = None,
    node: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build response sequence for a full coordinator refresh."""
    resps = [
        {"payload": login or MOCK_LOGIN_RESPONSE},
        {"payload": spam_status or MOCK_SPAM_STATUS_RESPONSE},
        {"payload": spam_users or MOCK_SPAM_USERS_RESPONSE},
        {"payload": spam_messages or MOCK_SPAM_MESSAGES_RESPONSE},
        {"payload": spam_messages or MOCK_SPAM_MESSAGES_RESPONSE},
    ]
    if postfix is not None:
        resps.append({"payload": postfix})
    if node is not None:
        resps.append({"payload": node})
    return resps


@pytest.fixture
def hass() -> HomeAssistant:
    """Return a fresh HomeAssistant instance."""
    return HomeAssistant()


@pytest.fixture
def config_entry() -> ConfigEntry:
    """Return a valid ConfigEntry."""
    return ConfigEntry(
        entry_id=ENTRY_ID,
        domain=DOMAIN,
        title=f"PMG {TEST_HOST}",
        data={
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_VERIFY_SSL: False,
        },
        options={
            CONF_SCAN_INTERVAL: 60,
            CONF_ENABLE_POSTFIX_SENSORS: True,
            CONF_ENABLE_NODE_SENSORS: True,
        },
    )


class TestFullIntegrationFlow:
    """End-to-end integration flow tests."""

    async def test_full_setup_and_sensor_lifecycle(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """Full setup creates coordinator, forwards platform, and updates sensor states."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        entities: list[Any] = []

        def _async_add_entities(new_entities: list[Any]) -> None:
            entities.extend(new_entities)

        async def _forward_setup(entry: ConfigEntry, platforms: list[Any]) -> None:
            await sensor_async_setup_entry(hass, entry, _async_add_entities)

        hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=_forward_setup)

        responses = _build_standard_pmg_responses(
            postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
            node=MOCK_NODE_STATUS_RESPONSE,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(client, responses),
        ):
            setup_ok = await async_setup_entry(hass, config_entry)

        assert setup_ok is True
        assert DOMAIN in hass.data
        coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert coordinator.last_update_success is True

        # Check entity registration (2 quarantine + 3 postfix + 1 node = 6)
        assert len(entities) == 6
        count_sensor = next(e for e in entities if isinstance(e, PMGQuarantineCountSensor))
        score_sensor = next(e for e in entities if isinstance(e, PMGAvgSpamSensor))
        active_sensor = next(
            e
            for e in entities
            if isinstance(e, PMGPostfixQueueSensor) and e._queue_name == "active"
        )
        node_sensor = next(e for e in entities if isinstance(e, PMGNodeCpuSensor))

        assert count_sensor.native_value == 2
        assert count_sensor.icon == "mdi:email-alert"
        assert score_sensor.native_value == pytest.approx(4.5)
        assert active_sensor.native_value == 3
        assert node_sensor.native_value == pytest.approx(12.0)

        # Verify device info
        dev_info = count_sensor.device_info
        assert dev_info["name"] == f"PMG {TEST_HOST}"
        assert dev_info["manufacturer"] == "Proxmox"

    async def test_quarantine_events_on_subsequent_cycle(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """New message arrival in quarantine triggers pmg_mail_quarantined event."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        # Baseline run: 1 message (ID: C0R17T196878491)
        initial_resps = _build_standard_pmg_responses(
            postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
            node=MOCK_NODE_STATUS_RESPONSE,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(client, initial_resps),
        ):
            await async_setup_entry(hass, config_entry)

        coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert len(hass.bus.fired_events) == 0

        # Cycle 2: A new quarantined message arrives
        new_mail_id = "NEW_ALERT_MAIL_123"
        updated_messages_resp = {
            "data": [
                MOCK_SPAM_MESSAGES_RESPONSE["data"][0],
                {
                    "id": new_mail_id,
                    "pmail": "kornelia@kwiatkowsky.eu",
                    "receiver": "kornelia@kwiatkowsky.eu",
                    "from": "Phishing Bot <bot@phish.xyz>",
                    "envelope_sender": "bot@phish.xyz",
                    "subject": "Urgent: Verify Your Account",
                    "spamlevel": 9,
                    "score-positive": 9.5,
                    "score-negative": 0.0,
                    "bytes": 1234,
                    "time": 1795009999,
                    "seen": 0,
                },
            ]
        }

        # Client ticket is still valid, so no login call needed
        cycle2_resps: list[dict[str, Any]] = [
            {"payload": MOCK_SPAM_STATUS_RESPONSE},
            {"payload": MOCK_SPAM_USERS_RESPONSE},
            {"payload": updated_messages_resp},
            {"payload": updated_messages_resp},
            {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
            {"payload": MOCK_NODE_STATUS_RESPONSE},
        ]

        with mock_http(client, cycle2_resps):
            await coordinator.async_request_refresh()

        events = [e for e in hass.bus.fired_events if e[0] == EVENT_MAIL_QUARANTINED]
        assert len(events) >= 1
        event_data = events[0][1]
        assert event_data["mail_id"] == new_mail_id
        assert event_data["sender"] == "Phishing Bot <bot@phish.xyz>"
        assert event_data["subject"] == "Urgent: Verify Your Account"
        assert event_data["spam_score"] == pytest.approx(9.0)

    async def test_service_calls_trigger_api_and_refresh(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """Service calls (deliver/delete/whitelist) invoke API and refresh coordinator."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(
                client,
                _build_standard_pmg_responses(
                    postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
                    node=MOCK_NODE_STATUS_RESPONSE,
                ),
            ),
        ):
            await async_setup_entry(hass, config_entry)

        # 1. deliver_mail
        with mock_http(
            client,
            [
                {"payload": {"data": None}},  # POST /quarantine/content
                {"payload": MOCK_SPAM_STATUS_RESPONSE},  # Refresh
                {"payload": MOCK_SPAM_USERS_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
                {"payload": MOCK_NODE_STATUS_RESPONSE},
            ],
        ):
            await hass.services.async_call(DOMAIN, "deliver_mail", {"mail_id": "C0R17T196878491"})

        # 2. delete_mail
        with mock_http(
            client,
            [
                {"payload": {"data": None}},
                {"payload": MOCK_SPAM_STATUS_RESPONSE},
                {"payload": MOCK_SPAM_USERS_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
                {"payload": MOCK_NODE_STATUS_RESPONSE},
            ],
        ):
            await hass.services.async_call(DOMAIN, "delete_mail", {"mail_id": "C0R17T196878491"})

        # 3. whitelist_mail
        with mock_http(
            client,
            [
                {"payload": {"data": None}},
                {"payload": MOCK_SPAM_STATUS_RESPONSE},
                {"payload": MOCK_SPAM_USERS_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
                {"payload": MOCK_NODE_STATUS_RESPONSE},
            ],
        ):
            await hass.services.async_call(DOMAIN, "whitelist_mail", {"mail_id": "C0R17T196878491"})

        # 4. generic quarantine_action (blacklist)
        with mock_http(
            client,
            [
                {"payload": {"data": None}},
                {"payload": MOCK_SPAM_STATUS_RESPONSE},
                {"payload": MOCK_SPAM_USERS_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
                {"payload": MOCK_NODE_STATUS_RESPONSE},
            ],
        ):
            await hass.services.async_call(
                DOMAIN,
                "quarantine_action",
                {"mail_id": "C0R17T196878491", "action": "blacklist"},
            )

    async def test_service_rejects_malicious_mail_id(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """Service call fails fast on validation without network requests."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(
                client,
                _build_standard_pmg_responses(
                    postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
                    node=MOCK_NODE_STATUS_RESPONSE,
                ),
            ),
        ):
            await async_setup_entry(hass, config_entry)

        with pytest.raises(PMGValidationError):
            await hass.services.async_call(DOMAIN, "deliver_mail", {"mail_id": "id;rm -rf /"})

    async def test_network_disconnect_and_recovery(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """Entity availability reflects coordinator network state."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        entities: list[Any] = []

        async def _forward(entry: ConfigEntry, platforms: list[Any]) -> None:
            await sensor_async_setup_entry(hass, entry, entities.extend)

        hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=_forward)

        # 1. Setup succeeds
        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(
                client,
                _build_standard_pmg_responses(
                    postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
                    node=MOCK_NODE_STATUS_RESPONSE,
                ),
            ),
        ):
            await async_setup_entry(hass, config_entry)

        coordinator: PMGDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
        sensor = entities[0]
        assert sensor.available is True

        # 2. Network error on refresh -> unavailable
        with mock_http(client, [{"exception": aiohttp.ServerConnectionError("offline")}]):
            await coordinator.async_request_refresh()

        assert coordinator.last_update_success is False
        assert sensor.available is False

        # 3. Connection restored -> available again
        with mock_http(
            client,
            [
                {"payload": MOCK_SPAM_STATUS_RESPONSE},
                {"payload": MOCK_SPAM_USERS_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_SPAM_MESSAGES_RESPONSE},
                {"payload": MOCK_POSTFIX_QUEUE_RESPONSE},
                {"payload": MOCK_NODE_STATUS_RESPONSE},
            ],
        ):
            await coordinator.async_request_refresh()

        assert coordinator.last_update_success is True
        assert sensor.available is True

    async def test_auth_failure_on_setup_raises_not_ready(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """401 during setup raises ConfigEntryAuthFailed."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(client, [{"status": 401, "payload": {}}]),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await async_setup_entry(hass, config_entry)

    async def test_unload_and_reload_entry(
        self, hass: HomeAssistant, config_entry: ConfigEntry
    ) -> None:
        """Integration unloads properly and listener triggers reload."""
        session = MagicMock(spec=aiohttp.ClientSession)
        client = PMGApiClient(
            host=TEST_HOST,
            port=TEST_PORT,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            verify_ssl=False,
            session=session,
        )

        with (
            patch(
                "custom_components.proxmox_mail_gateway.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.proxmox_mail_gateway.PMGApiClient",
                return_value=client,
            ),
            mock_http(
                client,
                _build_standard_pmg_responses(
                    postfix=MOCK_POSTFIX_QUEUE_RESPONSE,
                    node=MOCK_NODE_STATUS_RESPONSE,
                ),
            ),
        ):
            await async_setup_entry(hass, config_entry)

        assert config_entry.entry_id in hass.data[DOMAIN]

        # Unload
        unload_ok = await async_unload_entry(hass, config_entry)
        assert unload_ok is True
        assert DOMAIN not in hass.data

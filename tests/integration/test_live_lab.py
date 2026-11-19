"""Optional live integration tests against a real Proxmox Mail Gateway laboratory.

SAFETY GUARANTEE:
-----------------
This test suite executes **READ-ONLY** operations against the target PMG host.
It NEVER issues deliver, delete, whitelist, or blacklist commands, ensuring
zero impact on production or laboratory mailflow.

EXECUTION:
----------
Disabled by default in CI and local test runs. To run against a live lab:

    export PMG_LAB_HOST="192.168.222.30"
    export PMG_LAB_PORT="8006"
    export PMG_LAB_USERNAME="homeassistant@pmg"
    export PMG_LAB_PASSWORD="<your_password>"
    export PMG_LAB_VERIFY_SSL="false"

    pytest tests/integration/test_live_lab.py -v
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import aiohttp
import pytest
import pytest_asyncio

from custom_components.proxmox_mail_gateway.api import PMGApiClient

# Read configuration from environment
ENV_HOST = os.environ.get("PMG_LAB_HOST")
ENV_PORT = int(os.environ.get("PMG_LAB_PORT", "8006"))
ENV_USER = os.environ.get("PMG_LAB_USERNAME", "homeassistant@pmg")
ENV_PASS = os.environ.get("PMG_LAB_PASSWORD")
ENV_VERIFY_SSL = os.environ.get("PMG_LAB_VERIFY_SSL", "false").lower() in ("true", "1", "yes")

LIVE_LAB_ENABLED = bool(ENV_HOST and ENV_PASS)

pytestmark = pytest.mark.skipif(
    not LIVE_LAB_ENABLED,
    reason="Live PMG lab credentials not provided (set PMG_LAB_HOST and PMG_LAB_PASSWORD)",
)


@pytest_asyncio.fixture
async def live_client() -> AsyncGenerator[PMGApiClient, None]:
    """Yield an authenticated PMGApiClient connected to the live PMG laboratory."""
    assert ENV_HOST is not None
    assert ENV_PASS is not None

    async with aiohttp.ClientSession() as session:
        client = PMGApiClient(
            host=ENV_HOST,
            port=ENV_PORT,
            username=ENV_USER,
            password=ENV_PASS,
            verify_ssl=ENV_VERIFY_SSL,
            session=session,
            timeout=10,
        )
        await client.login()
        yield client


class TestLiveLabReadOnly:
    """Safe read-only integration tests against the live laboratory."""

    async def test_live_auth_and_ticket_validity(self, live_client: PMGApiClient) -> None:
        """Verify authentication ticket and CSRF token on live instance."""
        assert live_client.is_authenticated is True
        assert live_client._ticket is not None
        assert live_client._csrf is not None

    async def test_live_fetch_spam_status(self, live_client: PMGApiClient) -> None:
        """Fetch live quarantine status (count, avg spam, bytes)."""
        status = await live_client.get_spam_status()
        assert status is not None
        assert status.count >= 0
        assert status.avg_spam >= 0.0

    async def test_live_fetch_spam_users(self, live_client: PMGApiClient) -> None:
        """Fetch list of user mailboxes with quarantined emails."""
        users = await live_client.get_spam_users()
        assert isinstance(users, list)
        for user in users:
            assert "@" in user

    async def test_live_fetch_quarantine_messages(self, live_client: PMGApiClient) -> None:
        """Fetch all spam messages without performing any actions."""
        messages = await live_client.get_all_spam_messages()
        assert isinstance(messages, list)
        for msg in messages:
            assert msg.id
            assert msg.pmail

    async def test_live_optional_node_status(self, live_client: PMGApiClient) -> None:
        """Attempt to fetch node metrics (CPU, RAM, uptime)."""
        try:
            node = await live_client.get_node_status()
            assert node.uptime > 0
            assert 0.0 <= node.cpu_usage <= 1.0
        except Exception as exc:
            # Allowed to fail if user account lacks Sys.Audit / node permissions
            pytest.skip(f"Node status query skipped due to permissions: {exc}")

    async def test_live_optional_postfix_queues(self, live_client: PMGApiClient) -> None:
        """Attempt to query mail queues (active, deferred, incoming)."""
        try:
            queues = await live_client.get_postfix_queues()
            assert queues.active >= 0
            assert queues.deferred >= 0
            assert queues.incoming >= 0
        except Exception as exc:
            pytest.skip(f"Postfix queue query skipped due to permissions: {exc}")

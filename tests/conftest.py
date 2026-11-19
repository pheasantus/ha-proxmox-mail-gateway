"""Shared pytest fixtures for all test suites."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator

import aiohttp
import pytest_asyncio

from custom_components.proxmox_mail_gateway.api import PMGApiClient

# ── Test constants ─────────────────────────────────────────────────────────────
TEST_HOST = "192.168.222.30"
TEST_PORT = 8006
TEST_USERNAME = "homeassistant@pmg"
TEST_PASSWORD = "super_secret_password"
TEST_BASE_URL = f"https://{TEST_HOST}:{TEST_PORT}/api2/json"

MOCK_TICKET = "PMG:homeassistant@pmg:AABBCCDD1122=="
MOCK_CSRF = "AABBCCDD:XyZtEsTtOkEn"
MOCK_LOGIN_RESPONSE = {
    "data": {
        "ticket": MOCK_TICKET,
        "CSRFPreventionToken": MOCK_CSRF,
        "username": TEST_USERNAME,
    }
}

MOCK_SPAM_STATUS_RESPONSE = {
    "data": {
        "count": 2,
        "avgspam": "4.5",
        "avgbytes": "51142.5",
        "mbytes": 0.1015625,
    }
}

MOCK_SPAM_USERS_RESPONSE = {
    "data": [
        {"mail": "kornelia@kwiatkowsky.eu"},
        {"mail": "administrator@kwiatkowsky.eu"},
    ]
}

MOCK_SPAM_MESSAGES_RESPONSE = {
    "data": [
        {
            "id": "C0R17T196878491",
            "pmail": "kornelia@kwiatkowsky.eu",
            "receiver": "kornelia@kwiatkowsky.eu",
            "from": "Kurier Balonowy <patryk@example.com>",
            "envelope_sender": "bounce@example.com",
            "subject": "SPAM: Test Subject",
            "spamlevel": 5,
            "score-positive": 6.113,
            "score-negative": -0.301,
            "bytes": 51059,
            "time": 1795006116,
            "seen": 0,
        }
    ]
}

MOCK_NODE_STATUS_RESPONSE = {
    "data": {
        "uptime": 123456,
        "cpu": 0.12,
        "memory": {"total": 2097152000, "used": 1048576000},
    }
}

MOCK_POSTFIX_QUEUE_RESPONSE = {"data": {"active": 3, "deferred": 1, "incoming": 0}}


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Yield a real aiohttp.ClientSession for use with aioresponses."""
    async with aiohttp.ClientSession() as sess:
        yield sess


@pytest_asyncio.fixture
async def client(session: aiohttp.ClientSession) -> PMGApiClient:
    """Return an unauthenticated PMGApiClient backed by the shared session."""
    return PMGApiClient(
        host=TEST_HOST,
        port=TEST_PORT,
        username=TEST_USERNAME,
        password=TEST_PASSWORD,
        verify_ssl=False,
        session=session,
    )


@pytest_asyncio.fixture
async def authenticated_client(client: PMGApiClient) -> PMGApiClient:
    """Return a PMGApiClient with a pre-seeded (non-expired) ticket.

    Directly sets internal state to avoid a network call in every test
    that only needs an authenticated starting point.
    """
    client._ticket = MOCK_TICKET
    client._csrf = MOCK_CSRF
    client._ticket_expires_at = time.monotonic() + 7000
    return client

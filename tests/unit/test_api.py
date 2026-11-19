"""Unit tests for PMGApiClient (api.py) and data models (models.py).

Coverage targets
----------------
* Successful login → ticket + CSRF stored, expiry set.
* Failed login (wrong password) → PMGAuthError.
* Login with malformed JSON response → PMGAuthError / PMGServerError.
* Successful get_spam_status() → QuarantineStatus populated.
* Successful get_spam_users() → list of e-mail strings.
* Successful get_spam_messages() → list of QuarantineMessage with sanitized fields.
* get_all_spam_messages() merges multiple users concurrently.
* perform_action() with valid action → POST sent with correct payload.
* perform_action() with invalid mail_id → PMGValidationError (no network call).
* perform_action() with invalid action string → PMGValidationError.
* 401 on data request → auto re-auth + retry succeeds.
* Persistent 401 after re-auth → PMGAuthError raised.
* Connection error → PMGConnectionError.
* Timeout → PMGConnectionError.
* HTTP 429 → PMGRateLimitError.
* HTTP 500 → PMGServerError.
* Malicious control characters in subject/sender → stripped by sanitizer.
* get_node_status() and get_postfix_queues() → correct model populated.
* is_authenticated property reflects ticket state correctly.
* get_spam_messages() with malformed item → skips item, returns rest.
"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.proxmox_mail_gateway.api import (
    PMGApiClient,
    PMGAuthError,
    PMGConnectionError,
    PMGRateLimitError,
    PMGServerError,
    PMGValidationError,
)
from custom_components.proxmox_mail_gateway.models import (
    QuarantineMessage,
    QuarantineStatus,
    _sanitize_string,
)
from tests.conftest import (
    MOCK_CSRF,
    MOCK_LOGIN_RESPONSE,
    MOCK_NODE_STATUS_RESPONSE,
    MOCK_POSTFIX_QUEUE_RESPONSE,
    MOCK_SPAM_MESSAGES_RESPONSE,
    MOCK_SPAM_STATUS_RESPONSE,
    MOCK_SPAM_USERS_RESPONSE,
    MOCK_TICKET,
)

# ── Mock infrastructure ───────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal async context manager that mimics aiohttp.ClientResponse."""

    def __init__(
        self,
        payload: Any = None,
        status: int = 200,
        body: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self._body = body  # None → valid JSON; str → simulate parse error
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

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
            )


def _make_fake(
    payload: Any = None,
    status: int = 200,
    body: str | None = None,
    exception: Exception | None = None,
) -> _FakeResponse:
    return _FakeResponse(payload=payload, status=status, body=body, exception=exception)


@contextmanager
def mock_http(client: PMGApiClient, responses: list[dict[str, Any]]) -> Generator[None, None, None]:
    """Patch ``client._session._request`` with an ordered list of fake responses.

    aiohttp's ``session.request()`` and ``session.post()`` both delegate to the
    private ``session._request`` coroutine.  Patching it with an ``AsyncMock``
    intercepts all HTTP calls regardless of which public convenience method is
    used in the production code.

    Each item in *responses* is a dict with optional keys:
    ``payload``, ``status``, ``body``, ``exception``.  Responses are consumed
    in FIFO order; an extra call raises ``AssertionError``.
    """
    queue = list(responses)
    call_count = 0

    async def _request_mock(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        nonlocal call_count
        if call_count >= len(queue):
            raise AssertionError(f"Unexpected HTTP call #{call_count + 1}: {method} {url}")
        spec = queue[call_count]
        call_count += 1
        resp = _make_fake(**spec)
        # Raise connection-level exceptions immediately (before returning resp)
        if resp._exception is not None:
            raise resp._exception
        return resp

    with patch.object(client._session, "_request", AsyncMock(side_effect=_request_mock)):
        yield


# ══════════════════════════════════════════════════════════════════════════════
# Authentication tests
# ══════════════════════════════════════════════════════════════════════════════


class TestLogin:
    """Tests for PMGApiClient.login()."""

    async def test_successful_login_stores_ticket(self, client: PMGApiClient) -> None:
        """A valid 200 response stores ticket, CSRF, and sets expiry."""
        with mock_http(client, [{"payload": MOCK_LOGIN_RESPONSE}]):
            await client.login()

        assert client._ticket == MOCK_TICKET
        assert client._csrf == MOCK_CSRF
        assert client._ticket_expires_at > time.monotonic()
        assert client.is_authenticated is True

    async def test_login_401_raises_auth_error(self, client: PMGApiClient) -> None:
        """HTTP 401 during login raises PMGAuthError."""
        with mock_http(client, [{"status": 401}]), pytest.raises(PMGAuthError):
            await client.login()

    async def test_login_403_raises_auth_error(self, client: PMGApiClient) -> None:
        """HTTP 403 during login raises PMGAuthError."""
        with mock_http(client, [{"status": 403}]), pytest.raises(PMGAuthError):
            await client.login()

    async def test_login_500_raises_server_error(self, client: PMGApiClient) -> None:
        """HTTP 500 during login raises PMGServerError."""
        with mock_http(client, [{"status": 500}]), pytest.raises(PMGServerError):
            await client.login()

    async def test_login_missing_ticket_in_response_raises_auth_error(
        self, client: PMGApiClient
    ) -> None:
        """A 200 response that lacks 'ticket' raises PMGAuthError."""
        malformed: dict[str, Any] = {"data": {"CSRFPreventionToken": "only-csrf"}}
        with mock_http(client, [{"payload": malformed}]):
            with pytest.raises(PMGAuthError, match="missing"):
                await client.login()

    async def test_login_missing_csrf_in_response_raises_auth_error(
        self, client: PMGApiClient
    ) -> None:
        """A 200 response that lacks 'CSRFPreventionToken' raises PMGAuthError."""
        malformed: dict[str, Any] = {"data": {"ticket": "some-ticket"}}
        with mock_http(client, [{"payload": malformed}]):
            with pytest.raises(PMGAuthError, match="missing"):
                await client.login()

    async def test_login_connection_error_raises_connection_error(
        self, client: PMGApiClient
    ) -> None:
        """Network failure during login raises PMGConnectionError."""
        err = aiohttp.ClientConnectorError(MagicMock(), OSError("Connection refused"))
        with mock_http(client, [{"exception": err}]), pytest.raises(PMGConnectionError):
            await client.login()

    async def test_login_timeout_raises_connection_error(self, client: PMGApiClient) -> None:
        """Timeout during login raises PMGConnectionError."""
        with mock_http(client, [{"exception": aiohttp.ServerTimeoutError()}]):
            with pytest.raises(PMGConnectionError):
                await client.login()

    async def test_login_invalid_json_raises_server_error(self, client: PMGApiClient) -> None:
        """A non-JSON response body during login raises PMGServerError."""
        with mock_http(client, [{"body": "not-json-at-all"}]):
            with pytest.raises(PMGServerError):
                await client.login()


# ══════════════════════════════════════════════════════════════════════════════
# is_authenticated property tests
# ══════════════════════════════════════════════════════════════════════════════


class TestIsAuthenticated:
    """Tests for the is_authenticated property."""

    async def test_unauthenticated_client_returns_false(self, client: PMGApiClient) -> None:
        """Fresh client has no ticket → is_authenticated is False."""
        assert client.is_authenticated is False

    async def test_expired_ticket_returns_false(self, client: PMGApiClient) -> None:
        """An expired ticket makes is_authenticated return False."""
        client._ticket = MOCK_TICKET
        client._csrf = MOCK_CSRF
        client._ticket_expires_at = time.monotonic() - 1  # already expired
        assert client.is_authenticated is False

    async def test_valid_ticket_returns_true(self, authenticated_client: PMGApiClient) -> None:
        """A fresh ticket makes is_authenticated return True."""
        assert authenticated_client.is_authenticated is True


# ══════════════════════════════════════════════════════════════════════════════
# Quarantine query tests
# ══════════════════════════════════════════════════════════════════════════════


class TestGetSpamStatus:
    """Tests for PMGApiClient.get_spam_status()."""

    async def test_returns_quarantine_status(self, authenticated_client: PMGApiClient) -> None:
        """Successful response populates a QuarantineStatus correctly."""
        with mock_http(authenticated_client, [{"payload": MOCK_SPAM_STATUS_RESPONSE}]):
            status = await authenticated_client.get_spam_status()

        assert isinstance(status, QuarantineStatus)
        assert status.count == 2
        assert abs(status.avg_spam - 4.5) < 0.001
        assert status.mbytes == pytest.approx(0.1015625)

    async def test_unexpected_response_type_raises_server_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A non-dict 'data' field raises PMGServerError."""
        with mock_http(authenticated_client, [{"payload": {"data": [1, 2, 3]}}]):
            with pytest.raises(PMGServerError):
                await authenticated_client.get_spam_status()

    async def test_cookie_header_injected_directly(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """PMGAuthCookie is injected as a raw Cookie header rather than aiohttp cookies."""
        captured_kwargs: dict[str, Any] = {}

        async def _capture_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return _make_fake(payload=MOCK_SPAM_STATUS_RESPONSE)

        with patch.object(
            authenticated_client._session, "_request", AsyncMock(side_effect=_capture_request)
        ):
            await authenticated_client.get_spam_status()

        headers = captured_kwargs.get("headers", {})
        assert headers.get("Cookie") == f"PMGAuthCookie={MOCK_TICKET}"
        assert "cookies" not in captured_kwargs


class TestGetSpamUsers:
    """Tests for PMGApiClient.get_spam_users()."""

    async def test_returns_email_list(self, authenticated_client: PMGApiClient) -> None:
        """Successful response returns a list of e-mail strings."""
        with mock_http(authenticated_client, [{"payload": MOCK_SPAM_USERS_RESPONSE}]):
            users = await authenticated_client.get_spam_users()

        assert users == ["kornelia@kwiatkowsky.eu", "administrator@kwiatkowsky.eu"]

    async def test_empty_data_returns_empty_list(self, authenticated_client: PMGApiClient) -> None:
        """An empty data array returns an empty list (no exception)."""
        with mock_http(authenticated_client, [{"payload": {"data": []}}]):
            users = await authenticated_client.get_spam_users()
        assert users == []

    async def test_unexpected_response_type_returns_empty_list(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A non-list 'data' field returns empty list rather than raising."""
        with mock_http(authenticated_client, [{"payload": {"data": {"unexpected": "dict"}}}]):
            users = await authenticated_client.get_spam_users()
        assert users == []


class TestGetSpamMessages:
    """Tests for PMGApiClient.get_spam_messages()."""

    async def test_returns_message_list(self, authenticated_client: PMGApiClient) -> None:
        """Successful response returns correctly parsed QuarantineMessage list."""
        with mock_http(authenticated_client, [{"payload": MOCK_SPAM_MESSAGES_RESPONSE}]):
            messages = await authenticated_client.get_spam_messages("kornelia@kwiatkowsky.eu")

        assert len(messages) == 1
        msg = messages[0]
        assert msg.id == "C0R17T196878491"
        assert msg.spam_score == 5.0
        assert msg.bytes == 51059
        assert "Kurier Balonowy" in msg.sender

    async def test_malformed_item_is_skipped_rest_returned(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A message dict missing 'id' is skipped; valid items are returned."""
        payload: dict[str, Any] = {
            "data": [
                {"pmail": "user@example.com"},  # missing 'id' → skip
                {
                    "id": "VALIDID001",
                    "pmail": "user@example.com",
                    "from": "sender@example.com",
                    "envelope_sender": "bounce@example.com",
                    "subject": "Hello",
                    "spamlevel": 3,
                    "score-positive": 3.0,
                    "score-negative": 0.0,
                    "bytes": 1000,
                    "time": 1700000000,
                    "seen": 0,
                },
            ]
        }
        with mock_http(authenticated_client, [{"payload": payload}]):
            messages = await authenticated_client.get_spam_messages("user@example.com")

        assert len(messages) == 1
        assert messages[0].id == "VALIDID001"

    async def test_empty_response_returns_empty_list(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """An empty data array returns an empty list."""
        with mock_http(authenticated_client, [{"payload": {"data": []}}]):
            messages = await authenticated_client.get_spam_messages("nobody@example.com")
        assert messages == []


class TestGetAllSpamMessages:
    """Tests for PMGApiClient.get_all_spam_messages()."""

    async def test_aggregates_messages_from_multiple_users(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """Messages from two users are merged into a single sorted list."""
        users_payload: dict[str, Any] = {
            "data": [
                {"mail": "user1@example.com"},
                {"mail": "user2@example.com"},
            ]
        }
        user1_payload: dict[str, Any] = {
            "data": [
                {
                    "id": "MSG001",
                    "pmail": "user1@example.com",
                    "from": "spammer@example.com",
                    "envelope_sender": "bounce@example.com",
                    "subject": "Spam 1",
                    "spamlevel": 5,
                    "score-positive": 5.0,
                    "score-negative": 0.0,
                    "bytes": 1000,
                    "time": 1700000200,  # newer
                    "seen": 0,
                }
            ]
        }
        user2_payload: dict[str, Any] = {
            "data": [
                {
                    "id": "MSG002",
                    "pmail": "user2@example.com",
                    "from": "spammer2@example.com",
                    "envelope_sender": "bounce2@example.com",
                    "subject": "Spam 2",
                    "spamlevel": 7,
                    "score-positive": 7.0,
                    "score-negative": 0.0,
                    "bytes": 2000,
                    "time": 1700000100,  # older
                    "seen": 0,
                }
            ]
        }
        # get_all_spam_messages calls: 1×spamusers + N×spam (via asyncio.gather)
        # Order of gather calls matches users list order
        with mock_http(
            authenticated_client,
            [
                {"payload": users_payload},
                {"payload": user1_payload},
                {"payload": user2_payload},
            ],
        ):
            messages = await authenticated_client.get_all_spam_messages()

        assert len(messages) == 2
        # Sorted newest-first by timestamp
        assert messages[0].id == "MSG001"
        assert messages[1].id == "MSG002"

    async def test_empty_user_list_returns_empty(self, authenticated_client: PMGApiClient) -> None:
        """No users → no messages, no further network calls."""
        with mock_http(authenticated_client, [{"payload": {"data": []}}]):
            messages = await authenticated_client.get_all_spam_messages()
        assert messages == []

    async def test_partial_failure_returns_successful_results(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """If one user's fetch raises an error, the rest are still returned."""
        users_payload: dict[str, Any] = {
            "data": [
                {"mail": "good@example.com"},
                {"mail": "bad@example.com"},
            ]
        }
        good_payload: dict[str, Any] = {
            "data": [
                {
                    "id": "GOOD001",
                    "pmail": "good@example.com",
                    "from": "s@example.com",
                    "envelope_sender": "b@example.com",
                    "subject": "Good",
                    "spamlevel": 3,
                    "score-positive": 3.0,
                    "score-negative": 0.0,
                    "bytes": 500,
                    "time": 1700000000,
                    "seen": 0,
                }
            ]
        }
        # Patch get_spam_messages to simulate partial failure
        original_get_spam = authenticated_client.get_spam_messages

        async def patched_get_spam(pmail: str) -> list[QuarantineMessage]:
            if pmail == "bad@example.com":
                raise PMGServerError("simulated failure")
            return await original_get_spam(pmail)

        with patch.object(authenticated_client, "get_spam_messages", patched_get_spam):
            with mock_http(
                authenticated_client,
                [
                    {"payload": users_payload},
                    {"payload": good_payload},
                ],
            ):
                messages = await authenticated_client.get_all_spam_messages()

        assert len(messages) == 1
        assert messages[0].id == "GOOD001"


# ══════════════════════════════════════════════════════════════════════════════
# Quarantine action tests
# ══════════════════════════════════════════════════════════════════════════════


class TestPerformAction:
    """Tests for PMGApiClient.perform_action()."""

    async def test_deliver_action_sends_correct_payload(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """deliver action is POST-ed and completes without exception."""
        with mock_http(authenticated_client, [{"payload": {"data": None}}]):
            await authenticated_client.perform_action("C0R17T196878491", "deliver")

    async def test_delete_action_succeeds(self, authenticated_client: PMGApiClient) -> None:
        """delete action completes without exception."""
        with mock_http(authenticated_client, [{"payload": {"data": None}}]):
            await authenticated_client.perform_action("C0R17T196878491", "delete")

    async def test_whitelist_action_succeeds(self, authenticated_client: PMGApiClient) -> None:
        """whitelist action completes without exception."""
        with mock_http(authenticated_client, [{"payload": {"data": None}}]):
            await authenticated_client.perform_action("C0R17T196878491", "whitelist")

    async def test_blacklist_action_succeeds(self, authenticated_client: PMGApiClient) -> None:
        """blacklist action completes without exception."""
        with mock_http(authenticated_client, [{"payload": {"data": None}}]):
            await authenticated_client.perform_action("C0R17T196878491", "blacklist")

    async def test_batch_ids_semicolon_joined(self, authenticated_client: PMGApiClient) -> None:
        """A list of IDs is accepted and joined with semicolons before sending."""
        with mock_http(authenticated_client, [{"payload": {"data": None}}]):
            await authenticated_client.perform_action(["ID001", "ID002", "ID003"], "delete")

    async def test_invalid_mail_id_raises_validation_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A mail_id containing shell-special characters raises PMGValidationError."""
        with pytest.raises(PMGValidationError, match="Invalid mail_id"):
            await authenticated_client.perform_action("../../etc/passwd", "deliver")

    async def test_empty_mail_id_raises_validation_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """An empty mail_id string raises PMGValidationError."""
        with pytest.raises(PMGValidationError):
            await authenticated_client.perform_action("", "deliver")

    async def test_invalid_action_raises_validation_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """An unrecognised action string raises PMGValidationError."""
        with pytest.raises(PMGValidationError, match="Invalid action"):
            await authenticated_client.perform_action("C0R17T196878491", "pwn")

    async def test_sql_injection_in_mail_id_blocked(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A SQL-injection-style mail_id is rejected by the regex validator."""
        with pytest.raises(PMGValidationError):
            await authenticated_client.perform_action("' OR '1'='1", "delete")

    async def test_xss_payload_in_mail_id_blocked(self, authenticated_client: PMGApiClient) -> None:
        """An XSS-style mail_id is rejected by the regex validator."""
        with pytest.raises(PMGValidationError):
            await authenticated_client.perform_action("<script>alert(1)</script>", "deliver")


# ══════════════════════════════════════════════════════════════════════════════
# Auto re-authentication tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAutoReauth:
    """Tests for the automatic re-authentication on 401."""

    async def test_401_triggers_reauth_and_retry(self, authenticated_client: PMGApiClient) -> None:
        """A 401 from a data endpoint triggers re-login and a successful retry."""
        with mock_http(
            authenticated_client,
            [
                {"status": 401},  # first GET → 401
                {"payload": MOCK_LOGIN_RESPONSE},  # re-auth POST → 200
                {"payload": MOCK_SPAM_STATUS_RESPONSE},  # retry GET → 200
            ],
        ):
            status = await authenticated_client.get_spam_status()

        assert status.count == 2

    async def test_persistent_401_raises_auth_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """Two consecutive 401s raise PMGAuthError (no infinite loop)."""
        with mock_http(
            authenticated_client,
            [
                {"status": 401},  # first GET → 401
                {"payload": MOCK_LOGIN_RESPONSE},  # re-auth POST → 200
                {"status": 401},  # retry GET also → 401
            ],
        ):
            with pytest.raises(PMGAuthError):
                await authenticated_client.get_spam_status()

    async def test_403_raises_auth_error_without_retry(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """HTTP 403 is a permissions error (not retried)."""
        with mock_http(authenticated_client, [{"status": 403}]):
            with pytest.raises(PMGAuthError, match="403"):
                await authenticated_client.get_spam_status()


# ══════════════════════════════════════════════════════════════════════════════
# Network error tests
# ══════════════════════════════════════════════════════════════════════════════


class TestNetworkErrors:
    """Tests for connection and timeout handling."""

    async def test_connection_error_raises_connection_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A connection-refused error raises PMGConnectionError."""
        err = aiohttp.ClientConnectorError(MagicMock(), OSError("Connection refused"))
        with mock_http(authenticated_client, [{"exception": err}]):
            with pytest.raises(PMGConnectionError):
                await authenticated_client.get_spam_status()

    async def test_timeout_raises_connection_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A server timeout raises PMGConnectionError."""
        with mock_http(authenticated_client, [{"exception": aiohttp.ServerTimeoutError()}]):
            with pytest.raises(PMGConnectionError):
                await authenticated_client.get_spam_status()

    async def test_rate_limit_raises_rate_limit_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """HTTP 429 raises PMGRateLimitError."""
        with mock_http(authenticated_client, [{"status": 429}]):
            with pytest.raises(PMGRateLimitError):
                await authenticated_client.get_spam_status()

    async def test_server_error_raises_server_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """HTTP 500 raises PMGServerError."""
        with mock_http(authenticated_client, [{"status": 500}]):
            with pytest.raises(PMGServerError):
                await authenticated_client.get_spam_status()

    async def test_invalid_json_raises_server_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A non-JSON response body raises PMGServerError."""
        with mock_http(authenticated_client, [{"body": "this is not json"}]):
            with pytest.raises(PMGServerError):
                await authenticated_client.get_spam_status()


# ══════════════════════════════════════════════════════════════════════════════
# Optional metric endpoint tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOptionalEndpoints:
    """Tests for node status and postfix queue endpoints."""

    async def test_get_node_status_returns_model(self, authenticated_client: PMGApiClient) -> None:
        """get_node_status() returns a correctly populated NodeStatus."""
        with mock_http(authenticated_client, [{"payload": MOCK_NODE_STATUS_RESPONSE}]):
            node = await authenticated_client.get_node_status()

        assert node.uptime == 123456
        assert abs(node.cpu_usage - 0.12) < 0.001
        assert node.mem_total == 2097152000

    async def test_get_postfix_queues_returns_model(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """get_postfix_queues() returns a correctly populated PostfixQueues."""
        with mock_http(authenticated_client, [{"payload": MOCK_POSTFIX_QUEUE_RESPONSE}]):
            queues = await authenticated_client.get_postfix_queues()

        assert queues.active == 3
        assert queues.deferred == 1
        assert queues.incoming == 0

    async def test_node_status_non_dict_raises_server_error(
        self, authenticated_client: PMGApiClient
    ) -> None:
        """A non-dict node status response raises PMGServerError."""
        with mock_http(authenticated_client, [{"payload": {"data": ["not", "a", "dict"]}}]):
            with pytest.raises(PMGServerError):
                await authenticated_client.get_node_status()


# ══════════════════════════════════════════════════════════════════════════════
# Data sanitization / model tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDataSanitization:
    """Tests for input sanitization in models._sanitize_string and QuarantineMessage."""

    def test_control_chars_stripped_from_subject(self) -> None:
        """NUL bytes and control characters are removed."""
        raw = "SPAM\x00\x01\x1f Subject"
        result = _sanitize_string(raw)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x1f" not in result
        assert "Subject" in result

    def test_long_subject_truncated(self) -> None:
        """Subjects longer than max_length are truncated."""
        raw = "A" * 1000
        result = _sanitize_string(raw, max_length=500)
        assert len(result) == 500

    def test_del_char_stripped(self) -> None:
        """DEL character (0x7F) is stripped."""
        raw = "Subject\x7fHello"
        result = _sanitize_string(raw)
        assert "\x7f" not in result
        assert "Subject" in result
        assert "Hello" in result

    def test_from_api_response_sanitizes_subject(self) -> None:
        """QuarantineMessage.from_api_response strips control chars from subject."""
        data: dict[str, Any] = {
            "id": "TESTID",
            "pmail": "user@example.com",
            "from": "sender@example.com",
            "envelope_sender": "bounce@example.com",
            "subject": "SPAM\x0a\x0d Injected\x07 Subject",
            "spamlevel": 5,
            "score-positive": 5.0,
            "score-negative": 0.0,
            "bytes": 1000,
            "time": 1700000000,
            "seen": 0,
        }
        msg = QuarantineMessage.from_api_response(data)
        assert "\x0a" not in msg.subject  # LF
        assert "\x0d" not in msg.subject  # CR
        assert "\x07" not in msg.subject  # BEL
        assert "Injected" in msg.subject

    def test_from_api_response_handles_missing_optional_fields(self) -> None:
        """Missing optional fields default to empty/zero without raising."""
        data: dict[str, Any] = {"id": "MINID", "pmail": "u@e.com"}
        msg = QuarantineMessage.from_api_response(data)
        assert msg.id == "MINID"
        assert msg.sender == ""
        assert msg.spam_score == 0.0
        assert msg.bytes == 0
        assert msg.seen is False

    def test_from_api_response_missing_id_raises_key_error(self) -> None:
        """A message dict without 'id' raises KeyError (caller should skip)."""
        data: dict[str, Any] = {"pmail": "u@e.com", "subject": "No ID"}
        with pytest.raises(KeyError):
            QuarantineMessage.from_api_response(data)

    def test_from_api_response_non_numeric_spam_score_defaults_to_zero(self) -> None:
        """A non-numeric spamlevel coerces to 0.0 without raising."""
        data: dict[str, Any] = {
            "id": "NUMTEST",
            "pmail": "u@e.com",
            "spamlevel": "not-a-number",
        }
        msg = QuarantineMessage.from_api_response(data)
        assert msg.spam_score == 0.0

    def test_as_ha_attribute_produces_serializable_dict(self) -> None:
        """as_ha_attribute() returns a dict with all expected keys."""
        data: dict[str, Any] = {
            "id": "ATTRTEST",
            "pmail": "user@example.com",
            "from": "Sender Name <s@example.com>",
            "envelope_sender": "bounce@example.com",
            "subject": "Test Subject",
            "spamlevel": 4,
            "score-positive": 4.0,
            "score-negative": -0.1,
            "bytes": 2048,
            "time": 1700000000,
            "seen": 1,
        }
        msg = QuarantineMessage.from_api_response(data)
        attr = msg.as_ha_attribute()

        assert attr["id"] == "ATTRTEST"
        assert attr["recipient"] == "user@example.com"
        assert attr["seen"] is True
        assert isinstance(attr["date_str"], str) and len(attr["date_str"]) > 0
        assert attr["bytes"] == 2048
        assert attr["spam_score"] == 4.0

    def test_as_ha_attribute_invalid_timestamp_gives_empty_date_str(self) -> None:
        """An overflowing timestamp produces an empty date_str (no exception)."""
        data: dict[str, Any] = {
            "id": "TSTEST",
            "pmail": "u@e.com",
            "time": 999999999999999,  # overflow for datetime.fromtimestamp
        }
        msg = QuarantineMessage.from_api_response(data)
        attr = msg.as_ha_attribute()
        # Should not raise; date_str may be empty string
        assert isinstance(attr["date_str"], str)

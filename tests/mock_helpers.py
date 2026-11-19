"""Lightweight aiohttp mock helper compatible with aiohttp 3.11+.

aioresponses 0.7.9 is incompatible with aiohttp 3.11+ due to the
``stream_writer`` argument added to ``ClientResponse.__init__``.

This module provides a simple, dependency-free alternative that patches
``aiohttp.ClientSession._request`` directly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch


class MockResponse:
    """Minimal async context manager that mimics aiohttp.ClientResponse."""

    def __init__(
        self,
        payload: Any = None,
        status: int = 200,
        body: str | None = None,
        content_type: str = "application/json",
        exception: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self._body = body
        self._content_type = content_type
        self._exception = exception

    async def __aenter__(self) -> MockResponse:
        if self._exception is not None:
            raise self._exception
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def json(self, **kwargs: Any) -> Any:
        if self._body is not None:
            # Simulate JSON decode failure for non-JSON body
            raise json.JSONDecodeError("Expecting value", self._body, 0)
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
            )


# Lazy import to avoid circular issues at module load time
import aiohttp  # noqa: E402


class MockSession:
    """Context-manager based mock for aiohttp.ClientSession.

    Usage::

        responses = [
            {"method": "POST", "url": LOGIN_URL, "payload": MOCK_LOGIN_RESPONSE},
            {"method": "GET",  "url": STATUS_URL, "payload": MOCK_STATUS},
        ]
        with mock_session(client, responses):
            result = await client.get_spam_status()
    """

    def __init__(
        self,
        client: Any,
        responses: list[dict[str, Any]],
    ) -> None:
        self._client = client
        self._responses: list[dict[str, Any]] = list(responses)
        self._patcher: Any = None
        self._call_index = 0

    def _make_response(self, resp_def: dict[str, Any]) -> MockResponse:
        exc = resp_def.get("exception")
        if exc is not None:
            return MockResponse(exception=exc)
        return MockResponse(
            payload=resp_def.get("payload"),
            status=resp_def.get("status", 200),
            body=resp_def.get("body"),
            content_type=resp_def.get("content_type", "application/json"),
        )

    def _side_effect(self, method: str, url: str, **kwargs: Any) -> MockResponse:
        if self._call_index >= len(self._responses):
            raise AssertionError(f"Unexpected HTTP call #{self._call_index + 1}: {method} {url}")
        resp_def = self._responses[self._call_index]
        self._call_index += 1
        return self._make_response(resp_def)

    def __enter__(self) -> MockSession:
        mock_request = MagicMock(side_effect=self._side_effect)
        self._patcher = patch.object(self._client._session, "request", mock_request)
        self._patcher.start()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._patcher:
            self._patcher.stop()


def mock_responses(
    client: Any,
    responses: list[dict[str, Any]],
) -> MockSession:
    """Return a MockSession context manager for *client*.

    Args:
        client:    A :class:`~.api.PMGApiClient` instance.
        responses: Ordered list of response definitions.  Each entry is a
                   dict with keys: ``payload``, ``status``, ``body``,
                   ``exception``.  Responses are consumed in order.

    Example::

        with mock_responses(client, [
            {"payload": MOCK_LOGIN_RESPONSE},
            {"payload": MOCK_SPAM_STATUS_RESPONSE},
        ]):
            status = await client.get_spam_status()
    """
    return MockSession(client, responses)

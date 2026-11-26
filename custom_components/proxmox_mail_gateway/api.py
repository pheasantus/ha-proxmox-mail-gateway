"""Asynchronous HTTP client for the Proxmox Mail Gateway REST API.

Design principles
-----------------
* **Zero secrets in logs** – ticket, CSRF token, and password are never
  written to any log handler at any level.
* **Explicit timeouts** – every network call uses ``aiohttp.ClientTimeout``.
* **Auto re-authentication** – a single ``401`` response triggers one
  transparent re-login; a second consecutive ``401`` raises
  :class:`PMGAuthError` and lets the HA ``ConfigEntryAuthFailed`` handler
  ask the user to re-enter credentials.
* **Input validation** – ``mail_id`` strings are validated against
  :data:`.const.MAIL_ID_RE` before being sent to PMG.
* **Data sanitization** – all string fields returned by PMG are cleaned of
  control characters via :class:`.models.QuarantineMessage.from_api_response`.
* **Concurrency** – :meth:`PMGApiClient.get_all_spam_messages` gathers
  per-user queries concurrently with :func:`asyncio.gather`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_LOGIN_PATH,
    API_NODE_STATUS_PATH,
    API_PATH_PREFIX,
    API_POSTFIX_QUEUE_PATH,
    API_QUARANTINE_ACTION_PATH,
    API_SPAM_LIST_PATH,
    API_SPAM_STATUS_PATH,
    API_SPAM_USERS_PATH,
    MAIL_ID_RE,
    REQUEST_TIMEOUT_SECONDS,
    TICKET_VALIDITY_SECONDS,
    VALID_ACTIONS,
)
from .models import (
    NodeStatus,
    PostfixQueues,
    QuarantineMessage,
    QuarantineStatus,
)

_LOGGER = logging.getLogger(__name__)

# ── Custom exceptions ─────────────────────────────────────────────────────────


class PMGError(Exception):
    """Base exception for all PMG API errors."""


class PMGAuthError(PMGError):
    """Raised on authentication failure (401, 403, bad credentials)."""


class PMGAccessForbiddenError(PMGAuthError):
    """Raised when access is forbidden due to lack of permissions (HTTP 403)."""


class PMGConnectionError(PMGError):
    """Raised when the PMG host is unreachable or times out."""


class PMGSSLError(PMGConnectionError):
    """Raised when SSL/TLS certificate verification fails."""


class PMGTimeoutError(PMGConnectionError):
    """Raised when a request to PMG times out."""


class PMGRateLimitError(PMGError):
    """Raised when PMG responds with HTTP 429 Too Many Requests."""


class PMGServerError(PMGError):
    """Raised on PMG server errors (5xx) or malformed responses."""


class PMGValidationError(PMGError):
    """Raised when caller-supplied parameters fail local validation."""


# ── Client ────────────────────────────────────────────────────────────────────


class PMGApiClient:
    """Fully asynchronous client for the Proxmox Mail Gateway REST API.

    The session lifecycle is managed externally (by Home Assistant via
    ``async_get_clientsession``); the client does **not** close the session.

    Args:
        host:       Hostname or IP address of the PMG node.
        port:       HTTPS port (default 8006).
        username:   PMG username, e.g. ``homeassistant@pmg``.
        password:   PMG password.  **Never logged.**
        verify_ssl: If ``False``, TLS certificate validation is disabled.
                    Set to ``False`` for PMG's default self-signed cert.
        session:    An :class:`aiohttp.ClientSession` to use for all requests.
        timeout:    Per-request timeout in seconds.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool,
        session: aiohttp.ClientSession,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        clean_host = host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        if ":" in clean_host:
            host_part, _, port_part = clean_host.partition(":")
            clean_host = host_part
            if port_part.isdigit():
                port = int(port_part)
        self._base_url = f"https://{clean_host}:{port}{API_PATH_PREFIX}"

        user = username.strip()
        if "@" not in user:
            self._username = f"{user}@pam" if user == "root" else f"{user}@pmg"
        else:
            self._username = user
        # Password is stored only in memory, never referenced outside login().
        self._password = password
        self._ssl: bool = verify_ssl
        self._session = session
        self._timeout = timeout

        # ── Session ticket state (never logged) ──────────────────────────────
        self._ticket: str | None = None
        self._csrf: str | None = None
        self._ticket_expires_at: float = 0.0  # monotonic clock value

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def is_authenticated(self) -> bool:
        """Return ``True`` if a valid, unexpired ticket is held."""
        return (
            self._ticket is not None
            and self._csrf is not None
            and time.monotonic() < self._ticket_expires_at
        )

    # ── Authentication ────────────────────────────────────────────────────────

    async def login(self) -> None:
        """Authenticate with PMG and cache the session ticket.

        Sends credentials to ``POST /access/ticket`` and stores the returned
        ticket and CSRF token.  The ticket is **never written to logs**.

        Raises:
            PMGAuthError:      If credentials are rejected (HTTP 401/403).
            PMGConnectionError: If the host is unreachable or times out.
            PMGServerError:    If the response body is malformed.
        """
        url = f"{self._base_url}{API_LOGIN_PATH}"
        timeout = aiohttp.ClientTimeout(total=self._timeout)

        try:
            async with self._session.request(
                "POST",
                url,
                data={"username": self._username, "password": self._password},
                ssl=self._ssl,
                timeout=timeout,
            ) as resp:
                if resp.status in (401, 403):
                    msg = ""
                    try:
                        err_json = await resp.json(content_type=None)
                        if isinstance(err_json, dict) and err_json.get("message"):
                            msg = str(err_json["message"]).strip()
                    except Exception as exc:
                        _LOGGER.debug("Could not parse PMG error JSON: %s", exc)
                    detail = f": {msg}" if msg else ""
                    if resp.status == 403:
                        raise PMGAccessForbiddenError(
                            f"HTTP 403 Forbidden dla '{self._username}'{detail}"
                        )
                    raise PMGAuthError(f"HTTP 401 Unauthorized dla '{self._username}'{detail}")
                if resp.status >= 500:
                    raise PMGServerError(f"Błąd serwera PMG (HTTP {resp.status})")
                try:
                    body: dict[str, Any] = await resp.json(content_type=None)
                except Exception as exc:
                    raise PMGServerError("Invalid JSON in login response") from exc

        except aiohttp.ClientConnectorCertificateError as exc:
            raise PMGSSLError(
                f"Błąd weryfikacji certyfikatu SSL ({exc}). "
                "Odznacz 'Weryfikuj certyfikat SSL', jeśli PMG używa certyfikatu self-signed."
            ) from exc
        except (TimeoutError, aiohttp.ServerTimeoutError) as exc:
            raise PMGTimeoutError(
                f"Przekroczono limit czasu połączenia z {self._base_url} ({self._timeout}s)"
            ) from exc
        except aiohttp.ClientConnectorError as exc:
            raise PMGConnectionError(
                f"Nie można nawiązać połączenia z {self._base_url}: {exc}"
            ) from exc

        payload: dict[str, Any] = body.get("data") or {}
        ticket = payload.get("ticket")
        csrf = payload.get("CSRFPreventionToken")

        if not ticket or not csrf:
            raise PMGAuthError("Login response is missing 'ticket' or 'CSRFPreventionToken'")

        # Store credentials; mark validity window with 200 s safety buffer.
        self._ticket = ticket
        self._csrf = csrf
        self._ticket_expires_at = time.monotonic() + TICKET_VALIDITY_SECONDS

        # Log only the username, NEVER the ticket or CSRF value.
        _LOGGER.debug("PMG authentication successful for user '%s'", self._username)

    async def _ensure_auth(self) -> None:
        """Ensure a valid ticket exists, logging in if necessary."""
        if not self.is_authenticated:
            await self.login()

    # ── Core request dispatcher ───────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        _retrying: bool = False,
    ) -> Any:
        """Execute an authenticated API request and return the ``data`` payload.

        On a ``401`` response the method transparently re-authenticates and
        retries **once**.  A second ``401`` raises :class:`PMGAuthError`.

        Args:
            method:    HTTP verb (``GET``, ``POST``, …).
            path:      API path relative to the base URL (e.g. ``/quarantine/spamstatus``).
            params:    URL query parameters for GET requests.
            data:      Form-encoded body for POST/PUT/DELETE requests.
            _retrying: Internal flag; ``True`` on the re-auth retry.

        Returns:
            The ``data`` field from the parsed JSON response (may be ``None``).

        Raises:
            PMGAuthError:       On persistent authentication failure.
            PMGConnectionError: On network or timeout errors.
            PMGRateLimitError:  On HTTP 429.
            PMGServerError:     On HTTP 5xx or malformed JSON.
        """
        await self._ensure_auth()

        url = f"{self._base_url}{path}"
        timeout = aiohttp.ClientTimeout(total=self._timeout)

        # In aiohttp, passing cookies via `cookies={...}` causes the default CookieJar
        # to silently reject/strip cookies when communicating with raw IP addresses
        # (RFC 6265 strict IP check). We inject PMGAuthCookie directly into headers.
        headers: dict[str, str] = {}
        if self._ticket:
            headers["Cookie"] = f"PMGAuthCookie={self._ticket}"

        # CSRF token is mandatory for state-changing requests.
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            # _ensure_auth() guarantees _csrf is not None at this point.
            headers["CSRFPreventionToken"] = self._csrf  # type: ignore[assignment]

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                ssl=self._ssl,
                timeout=timeout,
            ) as resp:
                # ── Handle authentication failure with one retry ───────────
                if resp.status == 401:
                    if _retrying:
                        raise PMGAuthError(
                            "PMG re-authentication failed; "
                            "please check credentials in the integration options."
                        )
                    _LOGGER.debug(
                        "PMG returned 401 on %s %s — re-authenticating (once)",
                        method,
                        path,
                    )
                    # Invalidate cached ticket and retry.
                    self._ticket = None
                    self._csrf = None
                    self._ticket_expires_at = 0.0
                    return await self._request(
                        method, path, params=params, data=data, _retrying=True
                    )

                if resp.status == 403:
                    raise PMGAccessForbiddenError(
                        f"Brak uprawnień (HTTP 403) do {path}. "
                        "Upewnij się, że użytkownik w PMG ma uprawnienia do kwarantanny."
                    )
                if resp.status == 429:
                    raise PMGRateLimitError(
                        "PMG ograniczył liczbę zapytań (HTTP 429 Too Many Requests)."
                    )
                if resp.status >= 500:
                    raise PMGServerError(f"Błąd wewnętrzny serwera PMG: HTTP {resp.status}")

                if resp.status >= 400:
                    err_body = ""
                    try:
                        err_body = (await resp.text())[:300]
                    except (aiohttp.ClientError, UnicodeDecodeError) as exc:
                        _LOGGER.debug("Failed to read error response body: %s", exc)
                    raise PMGServerError(
                        f"PMG zwrócił błąd HTTP {resp.status} dla {method} {path}"
                        + (f": {err_body}" if err_body else "")
                    )

                try:
                    body: dict[str, Any] = await resp.json(content_type=None)
                except Exception as exc:
                    raise PMGServerError(f"PMG zwrócił odpowiedź inną niż JSON dla {path}") from exc

                return body.get("data")

        except aiohttp.ClientConnectorCertificateError as exc:
            raise PMGSSLError(f"Błąd certyfikatu SSL dla {url}: {exc}") from exc
        except (TimeoutError, aiohttp.ServerTimeoutError) as exc:
            raise PMGTimeoutError(
                f"Zapytanie do PMG przekroczyło limit czasu: {method} {path}"
            ) from exc
        except aiohttp.ClientConnectorError as exc:
            raise PMGConnectionError(f"Brak łączności z PMG ({url}): {exc}") from exc

    # ── Quarantine queries ────────────────────────────────────────────────────

    async def get_spam_status(self) -> QuarantineStatus:
        """Fetch aggregate quarantine statistics.

        Returns:
            A :class:`.models.QuarantineStatus` with count, averages, etc.

        Raises:
            PMGServerError: If the response format is unexpected.
        """
        raw = await self._request("GET", API_SPAM_STATUS_PATH)
        if not isinstance(raw, dict):
            raise PMGServerError(f"Unexpected type for spamstatus response: {type(raw)}")
        return QuarantineStatus.from_api_response(raw)

    async def get_spam_users(
        self,
        starttime: int | None = None,
        endtime: int | None = None,
    ) -> list[str]:
        """Return a list of recipient e-mail addresses that have quarantined mail.

        Args:
            starttime: Filter messages created on or after this epoch timestamp.
            endtime: Filter messages created before this epoch timestamp.

        Returns:
            List of e-mail strings (may be empty).
        """
        params: dict[str, str] = {}
        if starttime is not None:
            params["starttime"] = str(starttime)
        if endtime is not None:
            params["endtime"] = str(endtime)
        raw = await self._request("GET", API_SPAM_USERS_PATH, params=params or None)
        if not isinstance(raw, list):
            _LOGGER.debug("get_spam_users: unexpected response type %s", type(raw))
            return []
        return [str(item["mail"]) for item in raw if isinstance(item, dict) and "mail" in item]

    async def get_spam_messages(
        self,
        pmail: str | None = None,
        starttime: int | None = None,
        endtime: int | None = None,
    ) -> list[QuarantineMessage]:
        """Fetch quarantined messages, optionally for a specific recipient.

        Args:
            pmail: Recipient e-mail address (optional). If None, PMG returns all recipients.
            starttime: Filter messages created on or after this epoch timestamp.
            endtime: Filter messages created before this epoch timestamp.

        Returns:
            List of :class:`.models.QuarantineMessage` objects (may be empty).
        """
        params: dict[str, str] = {}
        if pmail:
            params["pmail"] = pmail
        if starttime is not None:
            params["starttime"] = str(starttime)
        if endtime is not None:
            params["endtime"] = str(endtime)
        raw = await self._request("GET", API_SPAM_LIST_PATH, params=params or None)
        if not isinstance(raw, list):
            return []

        messages: list[QuarantineMessage] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                messages.append(QuarantineMessage.from_api_response(item))
            except (KeyError, ValueError, TypeError) as exc:
                _LOGGER.warning(
                    "Skipping malformed quarantine entry (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
        return messages

    async def get_all_spam_messages(
        self,
        starttime: int | None = None,
        endtime: int | None = None,
    ) -> list[QuarantineMessage]:
        """Fetch quarantined messages for all recipients.

        Queries /quarantine/spamusers with a 30-day lookback window (passing both
        starttime and endtime, because PMG defaults endtime to starttime + 86400)
        and fetches quarantined messages for each discovered mailbox.

        Returns:
            All quarantine messages merged and sorted newest-first.
        """
        now = int(time.time())
        et = endtime if endtime is not None else now
        st = starttime if starttime is not None else (et - 30 * 86400)
        users = await self.get_spam_users(starttime=st, endtime=et)
        if not users:
            return []

        results = await asyncio.gather(
            *[self.get_spam_messages(email, starttime=st, endtime=et) for email in users],
            return_exceptions=True,
        )

        messages: list[QuarantineMessage] = []
        for result in results:
            if isinstance(result, BaseException):
                # Log error type and message; never log per-user email addresses.
                _LOGGER.warning(
                    "Failed to fetch quarantine messages (%s): %s",
                    type(result).__name__,
                    result,
                )
            else:
                messages.extend(result)

        return sorted(messages, key=lambda m: m.timestamp, reverse=True)

    # ── Quarantine actions ────────────────────────────────────────────────────

    async def perform_action(
        self,
        mail_ids: str | list[str],
        action: str,
    ) -> None:
        """Execute an action on one or more quarantined messages.

        Args:
            mail_ids: Single mail ID or list of IDs to act on.  IDs are
                      validated against :data:`.const.MAIL_ID_RE` before
                      the request is sent.
            action:   One of ``deliver``, ``delete``, ``whitelist``,
                      ``blacklist``.

        Raises:
            PMGValidationError: If *mail_ids* or *action* fail local validation.
            PMGAuthError, PMGConnectionError, PMGServerError: On API errors.
        """
        # Normalise to a semicolon-joined string (PMG batch format).
        mail_id_str = ";".join(mail_ids) if isinstance(mail_ids, list) else str(mail_ids)

        # ── Validate before hitting the network ───────────────────────────────
        if not mail_id_str or not MAIL_ID_RE.match(mail_id_str):
            raise PMGValidationError(
                f"Invalid mail_id format (rejected by safety regex): {mail_id_str[:50]!r}"
            )
        if action not in VALID_ACTIONS:
            raise PMGValidationError(
                f"Invalid action {action!r}. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"
            )

        await self._request(
            "POST",
            API_QUARANTINE_ACTION_PATH,
            data={"id": mail_id_str, "action": action},
        )
        _LOGGER.debug("PMG action '%s' completed for %d ID(s)", action, mail_id_str.count(";") + 1)

    # ── Optional node / queue metrics ─────────────────────────────────────────

    async def get_node_status(self) -> NodeStatus:
        """Fetch hardware and OS metrics for the PMG node.

        Returns:
            A :class:`.models.NodeStatus` instance.

        Raises:
            PMGServerError: If the response format is unexpected.
        """
        raw = await self._request("GET", API_NODE_STATUS_PATH)
        if not isinstance(raw, dict):
            raise PMGServerError(f"Unexpected type for node status response: {type(raw)}")
        return NodeStatus.from_api_response(raw)

    async def get_postfix_queues(self) -> PostfixQueues:
        """Fetch Postfix mail queue depths from the PMG node.

        Returns:
            A :class:`.models.PostfixQueues` instance.

        Raises:
            PMGServerError: If the response format is unexpected.
        """
        raw = await self._request("GET", API_POSTFIX_QUEUE_PATH)
        if not isinstance(raw, dict):
            raise PMGServerError(f"Unexpected type for postfix queue response: {type(raw)}")
        return PostfixQueues.from_api_response(raw)

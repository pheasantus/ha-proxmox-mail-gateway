"""DataUpdateCoordinator for Proxmox Mail Gateway.

Responsibilities
----------------
* Polls all PMG data sources on a configurable interval.
* Runs per-user quarantine queries concurrently using :func:`asyncio.gather`.
* Diffs mail IDs between cycles and fires ``pmg_mail_quarantined`` events for
  newly discovered messages.
* Translates PMG API exceptions into the appropriate HA exception types so
  that Home Assistant can surface them correctly in the UI.

Security notes
--------------
* Mail sender / subject text in event payloads is already sanitized by
  :class:`.models.QuarantineMessage.from_api_response` before it reaches this
  layer.
* ``ConfigEntryAuthFailed`` is raised (not suppressed) so HA can prompt the
  user to re-enter credentials.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PMGApiClient, PMGAuthError, PMGError
from .const import (
    CONF_ENABLE_NODE_SENSORS,
    CONF_ENABLE_POSTFIX_SENSORS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_NODE_SENSORS,
    DEFAULT_ENABLE_POSTFIX_SENSORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_MAIL_QUARANTINED,
)
from .models import PMGData

_LOGGER = logging.getLogger(__name__)


class PMGDataUpdateCoordinator(DataUpdateCoordinator[PMGData]):
    """Coordinate all data fetching for the Proxmox Mail Gateway integration.

    Args:
        hass:  The Home Assistant instance.
        api:   An authenticated :class:`.api.PMGApiClient`.
        entry: The active config entry (provides options such as scan interval).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: PMGApiClient,
        entry: ConfigEntry,
    ) -> None:
        self.api: PMGApiClient = api
        self.entry: ConfigEntry = entry
        self._known_mail_ids: set[str] = set()
        self._first_run: bool = True

        scan_interval: int = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=scan_interval),
        )

    # ── Core data fetch ───────────────────────────────────────────────────────

    async def _async_update_data(self) -> PMGData:
        """Fetch a full PMG data snapshot.

        Returns:
            A :class:`.models.PMGData` with current quarantine state.

        Raises:
            ConfigEntryAuthFailed: If PMG rejects credentials (causes HA to
                prompt the user to re-authenticate).
            UpdateFailed: On any other PMG API error (HA marks the entity
                unavailable and retries on the next interval).
        """
        try:
            status = await self.api.get_spam_status()
            messages = await self.api.get_all_spam_messages()

            # Optional Postfix queue metrics
            queues = None
            if self.entry.options.get(CONF_ENABLE_POSTFIX_SENSORS, DEFAULT_ENABLE_POSTFIX_SENSORS):
                try:
                    queues = await self.api.get_postfix_queues()
                except PMGError as exc:
                    _LOGGER.debug("Postfix queue fetch skipped: %s", type(exc).__name__)

            # Optional node hardware metrics
            node = None
            if self.entry.options.get(CONF_ENABLE_NODE_SENSORS, DEFAULT_ENABLE_NODE_SENSORS):
                try:
                    node = await self.api.get_node_status()
                except PMGError as exc:
                    _LOGGER.debug("Node status fetch skipped: %s", type(exc).__name__)

        except PMGAuthError as exc:
            raise ConfigEntryAuthFailed(
                "Proxmox Mail Gateway rejected credentials. "
                "Please update them in the integration settings."
            ) from exc
        except PMGError as exc:
            raise UpdateFailed(f"PMG data fetch failed: {exc}") from exc

        # ── Event detection: fire events only for NEW messages ────────────────
        current_ids: set[str] = {m.id for m in messages}

        if not self._first_run:
            new_ids: set[str] = current_ids - self._known_mail_ids
            for message in messages:
                if message.id in new_ids:
                    self.hass.bus.async_fire(
                        EVENT_MAIL_QUARANTINED,
                        {
                            "mail_id": message.id,
                            "sender": message.sender,
                            "recipient": message.pmail,
                            "subject": message.subject,
                            "spam_score": message.spam_score,
                            "time": message.timestamp,
                        },
                    )
                    _LOGGER.debug("New quarantined mail detected: id=%s", message.id)

        self._first_run = False
        self._known_mail_ids = current_ids

        return PMGData(status=status, messages=messages, queues=queues, node=node)

    # ── Action helper ─────────────────────────────────────────────────────────

    async def async_perform_action(self, mail_id: str, action: str) -> None:
        """Perform a quarantine action and schedule an immediate data refresh.

        Validation of *mail_id* and *action* is performed in
        :meth:`.api.PMGApiClient.perform_action`; this method does not
        duplicate that logic.

        Args:
            mail_id: One or more mail IDs (semicolon-separated for batches).
            action:  One of ``deliver``, ``delete``, ``whitelist``, ``blacklist``.
        """
        await self.api.perform_action(mail_id, action)
        await self.async_request_refresh()

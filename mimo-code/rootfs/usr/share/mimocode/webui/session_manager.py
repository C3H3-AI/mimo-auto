"""Unified session manager for all channels.

Maps stable channel_key → mimo session_id, with auto-recovery on 409/404.
All operations are async and designed for a single event loop.

channel_key format: "{channel_type}:{account_id}:{user_id}"
Examples:
  - feishu:default:ou_xxx
  - personal_wechat:default:wx_user_xxx
  - wechat_work:acc1:user_yyy
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from client import MimoAIClient, MimoAPIError

_LOGGER = logging.getLogger(__name__)

SESSION_FILE = "/data/mimocode/sessions.json"
MAX_RETRY_BUSY = 3
RETRY_BUSY_DELAY = 1.0  # seconds


class MimoSessionManager:
    """Manages channel_key ↔ session_id mappings with persistence.

    Thread-safe: uses asyncio lock (designed for single event loop usage).
    """

    def __init__(self, path: str = SESSION_FILE) -> None:
        self._path = path
        # In-memory mapping: channel_key → session_id
        self._data: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    async def _load(self) -> None:
        if self._loaded:
            return
        try:
            if os.path.exists(self._path):
                loop = asyncio.get_event_loop()
                self._data = await loop.run_in_executor(None, self._read_file)
                _LOGGER.debug("Loaded %d sessions from %s", len(self._data), self._path)
        except Exception as err:
            _LOGGER.warning("Failed to load sessions: %s", err)
            self._data = {}
        self._loaded = True

    def _read_file(self) -> dict[str, str]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    async def _save(self) -> None:
        loop = asyncio.get_event_loop()
        data_copy = dict(self._data)
        await loop.run_in_executor(None, self._write_file, data_copy)

    def _write_file(self, data: dict[str, str]) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception as err:
            _LOGGER.warning("Failed to save sessions: %s", err)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def get_or_create_session(
        self,
        mimo_client: MimoAIClient,
        channel_key: str,
        force_new: bool = False,
    ) -> str:
        """Get existing session or create new one for channel_key.

        Args:
            force_new: If True, skip cache and always create a new session.
                       Use after 409 Busy to get a fresh session.
        """
        await self._load()

        if force_new:
            # Clear cached session for this key
            async with self._lock:
                self._data.pop(channel_key, None)
            session_id = ""
        else:
            async with self._lock:
                session_id = self._data.get(channel_key, "")

        if session_id:
            # Verify existing session
            session_id = await self._verify_session(mimo_client, session_id)

        if not session_id:
            # Create new session
            session_id = await self._create_session(mimo_client, channel_key)

        # Persist mapping
        async with self._lock:
            self._data[channel_key] = session_id
            await self._save()

        return session_id

    async def _verify_session(
        self, mimo_client: MimoAIClient, session_id: str
    ) -> str:
        """Verify session is valid. Returns session_id if valid, empty string if not."""
        try:
            valid = await mimo_client.ensure_session(session_id, timeout=5.0)
            if valid == session_id:
                return session_id
        except Exception as err:
            _LOGGER.debug("Session %s verify failed: %s", session_id, err)
        return ""

    async def _create_session(
        self, mimo_client: MimoAIClient, channel_key: str
    ) -> str:
        """Create a new session with retry for 409 Busy."""
        for attempt in range(1, MAX_RETRY_BUSY + 1):
            try:
                new_id = await mimo_client.ensure_session("", timeout=10.0)
                if new_id:
                    _LOGGER.info(
                        "Created new session %s for channel_key=%s (attempt %d)",
                        new_id, channel_key, attempt,
                    )
                    return new_id
            except MimoAPIError as err:
                if err.status == 409:
                    _LOGGER.warning(
                        "Session creation got 409 (attempt %d/%d), retrying...",
                        attempt, MAX_RETRY_BUSY,
                    )
                    if attempt < MAX_RETRY_BUSY:
                        await asyncio.sleep(RETRY_BUSY_DELAY * attempt)
                    continue
                _LOGGER.error("Session creation failed: %s", err)
            except Exception as err:
                _LOGGER.error("Session creation error: %s", err)
                break
        # Last resort: create without verify (mimo_client handles it)
        _LOGGER.warning("Creating session via empty ensure_session for %s", channel_key)
        return await mimo_client.ensure_session("", timeout=15.0)

    async def get_stored_session(self, channel_key: str) -> str | None:
        """Return stored session_id without verification."""
        await self._load()
        async with self._lock:
            return self._data.get(channel_key)

    async def clear_session(self, channel_key: str) -> None:
        """Remove session mapping."""
        await self._load()
        async with self._lock:
            self._data.pop(channel_key, None)
            await self._save()

    async def flush(self) -> None:
        """Force immediate save."""
        await self._save()

"""Persistent session ID store for multi-turn conversations.

Maps channel-specific keys (e.g. "feishu:<chat_id>", "wechat:<user_id>")
to mimo serve session IDs, persisted as a JSON file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_PATH = "/data/mimocode/sessions.json"


class SessionStore:
    """Thread-safe JSON-backed session ID store with debounced persistence."""

    def __init__(self, path: str = DEFAULT_PATH, debounce_seconds: float = 2.0) -> None:
        self._path = path
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()
        self._debounce_seconds = debounce_seconds
        self._pending_save: bool = False
        self._save_timer: threading.Timer | None = None
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                _LOGGER.debug("Loaded %d sessions from %s", len(self._data), self._path)
        except Exception as err:
            _LOGGER.warning("Failed to load sessions: %s", err)
            self._data = {}

    def _save(self) -> None:
        """Debounced save: delays actual disk write to batch rapid changes."""
        self._pending_save = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(self._debounce_seconds, self._do_save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _do_save(self) -> None:
        """Actually write to disk (called by debounce timer)."""
        with self._lock:
            if not self._pending_save:
                return
            self._pending_save = False
            try:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except Exception as err:
                _LOGGER.warning("Failed to save sessions: %s", err)

    def flush(self) -> None:
        """Force immediate save to disk (e.g. before shutdown)."""
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        self._do_save()

    # -- public API ---------------------------------------------------------

    def get_session_id(self, channel_key: str) -> str | None:
        """Return the stored session ID for *channel_key*, or None."""
        with self._lock:
            return self._data.get(channel_key)

    def set_session_id(self, channel_key: str, session_id: str) -> None:
        """Store a session ID for *channel_key* and persist to disk."""
        with self._lock:
            self._data[channel_key] = session_id
            self._save()

    def clear(self, channel_key: str) -> None:
        """Remove the session mapping for *channel_key*."""
        with self._lock:
            self._data.pop(channel_key, None)
            self._save()

    def clear_all(self) -> None:
        """Remove all session mappings."""
        with self._lock:
            self._data.clear()
            self._save()

    def all_keys(self) -> list[str]:
        """Return all stored channel keys."""
        with self._lock:
            return list(self._data.keys())

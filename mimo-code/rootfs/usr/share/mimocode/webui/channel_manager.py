"""Channel manager for MiMo Code Addon.

Manages multiple IM channel connections (Feishu, WeChat Work, Personal WeChat)
and routes messages to mimo serve.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from client import MimoAIClient, MimoAPIError
from session_store import SessionStore
from ha_context import get_ha_context_builder
from media import parse_reply_segments, TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment
from base_channel import build_system_prompt, is_rate_limit, RATE_LIMIT_MESSAGE
from evolution_review import get_evolution_review
from feishu_client import FeishuClient
from wechat_client import WeChatWorkClient
from wechat_personal import PersonalWeChatClient, DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__name__)

# Default config file path
DEFAULT_CONFIG_PATH = "/data/mimocode/mimo.json"


class ChannelManager:
    """Manages IM channel connections.

    Loads channel configuration, starts channel clients,
    and routes messages to mimo serve via MimoAIClient (async).
    """

    def __init__(
        self,
        config: dict[str, Any],
        mimo_serve_url: str = "http://127.0.0.1:14095",
    ) -> None:
        """Initialize the channel manager.

        Args:
            config: Channel configuration dictionary.
            mimo_serve_url: URL of mimo serve API.
        """
        self._config = config
        self._mimo_serve_url = mimo_serve_url
        self._channels: dict[str, Any] = {}
        self._running = False
        self._mimo_client = MimoAIClient(base_url=mimo_serve_url)
        self._session_store = SessionStore()

    @classmethod
    def from_config_file(
        cls,
        config_path: str = DEFAULT_CONFIG_PATH,
        mimo_serve_url: str = "http://127.0.0.1:14095",
    ) -> "ChannelManager":
        """Create ChannelManager from config file.

        Args:
            config_path: Path to mimo.json config file.
            mimo_serve_url: URL of mimo serve API.

        Returns:
            ChannelManager instance.
        """
        config = {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except FileNotFoundError:
            _LOGGER.info("Config file not found: %s", config_path)
        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse config: %s", err)

        return cls(config, mimo_serve_url)

    async def start(self) -> None:
        """Start all enabled channels."""
        if self._running:
            return

        self._running = True
        channels_config = self._config.get("channels", {})

        # Start Feishu if configured
        feishu_config = channels_config.get("feishu", {})
        if feishu_config.get("enabled", False):
            await self._start_feishu(feishu_config)

        # Start WeChat Work accounts (supports multiple)
        wechat_config = channels_config.get("wechat", {})
        if isinstance(wechat_config, dict):
            if wechat_config.get("enabled", False):
                await self._start_wechat(wechat_config, account_id="default")
        elif isinstance(wechat_config, list):
            for account in wechat_config:
                if account.get("enabled", False):
                    account_id = account.get("id", f"wechat_{len(self._channels)}")
                    await self._start_wechat(account, account_id=account_id)

        # Start Personal WeChat (supports multiple)
        personal_wechat_config = channels_config.get("personal_wechat", {})
        if isinstance(personal_wechat_config, dict):
            if personal_wechat_config.get("enabled", False):
                await self._start_personal_wechat(personal_wechat_config, account_id="default")
        elif isinstance(personal_wechat_config, list):
            for account in personal_wechat_config:
                if account.get("enabled", False):
                    account_id = account.get("id", f"wx_{len(self._channels)}")
                    await self._start_personal_wechat(account, account_id=account_id)

        if self._channels:
            _LOGGER.info(
                "Started %d channel(s): %s",
                len(self._channels),
                ", ".join(self._channels.keys()),
            )
        else:
            _LOGGER.info("No channels enabled")

    async def stop(self) -> None:
        """Stop all channels."""
        self._running = False

        for name, client in self._channels.items():
            try:
                if isinstance(client, dict):
                    continue
                await client.stop()
                _LOGGER.info("Stopped channel: %s", name)
            except Exception as err:
                _LOGGER.error("Error stopping channel %s: %s", name, err)

        self._channels.clear()

    async def reload(self, config: dict[str, Any]) -> None:
        """Stop all channels and restart with a new config."""
        _LOGGER.info("Reloading channel manager with new config")
        await self.stop()
        self._config = config
        self._running = False
        self._channels.clear()
        await self.start()

    def get_status(self) -> dict[str, Any]:
        """Return a status dict for each known channel."""
        status: dict[str, Any] = {}
        for name, client in self._channels.items():
            if isinstance(client, dict):
                ch_status = client.get("status", "pending")
                status[name] = {
                    "connected": ch_status == "connected",
                    "status": ch_status,
                }
            else:
                status_str = getattr(client, "connection_status", None)
                if status_str:
                    status[name] = {
                        "connected": status_str == "connected",
                        "status": status_str,
                        "error": getattr(client, "last_error", None),
                    }
                else:
                    status[name] = {
                        "connected": bool(getattr(client, "is_connected", False) or getattr(client, "is_logged_in", False)),
                        "error": getattr(client, "last_error", None),
                    }
        return status

    async def _start_feishu(self, config: dict[str, Any]) -> None:
        """Start Feishu channel (WebSocket long-connection, self-contained)."""
        app_id = config.get("app_id", "")
        app_secret = config.get("app_secret", "")

        if not app_id or not app_secret:
            _LOGGER.warning("Feishu channel missing app_id or app_secret")
            return

        client = FeishuClient(
            app_id=app_id,
            app_secret=app_secret,
            mimo_serve_url=self._mimo_serve_url,
            verification_token=config.get("verification_token"),
            encrypt_key=config.get("encrypt_key"),
            show_reasoning=bool(config.get("show_reasoning", True)),
            on_message=self._handle_message,
        )

        client.start()
        self._channels["feishu"] = client
        _LOGGER.info("Feishu channel started")

    async def _start_wechat(self, config: dict[str, Any], account_id: str) -> None:
        """Start WeChat Work channel."""
        corp_id = config.get("corp_id", "")
        agent_id = config.get("agent_id", "")
        secret = config.get("secret", "")
        token = config.get("token", "")
        encoding_aes_key = config.get("encoding_aes_key", "")

        if not corp_id or not agent_id or not secret:
            _LOGGER.warning(
                "WeChat Work channel missing corp_id, agent_id, or secret: %s",
                account_id,
            )
            return

        client = WeChatWorkClient(
            corp_id=corp_id,
            agent_id=agent_id,
            secret=secret,
            token=token,
            encoding_aes_key=encoding_aes_key,
            on_message=self._handle_message,
            account_id=account_id,
        )

        await client.start()
        channel_key = f"wechat_{account_id}"
        self._channels[channel_key] = client
        _LOGGER.info("WeChat Work channel started: %s", account_id)

    async def _start_personal_wechat(self, config: dict[str, Any], account_id: str) -> None:
        """Start Personal WeChat channel."""
        saved_creds = config.get("credentials", {})

        # Save sync_buf to config whenever it changes (async, non-blocking)
        def _on_state_change(new_buf: str) -> None:
            asyncio.create_task(self._persist_sync_buf(new_buf))

        client = PersonalWeChatClient(
            on_message=self._handle_message,
            base_url=config.get("base_url") or DEFAULT_BASE_URL,
            account_id=account_id,
            save_state_callback=_on_state_change,
            show_reasoning=bool(config.get("show_reasoning", True)),
            mimo_serve_url=self._mimo_serve_url,
        )

        if saved_creds and client.load_credentials(saved_creds):
            client.start()  # synchronous - starts a background thread
            channel_key = f"personal_wechat_{account_id}"
            self._channels[channel_key] = client
            _LOGGER.info("Personal WeChat channel started (from saved credentials): %s", account_id)
        else:
            channel_key = f"personal_wechat_{account_id}"
            self._channels[channel_key] = {
                "client": client,
                "status": "pending_login",
                "account_id": account_id,
            }
            _LOGGER.info("Personal WeChat pending QR login: %s", account_id)

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    async def _persist_sync_buf(self, new_buf: str) -> None:
        """Persist WeChat sync_buf to config file (async, non-blocking)."""
        import os
        try:
            loop = asyncio.get_event_loop()
            cfg = await loop.run_in_executor(None, self._read_config_file)
            if "personal_wechat" not in cfg.setdefault("channels", {}):
                cfg["channels"]["personal_wechat"] = {"enabled": True}
            if "credentials" not in cfg["channels"]["personal_wechat"]:
                cfg["channels"]["personal_wechat"]["credentials"] = {}
            cfg["channels"]["personal_wechat"]["credentials"]["get_updates_buf"] = new_buf
            await loop.run_in_executor(None, self._write_config_file, cfg)
        except Exception as e:
            _LOGGER.warning("Failed to persist sync_buf: %s", e)

    @staticmethod
    def _read_config_file() -> dict[str, Any]:
        try:
            with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"channels": {}}

    @staticmethod
    def _write_config_file(cfg: dict[str, Any]) -> None:
        import os
        os.makedirs(os.path.dirname(DEFAULT_CONFIG_PATH), exist_ok=True)
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Message handling (async)
    # ------------------------------------------------------------------ #

    async def _handle_message(self, message: dict[str, Any], on_reasoning: Any = None) -> str:
        """Handle incoming message from any channel (async).

        Args:
            message: Message dictionary with text, sender_id, chat_id, etc.
            on_reasoning: Optional callback for real-time reasoning push.

        Returns:
            Response text to send back (plain text, media tags preserved).
        """
        text = message.get("text", "")
        sender_id = message.get("sender_id", "")
        chat_id = message.get("chat_id", "")
        account_id = message.get("account_id", "")

        # Build context for AI including inbound media info
        media_info = message.get("media_info", "")
        ai_input = f"{text}\n{media_info}".strip() if media_info else text

        _LOGGER.info(
            "Processing message from %s/%s: %s",
            account_id or "unknown",
            sender_id,
            text[:100],
        )

        session_key = f"{sender_id}:{chat_id}" if chat_id else sender_id or "default"
        response = await self._call_mimo_serve(ai_input, session_key, on_reasoning=on_reasoning)

        return response

    async def _call_mimo_serve(
        self,
        text: str,
        session_key: str = "default",
        on_reasoning: Any = None,
    ) -> str:
        """Call mimo serve API to get AI response (async, via MimoAIClient).

        Uses session persistence across restarts via SessionStore.
        Injects HA device context into system prompt for smart responses.
        Supports real-time reasoning push via on_reasoning callback.

        Args:
            text: User message text.
            session_key: Key for session persistence (e.g. "user_id:chat_id").
            on_reasoning: Optional callback for real-time reasoning push.

        Returns:
            AI response text.
        """
        try:
            # 1. Restore session from persistence, or create new
            stored_id = self._session_store.get_session_id(session_key)
            session_id = stored_id if stored_id else ""
            session_id = await self._mimo_client.ensure_session(session_id)
            # Only write back if we got a valid session_id
            if session_id:
                self._session_store.set_session_id(session_key, session_id)
            else:
                _LOGGER.error("ensure_session returned empty session_id for key %s", session_key)

            # 2. Build system prompt with HA device context + learned lessons
            ha_ctx = get_ha_context_builder()
            device_context = await ha_ctx.get_context()
            evolution = get_evolution_review()
            lessons_context = evolution.get_lessons_context()
            system_prompt = build_system_prompt(device_context)
            if lessons_context:
                system_prompt = f"{system_prompt}\n\n{lessons_context}"

            # 3. Send message with system context
            _LOGGER.info(
                "Calling mimo serve: session=%s, text=%s, system_len=%d",
                session_id, text[:50], len(system_prompt),
            )

            # Collect text events, push reasoning via callback (real-time, chunk by chunk)
            collected_text = []

            async def _on_event(event: dict) -> None:
                if event.get("type") == "text":
                    collected_text.append(event["text"])
                elif event.get("type") == "reasoning":
                    if on_reasoning:
                        r = event.get("text", "").strip()
                        if r:
                            try:
                                await on_reasoning(r)
                            except Exception:
                                pass

            # Send message, retry with new session on 409 or 404
            try:
                await self._mimo_client.send_message(
                    text, session_id, system=system_prompt, on_event=_on_event
                )
            except MimoAPIError as send_err:
                if send_err.status in (409, 404):
                    _LOGGER.warning("Session issue (HTTP %d), creating new session", send_err.status)
                    session_id = await self._mimo_client.ensure_session("")
                    if session_id:
                        self._session_store.set_session_id(session_key, session_id)
                        collected_text.clear()
                        await self._mimo_client.send_message(
                            text, session_id, system=system_prompt, on_event=_on_event
                        )
                else:
                    raise

            response = "\n".join(collected_text)
            _LOGGER.info("mimo serve returned: %s", response[:100] if response else "(empty)")

            # 4. Check for rate limiting
            if response and is_rate_limit(response):
                _LOGGER.warning("MiMo rate-limited, returning fallback message")
                return RATE_LIMIT_MESSAGE

            # 5. Schedule evolution review (background, fire-and-forget)
            if response:
                evolution = get_evolution_review()
                asyncio.create_task(
                    evolution.schedule_review(text, response, self._mimo_client, session_id)
                )

            return response or ""

        except Exception as err:
            _LOGGER.error("Error calling mimo serve: %s", err)
            return f"Error: {str(err)}"

    @property
    def is_running(self) -> bool:
        """Check if any channel is running."""
        return self._running and len(self._channels) > 0

    @property
    def channels(self) -> dict[str, Any]:
        """Get active channels."""
        return self._channels.copy()

    def get_pending_logins(self) -> dict[str, Any]:
        """Get channels pending QR code login."""
        pending = {}
        for key, value in self._channels.items():
            if isinstance(value, dict) and value.get("status") == "pending_login":
                pending[key] = value
        return pending

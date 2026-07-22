"""Channel manager for MiMo Code Addon.

Manages multiple IM channel connections (Feishu, WeChat Work, Personal WeChat)
and routes messages to mimo serve.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from client import MimoAIClient
from session_store import SessionStore
from ha_context import get_ha_context_builder
from feishu_client import FeishuClient
from wechat_client import WeChatWorkClient
from wechat_personal import PersonalWeChatClient, DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__name__)

# Rate limit detection patterns (case-insensitive)
_RATE_LIMIT_KEYWORDS = [
    "排队等待", "token plan", "/login",
    "subscribe", "free mode", "queue",
    "rate limit", "too many requests",
]

RATE_LIMIT_MESSAGE = "AI 服务繁忙，请稍后再试。"


def _is_rate_limit(text: str) -> bool:
    """Detect mimo serve rate-limiting messages."""
    lower = text.lower()
    return any(kw in lower for kw in _RATE_LIMIT_KEYWORDS)

# Default config file path
DEFAULT_CONFIG_PATH = "/usr/share/mimocode/webui/mimo.json"


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

        client = PersonalWeChatClient(
            on_message=self._handle_message,
            base_url=config.get("base_url") or DEFAULT_BASE_URL,
            account_id=account_id,
        )

        if saved_creds and client.load_credentials(saved_creds):
            await client.start()
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
    # Message handling (async)
    # ------------------------------------------------------------------ #

    async def _handle_message(self, message: dict[str, Any]) -> str:
        """Handle incoming message from any channel (async).

        Args:
            message: Message dictionary with text, sender_id, chat_id, etc.

        Returns:
            Response text to send back.
        """
        text = message.get("text", "")
        sender_id = message.get("sender_id", "")
        chat_id = message.get("chat_id", "")
        account_id = message.get("account_id", "")

        _LOGGER.info(
            "Processing message from %s/%s: %s",
            account_id or "unknown",
            sender_id,
            text[:100],
        )

        session_key = f"{sender_id}:{chat_id}" if chat_id else sender_id or "default"
        response = await self._call_mimo_serve(text, session_key)

        return response

    async def _call_mimo_serve(
        self,
        text: str,
        session_key: str = "default",
    ) -> str:
        """Call mimo serve API to get AI response (async, via MimoAIClient).

        Uses session persistence across restarts via SessionStore.
        Injects HA device context into system prompt for smart responses.

        Args:
            text: User message text.
            session_key: Key for session persistence (e.g. "user_id:chat_id").

        Returns:
            AI response text.
        """
        try:
            # 1. Restore session from persistence, or create new
            session_id = self._session_store.get_session_id(session_key)
            session_id = await self._mimo_client.ensure_session(session_id or "")
            # Always write back (handles idempotent writes and id changes)
            self._session_store.set_session_id(session_key, session_id)

            # 2. Build system prompt with HA device context
            ha_ctx = get_ha_context_builder()
            device_context = await ha_ctx.get_context()

            system_prompt = (
                "你是 Home Assistant 管家。根据用户请求控制设备或回答问题。\n"
                "如果用户要求控制设备，直接调用对应工具。\n"
                "回复要简洁友好。\n\n"
                f"{device_context}"
            )

            # 3. Send message with system context
            response = await self._mimo_client.send_message(
                text, session_id, system=system_prompt
            )

            # 4. Check for rate limiting
            if response and _is_rate_limit(response):
                _LOGGER.warning("MiMo rate-limited, returning fallback message")
                return RATE_LIMIT_MESSAGE

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

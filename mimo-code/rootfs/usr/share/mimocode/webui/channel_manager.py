"""Channel manager for MiMo Code Addon.

Manages multiple IM channel connections (Feishu, WeChat Work, Personal WeChat)
and routes messages to mimo serve.
Uses only standard library (no aiohttp).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import urllib.error
from typing import Any, Callable, Awaitable

from feishu_client import FeishuClient
from wechat_client import WeChatWorkClient
from wechat_personal import PersonalWeChatClient

_LOGGER = logging.getLogger(__name__)

# Default config file path
DEFAULT_CONFIG_PATH = "/usr/share/mimocode/webui/mimo.json"


class ChannelManager:
    """Manages IM channel connections.

    Loads channel configuration, starts channel clients,
    and routes messages to mimo serve.
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
                    # pending login placeholder — nothing to stop
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
                status[name] = {
                    "connected": False,
                    "status": client.get("status", "pending"),
                }
            else:
                status[name] = {
                    "connected": bool(getattr(client, "is_connected", False)),
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

        # FeishuClient.start() spawns its own WS thread and returns immediately.
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
        """Start Personal WeChat channel.

        Args:
            config: Personal WeChat configuration.
            account_id: Account identifier.
        """
        # Check for saved credentials
        saved_creds = config.get("credentials", {})

        client = PersonalWeChatClient(
            on_message=self._handle_message,
            base_url=config.get("base_url", "https://ilink-bot.weixin.qq.com"),
            account_id=account_id,
        )

        # Try to load saved credentials
        if saved_creds and client.load_credentials(saved_creds):
            await client.start()
            channel_key = f"personal_wechat_{account_id}"
            self._channels[channel_key] = client
            _LOGGER.info("Personal WeChat channel started (from saved credentials): %s", account_id)
        else:
            # Need QR code login - store client for later
            channel_key = f"personal_wechat_{account_id}"
            self._channels[channel_key] = {
                "client": client,
                "status": "pending_login",
                "account_id": account_id,
            }
            _LOGGER.info("Personal WeChat pending QR login: %s", account_id)

    async def _handle_message(self, message: dict[str, Any]) -> str:
        """Handle incoming message from any channel.

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

        # Call mimo serve
        response = await self._call_mimo_serve(text, sender_id, chat_id)

        return response

    async def _call_mimo_serve(
        self,
        text: str,
        user_id: str = "",
        conversation_id: str = "",
    ) -> str:
        """Call mimo serve API to get AI response.

        Args:
            text: User message text.
            user_id: User ID for session management.
            conversation_id: Conversation ID for session management.

        Returns:
            AI response text.
        """
        session_id = conversation_id or user_id or "default"

        try:
            # Create or get session
            session_url = f"{self._mimo_serve_url}/session"
            session_payload = json.dumps({"id": session_id}).encode("utf-8")
            req = urllib.request.Request(
                session_url,
                data=session_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    session_id = data.get("id", session_id)
            except Exception:
                pass

            # Send message
            message_url = f"{self._mimo_serve_url}/session/{session_id}/message"
            message_payload = json.dumps({
                "message": text,
                "parts": [{"type": "text", "text": text}],
            }).encode("utf-8")

            req = urllib.request.Request(
                message_url,
                data=message_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=180) as resp:
                response_text = await self._parse_response(resp)
                return response_text

        except Exception as err:
            _LOGGER.error("Error calling mimo serve: %s", err)
            return f"Error: {str(err)}"

    async def _parse_response(self, resp) -> str:
        """Parse streaming JSON response from mimo serve.

        Args:
            resp: HTTP response with NDJSON streaming.

        Returns:
            Extracted response text.
        """
        collected_texts = []
        buffer = ""

        while True:
            chunk = resp.read(4096)
            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="replace")

            while True:
                buffer = buffer.lstrip()
                if not buffer:
                    break

                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:]

                    if not isinstance(obj, dict):
                        continue

                    info = obj.get("info", {})
                    parts = obj.get("parts", [])

                    if info.get("role") != "assistant":
                        continue
                    if info.get("finish") != "stop":
                        continue

                    for part in parts:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "").strip()
                            if text:
                                collected_texts.append(text)

                except json.JSONDecodeError:
                    break

        return "\n".join(collected_texts) if collected_texts else ""

    @property
    def is_running(self) -> bool:
        """Check if any channel is running."""
        return self._running and len(self._channels) > 0

    @property
    def channels(self) -> dict[str, Any]:
        """Get active channels."""
        return self._channels.copy()

    def get_pending_logins(self) -> dict[str, Any]:
        """Get channels pending QR code login.

        Returns:
            Dict of channel_key -> client info.
        """
        pending = {}
        for key, value in self._channels.items():
            if isinstance(value, dict) and value.get("status") == "pending_login":
                pending[key] = value
        return pending

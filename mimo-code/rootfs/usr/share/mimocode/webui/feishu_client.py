"""Feishu WebSocket client for MiMo Code Addon.

Connects to Feishu's WebSocket API to receive and send messages.
Independent of Home Assistant - runs directly in the Addon.
Uses only standard library (no aiohttp).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Awaitable

_LOGGER = logging.getLogger(__name__)

# Feishu API endpoints
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# Reconnect settings
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60


class FeishuClient:
    """Feishu HTTP client (webhook mode).

    Receives messages via webhook and sends responses.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: Callable[[dict], Awaitable[str]],
        verification_token: str | None = None,
        encrypt_key: str | None = None,
    ) -> None:
        """Initialize the Feishu client.

        Args:
            app_id: Feishu app ID.
            app_secret: Feishu app secret.
            on_message: Async callback for handling messages.
            verification_token: Optional verification token for webhook validation.
            encrypt_key: Optional encryption key for message decryption.
        """
        self._app_id = app_id
        self._app_secret = app_secret
        self._on_message = on_message
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key

        self._tenant_token: str | None = None
        self._token_expires: float = 0
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Check if client is active."""
        return self._running

    async def start(self) -> None:
        """Start the Feishu client."""
        if self._running:
            return

        self._running = True
        # Start token refresh
        asyncio.create_task(self._token_refresh_loop())
        _LOGGER.info("Feishu client started")

    async def stop(self) -> None:
        """Stop the Feishu client."""
        self._running = False

    async def _token_refresh_loop(self) -> None:
        """Refresh access token periodically."""
        while self._running:
            try:
                await self._refresh_token()
                sleep_time = max(self._token_expires - time.time() - 600, 60)
                await asyncio.sleep(sleep_time)
            except Exception as err:
                _LOGGER.error("Token refresh error: %s", err)
                await asyncio.sleep(300)

    async def _refresh_token(self) -> None:
        """Refresh tenant access token."""
        if self._tenant_token and time.time() < self._token_expires:
            return

        payload = json.dumps({
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        }).encode("utf-8")

        req = urllib.request.Request(
            FEISHU_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    self._tenant_token = data.get("tenant_access_token")
                    self._token_expires = time.time() + data.get("expire", 7200) - 600
                    _LOGGER.info("Feishu token refreshed")
                else:
                    _LOGGER.error("Failed to get token: %s", data.get("msg"))
        except Exception as err:
            _LOGGER.error("Token refresh error: %s", err)

    async def handle_webhook(
        self,
        data: bytes,
        msg_signature: str = "",
        timestamp: str = "",
        nonce: str = "",
    ) -> str | None:
        """Handle incoming webhook message.

        Args:
            data: Raw JSON message data.
            msg_signature: Message signature.
            timestamp: Timestamp.
            nonce: Nonce.

        Returns:
            JSON response or None.
        """
        try:
            event = json.loads(data.decode("utf-8"))

            # Handle URL verification challenge
            if "challenge" in event:
                return json.dumps({"challenge": event["challenge"]})

            # Extract message info
            header = event.get("header", {})
            event_data = event.get("event", {})

            message = event_data.get("message", {})
            sender = event_data.get("sender", {})

            message_id = message.get("message_id", "")
            chat_id = message.get("chat_id", "")
            content = message.get("content", "")
            msg_type = message.get("message_type", "")
            sender_id = sender.get("sender_id", {}).get("open_id", "")

            # Parse content
            try:
                content_data = json.loads(content)
                text = content_data.get("text", "")
            except json.JSONDecodeError:
                text = content

            # Skip bot's own messages
            if sender.get("sender_type") == "app":
                return None

            _LOGGER.info("Received Feishu message from %s: %s", sender_id, text[:100])

            # Call message handler
            response = await self._on_message({
                "message_id": message_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "text": text,
                "msg_type": msg_type,
            })

            # Return empty response (async reply via API)
            return json.dumps({})

        except Exception as err:
            _LOGGER.error("Error handling Feishu message: %s", err)
            return json.dumps({})

    async def send_message(self, chat_id: str, text: str) -> bool:
        """Send a text message to a chat.

        Args:
            chat_id: Chat ID to send to.
            text: Message text.

        Returns:
            True if sent successfully.
        """
        if not self._tenant_token:
            _LOGGER.error("No tenant token available")
            return False

        url = f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id"
        payload = json.dumps({
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._tenant_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    _LOGGER.debug("Message sent to %s", chat_id)
                    return True
                else:
                    _LOGGER.error("Feishu API error: %s", data.get("msg"))
                    return False
        except Exception as err:
            _LOGGER.error("Failed to send message: %s", err)
            return False

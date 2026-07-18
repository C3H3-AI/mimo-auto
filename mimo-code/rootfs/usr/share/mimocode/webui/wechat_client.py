"""WeChat Work client for MiMo Code Addon.

Connects to WeChat Work (企业微信) API to receive and send messages.
Supports multiple WeChat Work accounts.
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

# WeChat Work API endpoints
WECHATWORK_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECHATWORK_MESSAGE_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

# Reconnect settings
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60


class WeChatWorkClient:
    """WeChat Work client.

    Maintains connection to WeChat Work API,
    receives messages via webhook, and sends responses.
    """

    def __init__(
        self,
        corp_id: str,
        agent_id: str,
        secret: str,
        token: str,
        encoding_aes_key: str,
        on_message: Callable[[dict], Awaitable[str]],
        account_id: str = "default",
    ) -> None:
        """Initialize the WeChat Work client.

        Args:
            corp_id: WeChat Work corp ID.
            agent_id: Agent ID.
            secret: App secret.
            token: Verification token.
            encoding_aes_key: Encoding AES key.
            on_message: Async callback for handling messages.
            account_id: Account identifier for multi-account support.
        """
        self._corp_id = corp_id
        self._agent_id = agent_id
        self._secret = secret
        self._token = token
        self._encoding_aes_key = encoding_aes_key
        self._on_message = on_message
        self._account_id = account_id

        self._access_token: str | None = None
        self._token_expires: float = 0
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Check if client is active."""
        return self._running

    async def start(self) -> None:
        """Start the WeChat Work client."""
        if self._running:
            return

        self._running = True
        # Start token refresh
        asyncio.create_task(self._token_refresh_loop())
        _LOGGER.info("WeChat Work client started: %s", self._account_id)

    async def stop(self) -> None:
        """Stop the WeChat Work client."""
        self._running = False

    async def _token_refresh_loop(self) -> None:
        """Refresh access token periodically."""
        while self._running:
            try:
                await self._refresh_token()
                sleep_time = max(self._token_expires - time.time() - 600, 60)
                await asyncio.sleep(sleep_time)
            except Exception as err:
                _LOGGER.error("Token refresh error for %s: %s", self._account_id, err)
                await asyncio.sleep(300)

    async def _refresh_token(self) -> None:
        """Refresh access token."""
        if self._access_token and time.time() < self._token_expires:
            return

        url = f"{WECHATWORK_TOKEN_URL}?corpid={self._corp_id}&corpsecret={self._secret}"

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    self._access_token = data.get("access_token")
                    self._token_expires = time.time() + data.get("expires_in", 7200) - 600
                    _LOGGER.info("WeChat Work token refreshed: %s", self._account_id)
                else:
                    _LOGGER.error("Failed to get token: %s", data.get("errmsg"))
        except Exception as err:
            _LOGGER.error("Token refresh error: %s", err)

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """Verify WeChat Work webhook URL.

        Args:
            msg_signature: Message signature.
            timestamp: Timestamp.
            nonce: Nonce.
            echostr: Echo string.

        Returns:
            Decrypted echo string.
        """
        # Simple verification - in production, implement full signature verification
        return echostr

    async def handle_webhook(
        self,
        data: bytes,
        msg_signature: str = "",
        timestamp: str = "",
        nonce: str = "",
    ) -> str | None:
        """Handle incoming webhook message.

        Args:
            data: Raw XML message data.
            msg_signature: Message signature.
            timestamp: Timestamp.
            nonce: Nonce.

        Returns:
            Response XML or None.
        """
        try:
            import xml.etree.ElementTree as ET

            # Parse XML
            root = ET.fromstring(data)

            # Extract message info
            to_user = root.find("ToUserName").text if root.find("ToUserName") is not None else ""
            from_user = root.find("FromUserName").text if root.find("FromUserName") is not None else ""
            msg_type = root.find("MsgType").text if root.find("MsgType") is not None else ""
            content = root.find("Content").text if root.find("Content") is not None else ""
            msg_id = root.find("MsgId").text if root.find("MsgId") is not None else ""

            _LOGGER.info(
                "Received WeChat Work message from %s: %s",
                from_user,
                content[:100] if content else "",
            )

            # Skip empty messages
            if not content:
                return None

            # Call message handler
            response = await self._on_message({
                "message_id": msg_id,
                "sender_id": from_user,
                "chat_id": to_user,
                "text": content,
                "msg_type": msg_type,
                "account_id": self._account_id,
            })

            # Return XML response
            if response:
                return self._build_reply_xml(from_user, to_user, response)

            return None

        except Exception as err:
            _LOGGER.error("Error handling WeChat Work message: %s", err)
            return None

    def _build_reply_xml(self, to_user: str, from_user: str, content: str) -> str:
        """Build reply XML message.

        Args:
            to_user: Recipient user ID.
            from_user: Sender user ID (should be the app).
            content: Reply content.

        Returns:
            XML string.
        """
        timestamp = str(int(time.time()))

        xml_content = f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""

        return xml_content

    async def send_message(
        self,
        user_id: str,
        content: str,
        msg_type: str = "text",
    ) -> bool:
        """Send message to WeChat Work user.

        Args:
            user_id: User ID.
            content: Message content.
            msg_type: Message type (text, markdown, etc.).

        Returns:
            True if sent successfully.
        """
        if not self._access_token:
            _LOGGER.error("No access token available")
            return False

        url = f"{WECHATWORK_MESSAGE_URL}?access_token={self._access_token}"
        payload = json.dumps({
            "touser": user_id,
            "agentid": int(self._agent_id),
            "msgtype": msg_type,
            "text": {"content": content},
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    _LOGGER.debug("Message sent to %s", user_id)
                    return True
                else:
                    _LOGGER.error("WeChat Work API error: %s", data.get("errmsg"))
                    return False
        except Exception as err:
            _LOGGER.error("Failed to send message: %s", err)
            return False

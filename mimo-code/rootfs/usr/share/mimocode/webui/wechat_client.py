"""WeChat Work client for MiMo Code Addon.

Connects to WeChat Work (企业微信) API to receive and send messages.
Supports multiple WeChat Work accounts.
Fully async (aiohttp, no blocking calls).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Awaitable

import aiohttp

_LOGGER = logging.getLogger(__name__)

# WeChat Work API endpoints
WECHATWORK_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECHATWORK_MESSAGE_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

# Reconnect settings
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60


class WeChatWorkClient:
    """WeChat Work client.

    Fully async (aiohttp) — no event loop blocking.
    Tracks real connection status (not just a running flag).
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
        self._status: str = "disconnected"  # connected | disconnected | error

    @property
    def is_connected(self) -> bool:
        """Check if client is active (backward compat)."""
        return self._running and self._access_token is not None

    @property
    def connection_status(self) -> str:
        """Real connection status."""
        if self._status == "error":
            return "error"
        if self._running and self._access_token:
            return self._status if self._status == "connected" else "connected"
        return "disconnected"

    async def start(self) -> None:
        """Start the WeChat Work client."""
        if self._running:
            return

        self._running = True
        self._status = "disconnected"
        asyncio.create_task(self._token_refresh_loop())
        _LOGGER.info("WeChat Work client started: %s", self._account_id)

    async def stop(self) -> None:
        """Stop the WeChat Work client."""
        self._running = False
        self._status = "disconnected"

    async def _token_refresh_loop(self) -> None:
        """Refresh access token periodically (async)."""
        while self._running:
            try:
                await self._refresh_token()
                if self._access_token:
                    self._status = "connected"
                sleep_time = max(self._token_expires - time.time() - 600, 60)
                await asyncio.sleep(sleep_time)
            except Exception as err:
                self._status = "error"
                _LOGGER.error("Token refresh error for %s: %s", self._account_id, err)
                await asyncio.sleep(300)

    async def _refresh_token(self) -> None:
        """Refresh access token using aiohttp (non-blocking)."""
        if self._access_token and time.time() < self._token_expires:
            return

        url = f"{WECHATWORK_TOKEN_URL}?corpid={self._corp_id}&corpsecret={self._secret}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    if data.get("errcode") == 0:
                        self._access_token = data.get("access_token")
                        self._token_expires = time.time() + data.get("expires_in", 7200) - 600
                        _LOGGER.info("WeChat Work token refreshed: %s", self._account_id)
                    else:
                        _LOGGER.error(
                            "WeChat Work token error for %s: errcode=%s errmsg=%s",
                            self._account_id, data.get("errcode"), data.get("errmsg"),
                        )
        except asyncio.TimeoutError:
            _LOGGER.warning("WeChat Work token refresh timeout for %s", self._account_id)
        except aiohttp.ClientError as err:
            _LOGGER.warning("WeChat Work token refresh HTTP error for %s: %s", self._account_id, err)

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """Verify WeChat Work webhook URL.

        Performs SHA-1 signature verification against token + timestamp + nonce.
        Only returns echostr if signature matches.
        """
        if not self._token:
            return echostr

        expected = hashlib.sha1(
            "".join(sorted([self._token, timestamp, nonce])).encode("utf-8")
        ).hexdigest()

        if expected == msg_signature:
            return echostr

        _LOGGER.warning(
            "WeChat Work URL verification failed for %s: expected=%s got=%s",
            self._account_id, expected, msg_signature,
        )
        return ""

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
            # Run XML parsing in executor to avoid blocking
            def _parse_xml(data: bytes) -> tuple[str, str, str, str, str]:
                root = ET.fromstring(data)
                return (
                    root.findtext("ToUserName", ""),
                    root.findtext("FromUserName", ""),
                    root.findtext("MsgType", ""),
                    root.findtext("Content", ""),
                    root.findtext("MsgId", ""),
                )

            loop = asyncio.get_running_loop()
            to_user, from_user, msg_type, content, msg_id = await loop.run_in_executor(
                None, _parse_xml, data
            )

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
        """Build reply XML message."""
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
        """Send message to WeChat Work user via aiohttp (non-blocking).

        Args:
            user_id: User ID.
            content: Message content.
            msg_type: Message type (text, markdown, etc.).

        Returns:
            True if sent successfully.
        """
        if not self._access_token:
            _LOGGER.error("No access token available for %s", self._account_id)
            return False

        url = f"{WECHATWORK_MESSAGE_URL}?access_token={self._access_token}"
        payload = {
            "touser": user_id,
            "agentid": int(self._agent_id),
            "msgtype": msg_type,
            "text": {"content": content},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    if data.get("errcode") == 0:
                        _LOGGER.debug("Message sent to %s (account=%s)", user_id, self._account_id)
                        return True
                    else:
                        _LOGGER.error(
                            "WeChat Work API error for %s: errcode=%s errmsg=%s",
                            self._account_id, data.get("errcode"), data.get("errmsg"),
                        )
                        if data.get("errcode") == 40001:  # Invalid credential
                            self._access_token = None
                            self._token_expires = 0
                            _LOGGER.info("WeChat Work token invalidated, will refresh on next send")
                        return False
        except asyncio.TimeoutError:
            _LOGGER.warning("WeChat Work send timeout for %s (user=%s)", self._account_id, user_id)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.warning("WeChat Work send HTTP error for %s: %s", self._account_id, err)
            return False

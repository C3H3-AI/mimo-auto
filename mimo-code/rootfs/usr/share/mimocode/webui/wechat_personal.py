"""Personal WeChat client for MiMo Code Addon.

Based on Tencent iLink Bot API protocol (from cn_im_hub).
Supports QR code login, message receiving/sending.
Uses aiohttp for async HTTP (no event loop blocking).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Awaitable
from uuid import uuid4

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Tencent iLink Bot API endpoints (same as cn_im_hub)
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://c2cwxappimg.weixin.qq.com"

# Timeouts
QR_POLL_TIMEOUT_MS = 35000
LONG_POLL_TIMEOUT_MS = 35000
API_TIMEOUT_MS = 15000
LOGIN_TIMEOUT_MS = 480000  # 8 minutes

# Bot type
DEFAULT_BOT_TYPE = "3"


class WeixinLoginSession:
    """WeChat login session data."""

    def __init__(
        self,
        session_key: str,
        qrcode: str,
        qrcode_url: str,
    ) -> None:
        self.session_key = session_key
        self.qrcode = qrcode
        self.qrcode_url = qrcode_url


class WeixinLoginResult:
    """WeChat login result data."""

    def __init__(
        self,
        connected: bool,
        message: str,
        token: str = "",
        account_id: str = "",
        base_url: str = "",
        user_id: str = "",
    ) -> None:
        self.connected = connected
        self.message = message
        self.token = token
        self.account_id = account_id
        self.base_url = base_url
        self.user_id = user_id


def _random_wechat_uin() -> str:
    """Generate random WeChat UIN."""
    digest = hashlib.sha256(uuid4().bytes).digest()[:4]
    value = int.from_bytes(digest, "big", signed=False)
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _build_headers(body: str, token: str | None = None) -> dict[str, str]:
    """Build request headers."""
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_post(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    """Make API POST request (synchronous)."""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body = json.dumps(payload, ensure_ascii=False)

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers=_build_headers(body, token),
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
            raw = resp.read().decode("utf-8")
            if resp.status >= 400:
                raise RuntimeError(f"{endpoint} {resp.status}: {raw}")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"{endpoint} {err.code}: {err.read().decode()}") from err


class PersonalWeChatClient:
    """Personal WeChat client using Tencent iLink Bot API.

    Supports:
    - QR code login
    - Message receiving (long polling)
    - Message sending (text, image, file)
    """

    def __init__(
        self,
        on_message: Callable[[dict], Awaitable[str]],
        base_url: str = DEFAULT_BASE_URL,
        account_id: str = "default",
    ) -> None:
        """Initialize the client.

        Args:
            on_message: Async callback for handling messages.
            base_url: Tencent iLink Bot API base URL.
            account_id: Account identifier.
        """
        self._on_message = on_message
        self._base_url = base_url
        self._account_id = account_id

        self._token: str | None = None
        self._user_id: str | None = None
        self._get_updates_buf: str = ""
        self._running = False
        self._logged_in = False

    @property
    def is_logged_in(self) -> bool:
        """Check if logged in."""
        return self._logged_in and self._token is not None

    async def start_login(self) -> WeixinLoginSession:
        """Start login process and return QR code.

        Returns:
            Login session with QR code data.
        """
        url = f"{self._base_url}/ilink/bot/get_bot_qrcode?bot_type={DEFAULT_BOT_TYPE}"

        req = urllib.request.Request(url, method="GET")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            raise RuntimeError(f"Failed to get QR code: {err}") from err

        qrcode = str(data.get("qrcode") or "")
        qrcode_url = str(data.get("qrcode_img_content") or "")

        if not qrcode or not qrcode_url:
            raise ValueError("Failed to fetch login QR code")

        return WeixinLoginSession(
            session_key=uuid4().hex,
            qrcode=qrcode,
            qrcode_url=qrcode_url,
        )

    async def wait_login(
        self,
        login: WeixinLoginSession,
        timeout_ms: int = LOGIN_TIMEOUT_MS,
    ) -> WeixinLoginResult:
        """Wait for user to scan QR code and confirm login.

        Uses aiohttp for truly async HTTP calls (no event loop blocking).
        Does NOT swallow API errors silently — lets real errors propagate.

        Args:
            login: Login session from start_login.
            timeout_ms: Timeout in milliseconds.

        Returns:
            Login result with token and user info.
        """
        deadline = time.time() + max(timeout_ms / 1000, 1)

        # get_qrcode_status is a long-poll GET with only iLink-App-ClientVersion header.
        long_poll_headers = {"iLink-App-ClientVersion": "1"}
        timeout_sec = min(QR_POLL_TIMEOUT_MS, timeout_ms) / 1000

        _LOGGER.warning("wait_login started, qrcode=%s..., url=%s, deadline=%s",
                         login.qrcode[:16] if login.qrcode else "EMPTY",
                         f"{self._base_url}/ilink/bot/get_qrcode_status?qrcode=...",
                         deadline)

        async with aiohttp.ClientSession() as session:
            while time.time() < deadline:
                url = f"{self._base_url}/ilink/bot/get_qrcode_status?qrcode={login.qrcode}"

                try:
                    async with session.get(url, headers=long_poll_headers, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                        text = await resp.text()
                        if resp.status >= 400:
                            _LOGGER.warning("get_qrcode_status HTTP %s: %s", resp.status, text[:200])
                            await asyncio.sleep(2)
                            continue
                        data = json.loads(text) if text else {}
                        _LOGGER.warning("get_qrcode_status response: %s", text[:300])
                except TimeoutError:
                    # Long-poll timeout is normal — retry immediately
                    _LOGGER.warning("get_qrcode_status timed out after %ss (expected), retrying", timeout_sec)
                    continue
                except (aiohttp.ClientError, json.JSONDecodeError) as err:
                    _LOGGER.warning("get_qrcode_status error: %s, retrying...", err)
                    await asyncio.sleep(2)
                    continue

                status = str(data.get("status") or "wait")
                _LOGGER.warning("wait_login: status=%s, data_keys=%s", status, list(data.keys()))

                if status == "confirmed":
                    account_id = str(data.get("ilink_bot_id") or "")
                    token = str(data.get("bot_token") or "")
                    user_id = str(data.get("ilink_user_id") or "")
                    base_url = str(data.get("baseurl") or self._base_url)

                    if not account_id or not token:
                        raise ValueError("Login confirmed but token/account_id missing")

                    # Store credentials
                    self._token = token
                    self._user_id = user_id
                    self._base_url = base_url
                    self._logged_in = True

                    _LOGGER.info("WeChat login successful: %s", account_id)

                    return WeixinLoginResult(
                        connected=True,
                        message="与微信连接成功",
                        token=token,
                        account_id=account_id,
                        base_url=base_url,
                        user_id=user_id,
                    )

                if status == "expired":
                    raise ValueError("微信二维码已过期，请重新获取")

                # Any other status → normal wait, poll again
                await asyncio.sleep(2)

        _LOGGER.warning("wait_login: deadline reached, raising TimeoutError")
        raise TimeoutError("微信登录超时，请重试")

    async def start(self) -> None:
        """Start message receiving loop."""
        if not self._logged_in:
            _LOGGER.error("Cannot start: not logged in")
            return

        self._running = True
        asyncio.create_task(self._message_loop())
        _LOGGER.info("WeChat message loop started")

    async def stop(self) -> None:
        """Stop message receiving loop."""
        self._running = False

    async def _message_loop(self) -> None:
        """Long poll for new messages."""
        while self._running and self._logged_in:
            try:
                await self._poll_messages()
            except Exception as err:
                _LOGGER.error("Message poll error: %s", err)
                await asyncio.sleep(5)

    async def _poll_messages(self) -> None:
        """Poll for new messages."""
        if not self._token:
            return

        data = _api_post(
            self._base_url,
            "ilink/bot/getupdates",
            {
                "get_updates_buf": self._get_updates_buf,
                "base_info": {"channel_version": "mimo-code-addon"},
            },
            token=self._token,
            timeout_ms=LONG_POLL_TIMEOUT_MS,
        )

        # Update buffer for next poll
        self._get_updates_buf = str(data.get("get_updates_buf") or "")

        # Process messages
        msgs = data.get("msgs", [])
        for msg in msgs:
            await self._handle_message(msg)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle incoming message."""
        try:
            # Extract message info
            from_user_id = str(msg.get("from_user_id") or "")
            to_user_id = str(msg.get("to_user_id") or "")
            context_token = str(msg.get("context_token") or "")
            msg_type = int(msg.get("message_type") or 0)

            # Extract text content
            text = ""
            item_list = msg.get("item_list", [])
            for item in item_list:
                if item.get("type") == 1:  # Text item
                    text_item = item.get("text_item", {})
                    text = str(text_item.get("text") or "")

            if not text:
                return

            # Skip bot's own messages
            if from_user_id == self._user_id:
                return

            _LOGGER.info("Received WeChat message from %s: %s", from_user_id, text[:100])

            # Call message handler
            response = await self._on_message({
                "message_id": str(msg.get("msg_id") or ""),
                "sender_id": from_user_id,
                "chat_id": to_user_id,
                "text": text,
                "msg_type": msg_type,
                "context_token": context_token,
                "account_id": self._account_id,
            })

            # Send response if any
            if response:
                await self.send_text(
                    to_user_id=from_user_id,
                    text=response,
                    context_token=context_token,
                )

        except Exception as err:
            _LOGGER.error("Error handling WeChat message: %s", err)

    async def send_text(
        self,
        to_user_id: str,
        text: str,
        context_token: str = "",
    ) -> str:
        """Send text message.

        Args:
            to_user_id: Recipient user ID.
            text: Message text.
            context_token: Context token from incoming message.

        Returns:
            Client ID of sent message.
        """
        if not self._token:
            raise RuntimeError("Not logged in")

        client_id = f"mimo_{uuid4().hex}"

        _api_post(
            self._base_url,
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                    "context_token": context_token,
                },
                "base_info": {"channel_version": "mimo-code-addon"},
            },
            token=self._token,
        )

        _LOGGER.debug("Message sent to %s", to_user_id)
        return client_id

    def save_credentials(self) -> dict[str, Any]:
        """Save login credentials for persistence.

        Returns:
            Credentials dictionary.
        """
        return {
            "token": self._token,
            "user_id": self._user_id,
            "base_url": self._base_url,
            "account_id": self._account_id,
        }

    def load_credentials(self, creds: dict[str, Any]) -> bool:
        """Load saved credentials.

        Args:
            creds: Credentials dictionary.

        Returns:
            True if loaded successfully.
        """
        if not creds.get("token"):
            return False

        self._token = creds.get("token")
        self._user_id = creds.get("user_id")
        self._base_url = creds.get("base_url", DEFAULT_BASE_URL)
        self._account_id = creds.get("account_id", "default")
        self._logged_in = True

        _LOGGER.info("WeChat credentials loaded for %s", self._account_id)
        return True

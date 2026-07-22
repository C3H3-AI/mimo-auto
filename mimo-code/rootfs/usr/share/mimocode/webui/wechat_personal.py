"""Personal WeChat client for MiMo Code Addon.

Based on Tencent iLink Bot API protocol (from cn_im_hub).
Supports QR code login, message receiving/sending.
Fully async (no blocking calls).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
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

# Retry / failure handling (following cn_im_hub)
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_TOTAL_FAILURES = 8
_BACKOFF_DELAY = 30  # seconds
_RETRY_DELAY = 2
_SESSION_PAUSE_SECONDS = 3600  # 1 hour pause on session expiry
_SESSION_EXPIRED_ERRCODE = -14

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


async def _api_post(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout_ms: int = API_TIMEOUT_MS,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Make API POST request (async, non-blocking).

    Accepts optional reusable session (avoids creating new connection per call).
    """
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body = json.dumps(payload, ensure_ascii=False)
    headers = _build_headers(body, token)

    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)

    async def _do(s: aiohttp.ClientSession) -> dict[str, Any]:
        async with s.post(url, data=body.encode("utf-8"), headers=headers) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"{endpoint} {resp.status}: {raw}")
            return json.loads(raw) if raw else {}

    if session is not None:
        return await _do(session)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        return await _do(s)


class SessionExpiredError(RuntimeError):
    """Session token is expired/invalid. Should pause and wait for re-login."""
    pass


class PersonalWeChatClient:
    """Personal WeChat client using Tencent iLink Bot API.

    Supports:
    - QR code login (fully async, no blocking calls)
    - Message receiving (long polling with retry backoff)
    - Message sending (text)
    - Persistence callback for sync_buf
    - Session expiry detection
    - Typing indicator
    """

    def __init__(
        self,
        on_message: Callable[[dict], Awaitable[str]],
        base_url: str = DEFAULT_BASE_URL,
        account_id: str = "default",
        save_state_callback: Callable[[str], None] | None = None,
        show_reasoning: bool = False,
        mimo_serve_url: str | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            on_message: Async callback for handling messages.
            base_url: Tencent iLink Bot API base URL.
            account_id: Account identifier.
            save_state_callback: Called with new get_updates_buf after each poll.
            show_reasoning: If True, push AI reasoning as intermediate text messages.
            mimo_serve_url: URL for Mimo AI streaming (required if show_reasoning).
        """
        self._on_message = on_message
        self._base_url = base_url
        self._account_id = account_id
        self._save_state_callback = save_state_callback
        self._show_reasoning = show_reasoning
        self._mimo_url = mimo_serve_url

        self._token: str | None = None
        self._user_id: str | None = None
        self._get_updates_buf: str = ""
        self._running = False
        self._logged_in = False
        self._pause_until: float = 0.0
        self._status: str = "disconnected"  # connected | paused | session_expired | disconnected | error

    @property
    def is_logged_in(self) -> bool:
        """Check if logged in."""
        return self._logged_in and self._token is not None

    @property
    def connection_status(self) -> str:
        """Real connection status: connected, session_expired, paused, error, disconnected."""
        if self._pause_until > time.time():
            return "session_expired"
        if self._status == "error":
            return "error"
        if self._running and self._logged_in:
            return "connected"
        return self._status

    # -- Login -----------------------------------------------------------------

    async def start_login(self) -> WeixinLoginSession:
        """Start login process and return QR code (fully async)."""
        url = f"{self._base_url}/ilink/bot/get_bot_qrcode?bot_type={DEFAULT_BOT_TYPE}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"get_bot_qrcode {resp.status}: {raw}")
                data = json.loads(raw) if raw else {}

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

    def _remaining_pause(self) -> float:
        """Seconds remaining in pause (session expired hold)."""
        remaining = self._pause_until - time.time()
        return remaining if remaining > 0 else 0.0

    async def start(self) -> None:
        """Start message receiving loop."""
        if not self._logged_in:
            _LOGGER.error("Cannot start: not logged in")
            return

        self._running = True
        self._status = "connected"
        asyncio.create_task(self._message_loop())
        _LOGGER.info("WeChat message loop started")

    async def stop(self) -> None:
        """Stop message receiving loop."""
        self._running = False

    async def _message_loop(self) -> None:
        """Long poll for new messages with retry backoff + session expiry."""
        consecutive_failures = 0
        total_failures = 0
        next_timeout_ms = LONG_POLL_TIMEOUT_MS

        while self._running and self._logged_in and total_failures < _MAX_TOTAL_FAILURES:
            # Check if we're in a pause (session expired)
            remaining = self._remaining_pause()
            if remaining > 0:
                _LOGGER.info("WeChat session paused for %d more seconds", int(remaining))
                await asyncio.sleep(min(remaining, 60))
                continue

            try:
                data = await self._poll_messages(timeout_ms=next_timeout_ms)

                # Check for session expired
                errcode = self._extract_errcode(data)
                if errcode == _SESSION_EXPIRED_ERRCODE:
                    self._pause_until = time.time() + _SESSION_PAUSE_SECONDS
                    self._status = "session_expired"
                    _LOGGER.warning(
                        "WeChat session expired (account=%s), pausing for %d minutes",
                        self._account_id, _SESSION_PAUSE_SECONDS // 60,
                    )
                    consecutive_failures = 0
                    continue

                # Check for API errors
                if self._is_api_error(data):
                    consecutive_failures += 1
                    _LOGGER.warning(
                        "WeChat getupdates failed (%d/%d) account=%s ret=%s",
                        consecutive_failures, _MAX_CONSECUTIVE_FAILURES,
                        self._account_id, data.get("ret"),
                    )
                    delay = _BACKOFF_DELAY if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES else _RETRY_DELAY
                    await asyncio.sleep(delay)
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                    continue

                # Success: reset counters
                consecutive_failures = 0

                # Update timeout from server if provided
                if isinstance(data.get("longpolling_timeout_ms"), int) and data["longpolling_timeout_ms"] > 0:
                    next_timeout_ms = int(data["longpolling_timeout_ms"])

                # Persist sync_buf
                new_buf = str(data.get("get_updates_buf") or "")
                if new_buf and new_buf != self._get_updates_buf:
                    self._get_updates_buf = new_buf
                    if self._save_state_callback:
                        self._save_state_callback(new_buf)

                # Process messages
                for message in data.get("msgs") or []:
                    if not isinstance(message, dict):
                        continue
                    await self._handle_message(message)

            except SessionExpiredError:
                self._pause_until = time.time() + _SESSION_PAUSE_SECONDS
                self._status = "session_expired"
                _LOGGER.warning(
                    "WeChat session expired (account=%s), pausing for %d minutes",
                    self._account_id, _SESSION_PAUSE_SECONDS // 60,
                )
                consecutive_failures = 0

            except asyncio.CancelledError:
                raise

            except Exception as err:
                consecutive_failures += 1
                total_failures += 1
                _LOGGER.warning(
                    "WeChat poll error (attempt %d/%d, account=%s): %s",
                    total_failures, _MAX_TOTAL_FAILURES, self._account_id, err,
                )
                import traceback
                _LOGGER.warning("WeChat poll traceback: %s", traceback.format_exc())
                if total_failures >= _MAX_TOTAL_FAILURES:
                    self._status = "error"
                    _LOGGER.error(
                        "WeChat connection failed after %d attempts (account=%s)",
                        _MAX_TOTAL_FAILURES, self._account_id,
                    )
                    break
                delay = _BACKOFF_DELAY if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES else _RETRY_DELAY
                await asyncio.sleep(delay)

    async def _poll_messages(self, timeout_ms: int | None = None) -> dict[str, Any]:
        """Poll for new messages. Returns the API response dict.

        Long-poll timeout is normal (no messages) — return empty
        instead of raising (following cn_im_hub pattern).
        """
        if not self._token:
            _LOGGER.warning("WeChat _poll_messages: no token, skipping")
            return {"msgs": [], "get_updates_buf": self._get_updates_buf}

        _LOGGER.info(
            "WeChat polling %s/getupdates (buf=%s, timeout=%sms)",
            self._base_url, self._get_updates_buf[:16] if self._get_updates_buf else "empty",
            timeout_ms or LONG_POLL_TIMEOUT_MS,
        )

        try:
            result = await _api_post(
                self._base_url,
                "ilink/bot/getupdates",
                {
                    "get_updates_buf": self._get_updates_buf,
                    "base_info": {"channel_version": "mimo-code-addon"},
                },
                token=self._token,
                timeout_ms=timeout_ms or LONG_POLL_TIMEOUT_MS,
            )
        except TimeoutError:
            # Long-poll timeout is normal — no messages, don't count as failure
            _LOGGER.info("WeChat getupdates timeout (expected), retrying")
            return {"ret": 0, "msgs": [], "get_updates_buf": self._get_updates_buf}

        msg_count = len(result.get("msgs") or [])
        _LOGGER.info("WeChat poll result: %d msgs, errcode=%s, ret=%s",
                       msg_count, result.get("errcode"), result.get("ret"))
        return result

    @staticmethod
    def _extract_errcode(data: dict[str, Any]) -> int | None:
        for key in ("errcode", "ret"):
            val = data.get(key)
            if isinstance(val, int):
                return val
        return None

    @staticmethod
    def _is_api_error(data: dict[str, Any]) -> bool:
        errcode = data.get("errcode")
        ret = data.get("ret")
        return (isinstance(errcode, int) and errcode != 0) or (isinstance(ret, int) and ret != 0)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle incoming message with typing indicator."""
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

            # Skip the bot's own outgoing messages (from_user_id is the bot account)
            if from_user_id == self._account_id:
                return

            _LOGGER.info("Received WeChat message from %s: %s", from_user_id, text[:100])

            # Typing indicator: send "typing" status before processing
            asyncio.create_task(self._send_typing_indicator(from_user_id, context_token, status=1))

            if self._show_reasoning and self._mimo_url:
                # Streaming mode: push reasoning as it arrives, then send final response
                await self._handle_message_streaming(from_user_id, context_token, text)
            else:
                # Standard mode: call handler, get single response
                response = await self._on_message({
                    "message_id": str(msg.get("msg_id") or ""),
                    "sender_id": from_user_id,
                    "chat_id": to_user_id,
                    "text": text,
                    "msg_type": msg_type,
                    "context_token": context_token,
                    "account_id": self._account_id,
                })

                # Stop typing indicator
                asyncio.create_task(self._send_typing_indicator(from_user_id, context_token, status=2))

                # Send response if any
                if response:
                    await self.send_text(
                        to_user_id=from_user_id,
                        text=response,
                        context_token=context_token,
                    )

        except Exception as err:
            _LOGGER.error("Error handling WeChat message: %s", err)

    async def _handle_message_streaming(
        self, from_user_id: str, context_token: str, text: str,
    ) -> None:
        """Handle message with streaming (pushes reasoning as intermediate texts)."""
        try:
            from client import MimoAIClient

            # Create session for streaming
            key = f"wx_{self._account_id}_{uuid4().hex[:8]}"
            system = self._on_message  # Not used in streaming; _on_message called below
            session_id = f"ses_{uuid4().hex[:24]}"

            async with MimoAIClient(base_url=self._mimo_url or "http://127.0.0.1:14096") as ai:
                # Ensure session exists
                await ai.ensure_session(session_id)

                sent_reasoning = False
                events = await ai.send_message_stream(text, session_id)

                for event in events:
                    if event.get("type") == "reasoning" and not sent_reasoning:
                        r = event.get("text", "").strip()
                        if r:
                            await self.send_text(
                                to_user_id=from_user_id,
                                text=f"> 思考过程：\n\n{r}",
                                context_token=context_token,
                            )
                            sent_reasoning = True

                # Collect final response
                final = "\n".join(
                    e["text"] for e in events
                    if e.get("type") == "text" and e.get("text", "").strip()
                )

                if final:
                    await self.send_text(
                        to_user_id=from_user_id,
                        text=final,
                        context_token=context_token,
                    )

                # Clean up session
                try:
                    await ai.delete_session(session_id)
                except Exception:
                    pass

        except Exception as err:
            _LOGGER.error("Error in WeChat streaming handler: %s", err)
            # Fallback: try normal handler
            try:
                response = await self._on_message({
                    "sender_id": from_user_id,
                    "text": text,
                    "context_token": context_token,
                    "account_id": self._account_id,
                })
                if response:
                    await self.send_text(to_user_id=from_user_id, text=response, context_token=context_token)
            except Exception:
                pass

    async def _send_typing_indicator(self, to_user_id: str, context_token: str, status: int = 1) -> None:
        """Send typing indicator (1=typing, 2=stop). Swallows errors silently."""
        try:
            await _api_post(
                self._base_url,
                "ilink/bot/sendtyping",
                {
                    "ilink_user_id": to_user_id,
                    "typing_ticket": context_token,
                    "status": status,
                    "base_info": {"channel_version": "mimo-code-addon"},
                },
                token=self._token,
                timeout_ms=5000,
            )
        except Exception:
            _LOGGER.debug("Typing indicator ignored (non-critical)")

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

        await _api_post(
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
        """Save login credentials + poll state for persistence.

        Returns:
            Credentials dictionary including sync_buf.
        """
        return {
            "token": self._token,
            "user_id": self._user_id,
            "base_url": self._base_url,
            "account_id": self._account_id,
            "get_updates_buf": self._get_updates_buf,
        }

    def load_credentials(self, creds: dict[str, Any]) -> bool:
        """Load saved credentials + poll state.

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
        self._get_updates_buf = str(creds.get("get_updates_buf") or "")
        self._logged_in = True
        self._status = "disconnected"  # loaded but not yet polling

        _LOGGER.info("WeChat credentials loaded for %s", self._account_id)
        return True

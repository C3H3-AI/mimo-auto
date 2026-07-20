"""Feishu long-connection (WebSocket) client for MiMo Code Addon.

Uses the OFFICIAL lark-oapi SDK WebSocket long-connection (长连接) mode,
which requires NO public IP / domain — ideal for a home addon behind NAT.

Event flow:
  Feishu push (im.message.receive_v1)
    -> parse text
    -> call MiMo serve (sync HTTP to 127.0.0.1)
    -> reply via Feishu message API
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
import urllib.error
from typing import Any

_LOGGER = logging.getLogger(__name__)

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImMessageReceiveV1,
        CreateMessageRequest,
        CreateMessageRequestBody,
    )
    _HAS_LARK = True
    _IMPORT_ERR: Exception | None = None
except Exception as _err:  # pragma: no cover
    _HAS_LARK = False
    _IMPORT_ERR = _err
    _LOGGER.warning("lark-oapi not available: %s", _err)


class FeishuClient:
    """Feishu WebSocket long-connection client (self-contained)."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        mimo_serve_url: str = "http://127.0.0.1:14096",
        verification_token: str | None = None,
        encrypt_key: str | None = None,
    ) -> None:
        """Initialize the Feishu client.

        Args:
            app_id: Feishu app ID.
            app_secret: Feishu app secret.
            mimo_serve_url: URL of MiMo serve API (used to generate replies).
            verification_token: Optional verification token (long-connection not needed).
            encrypt_key: Optional encryption key (long-connection not needed).
        """
        self._app_id = app_id
        self._app_secret = app_secret
        self._mimo_serve_url = mimo_serve_url
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key

        self._running = False
        self._connected = False
        self._thread: threading.Thread | None = None
        self._cli = None
        self._api_client = None
        self._last_error: str | None = None

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        """Whether the WS long-connection is currently active."""
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start the WebSocket long-connection in a background daemon thread."""
        if self._running:
            return
        if not _HAS_LARK:
            self._last_error = f"lark-oapi 未安装: {_IMPORT_ERR}"
            _LOGGER.error(self._last_error)
            return
        if not self._app_id or not self._app_secret:
            self._last_error = "飞书 app_id / app_secret 缺失"
            _LOGGER.error(self._last_error)
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()
        _LOGGER.info("飞书客户端启动中 (WebSocket 长连接)")

    def stop(self) -> None:
        """Stop the client."""
        self._running = False
        try:
            if self._cli is not None and hasattr(self._cli, "stop"):
                self._cli.stop()
        except Exception as err:  # pragma: no cover
            _LOGGER.debug("飞书停止异常: %s", err)
        self._connected = False

    def _run_ws(self) -> None:
        try:
            handler = (
                lark.EventDispatcherHandler.builder(
                    self._encrypt_key or "",
                    self._verification_token or "",
                )
                .register_p2_im_message_receive_v1(self._on_message_event)
                .build()
            )
            self._cli = lark.ws.Client(
                self._app_id,
                self._app_secret,
                event_handler=handler,
                log_level=lark.LogLevel.WARNING,
            )
            self._connected = True
            self._last_error = None
            _LOGGER.info("飞书 WebSocket 已连接")
            self._cli.start()  # blocking, auto-reconnect
        except Exception as err:  # pragma: no cover
            self._connected = False
            self._last_error = str(err)
            _LOGGER.error("飞书 WebSocket 错误: %s", err)
        finally:
            self._connected = False

    # ------------------------------------------------------------------ #
    # Event handler (runs inside the SDK thread)
    # ------------------------------------------------------------------ #
    def _on_message_event(self, event: "P2ImMessageReceiveV1") -> None:
        try:
            ev = event.event
            msg = ev.message
            chat_type = getattr(msg, "chat_type", "p2p")  # "group" / "p2p"
            chat_id = getattr(msg, "chat_id", "")
            content = getattr(msg, "content", "{}")
            sender = ev.sender
            open_id = getattr(getattr(sender, "sender_id", None), "open_id", "")

            try:
                text = json.loads(content).get("text", "")
            except Exception:
                text = content or ""

            if not text:
                return

            _LOGGER.info("收到飞书消息 (%s): %s", open_id, text[:100])

            reply = self._call_mimo(text, user_id=open_id, conv_id=chat_id)
            if reply:
                self._reply(chat_type, chat_id, open_id, reply)
        except Exception as err:  # pragma: no cover
            _LOGGER.error("飞书事件处理出错: %s", err)

    # ------------------------------------------------------------------ #
    # MiMo serve (synchronous HTTP to 127.0.0.1)
    # ------------------------------------------------------------------ #
    def _call_mimo(self, text: str, user_id: str = "", conv_id: str = "") -> str:
        session_id = conv_id or user_id or "feishu-default"
        try:
            # create / get session
            try:
                req = urllib.request.Request(
                    f"{self._mimo_serve_url}/session",
                    data=json.dumps({"id": session_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    session_id = data.get("id", session_id)
            except Exception:
                pass

            # send message (NDJSON streaming response)
            msg_url = f"{self._mimo_serve_url}/session/{session_id}/message"
            payload = json.dumps({
                "message": text,
                "parts": [{"type": "text", "text": text}],
            }).encode("utf-8")
            req2 = urllib.request.Request(
                msg_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            collected: list[str] = []
            with urllib.request.urlopen(req2, timeout=180) as resp:
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
                        except json.JSONDecodeError:
                            break
                        if isinstance(obj, dict):
                            info = obj.get("info", {})
                            if info.get("role") == "assistant" and info.get("finish") == "stop":
                                for part in obj.get("parts", []):
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        t = part.get("text", "").strip()
                                        if t:
                                            collected.append(t)
            return "\n".join(collected)
        except Exception as err:  # pragma: no cover
            _LOGGER.error("调用 MiMo 出错: %s", err)
            return f"⚠️ 调用 MiMo 失败: {err}"

    # ------------------------------------------------------------------ #
    # Reply
    # ------------------------------------------------------------------ #
    def _reply(self, chat_type: str, chat_id: str, open_id: str, text: str) -> None:
        try:
            if self._api_client is None:
                self._api_client = (
                    lark.Client.builder()
                    .app_id(self._app_id)
                    .app_secret(self._app_secret)
                    .build()
                )
            if chat_type == "group":
                rid, rtype = chat_id, "chat_id"
            else:
                rid, rtype = open_id, "open_id"

            for piece in self._chunk(text):
                req = (
                    CreateMessageRequest.builder()
                    .receive_id_type(rtype)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(rid)
                        .msg_type("text")
                        .content(json.dumps({"text": piece}, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                self._api_client.im.v1.message.create(req)
        except Exception as err:  # pragma: no cover
            _LOGGER.error("飞书回复出错: %s", err)

    @staticmethod
    def _chunk(text: str, size: int = 2000) -> list[str]:
        if len(text) <= size:
            return [text]
        return [text[i : i + size] for i in range(0, len(text), size)]

    # ------------------------------------------------------------------ #
    # One-shot connectivity test (used by the WebUI "测试连接" button)
    # ------------------------------------------------------------------ #
    @classmethod
    def test_connection(
        cls, app_id: str, app_secret: str, timeout: float = 10.0
    ) -> dict[str, Any]:
        """Try to establish a WS long-connection for `timeout` seconds.

        Returns a dict with keys: success (bool), connected (bool), error (str|None).
        """
        if not _HAS_LARK:
            return {"success": False, "connected": False,
                    "error": f"lark-oapi 未安装: {_IMPORT_ERR}"}
        if not app_id or not app_secret:
            return {"success": False, "connected": False,
                    "error": "app_id / app_secret 不能为空"}

        probe = cls(app_id, app_secret, mimo_serve_url="http://127.0.0.1:9")
        probe.start()
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if probe.is_connected:
                probe.stop()
                return {"success": True, "connected": True, "error": None}
            if probe.last_error:
                # WS raised immediately (bad creds / not enabled)
                return {"success": False, "connected": False,
                        "error": probe.last_error}
            time.sleep(0.5)
        probe.stop()
        return {
            "success": False,
            "connected": False,
            "error": "连接超时（请确认飞书开放平台已开启「事件订阅-长连接」）",
        }

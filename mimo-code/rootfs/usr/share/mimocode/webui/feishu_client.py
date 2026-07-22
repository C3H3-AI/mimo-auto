"""Feishu long-connection (WebSocket) client for MiMo Code Addon.

Uses the lark-oapi SDK WebSocket long-connection mode.
Architecture (following cn_im_hub pattern):
  WS thread: receives event -> push to queue -> return immediately (never block)
  Worker thread: pull from queue -> call MiMo serve -> send reply via API
  Retry loop: on disconnect, retry up to 8 times with 5s pause
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
from client import MimoClientSync
from session_store import SessionStore
from collections import OrderedDict
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
except Exception as _err:
    _HAS_LARK = False
    _IMPORT_ERR = _err
    _LOGGER.warning("lark-oapi not available: %s", _err)


class FeishuClient:
    """Feishu WebSocket client with non-blocking WS thread."""

    _session_store = SessionStore()
    _model_name: str = "MiMo Code"
    _seen_message_ids: OrderedDict[str, None] = OrderedDict()
    _seen_limit = 512

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        mimo_serve_url: str = "http://127.0.0.1:14096",
        verification_token: str | None = None,
        encrypt_key: str | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._mimo_serve_url = mimo_serve_url
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key

        self._running = False
        self._stop_flag = False
        self._connected = False
        self._status = "disconnected"
        self._ws_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._cli = None
        self._api_client = None
        self._last_error: str | None = None
        self._msg_queue: queue.Queue = queue.Queue()
        self._mimo_client = MimoClientSync(base_url=mimo_serve_url)
        # Load persisted session IDs
        self._load_sessions()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------ #
    # Persistent session cache
    # ------------------------------------------------------------------ #
    @classmethod
    def _load_sessions(cls) -> None:
        """Sessions are now managed by SessionStore (loaded on init)."""
        pass

    @classmethod
    def _save_sessions(cls) -> None:
        """Sessions are now managed by SessionStore (saved on write)."""
        pass

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
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
        self._stop_flag = False
        self._status = "connecting"

        # Start worker thread (processes messages from queue, calls AI, sends replies)
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="feishu_worker")
        self._worker_thread.start()

        # Start WS thread (receives events, pushes to queue, never blocks)
        self._ws_thread = threading.Thread(target=self._run_ws_reconnect, daemon=True, name="feishu_ws")
        self._ws_thread.start()
        _LOGGER.info("飞书客户端启动中 (WebSocket 长连接)")

    def stop(self) -> None:
        self._stop_flag = True
        self._running = False
        try:
            if self._cli is not None and hasattr(self._cli, "stop"):
                self._cli.stop()
        except Exception:
            pass
        self._connected = False
        self._status = "disconnected"

    # ------------------------------------------------------------------ #
    # WS thread: connect + retry
    # ------------------------------------------------------------------ #
    def _run_ws_reconnect(self) -> None:
        """WS thread entry: retry loop like cn_im_hub."""
        max_retries = 8
        retry_count = 0

        while retry_count < max_retries and not self._stop_flag:
            try:
                self._run_ws_once()
                retry_count = 0  # success, reset counter
            except Exception as err:
                if self._stop_flag:
                    return
                retry_count += 1
                _LOGGER.warning("飞书 WS 错误 (尝试 %d/%d): %s", retry_count, max_retries, err)
                if retry_count >= max_retries:
                    _LOGGER.error("飞书连接失败 %d 次，停止重试", max_retries)
                    self._status = "error"
                    self._connected = False
                    return
            if self._stop_flag:
                return
            self._status = "disconnected"
            self._connected = False
            time.sleep(5)
            self._status = "connecting"

    def _run_ws_once(self) -> None:
        """Establish WS connection and start event loop (blocks until disconnect)."""
        # Thread isolation: clean lark_oapi.ws module cache
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("lark_oapi.ws"):
                del sys.modules[mod_name]

        import lark_oapi.ws.client as lark_ws_client_mod

        worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(worker_loop)
        lark_ws_client_mod.loop = worker_loop

        handler = (
            lark.EventDispatcherHandler.builder(
                self._encrypt_key or "",
                self._verification_token or "",
            )
            .register_p2_im_message_receive_v1(self._on_message_event)
            .register_p2_im_message_message_read_v1(self._on_ignored_typed)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self._on_ignored_typed)
            .register_p2_card_action_trigger(self._on_ignored_typed)
            .build()
        )
        self._cli = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        self._connected = True
        self._status = "connected"
        self._last_error = None
        _LOGGER.info("飞书 WebSocket 已连接")
        self._cli.start()  # blocking - returns when WS disconnects
        _LOGGER.info("飞书 WebSocket 已断开")
        self._connected = False
        self._status = "disconnected"
        try:
            worker_loop.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Event handlers (called from WS thread - MUST NOT block)
    # ------------------------------------------------------------------ #
    def _on_ignored_typed(self, *args, **kwargs) -> None:
        """No-op handler - WS thread returns immediately."""
        return

    def _on_message_event(self, event: "P2ImMessageReceiveV1") -> None:
        """Handle incoming message: extract text, push to queue, return immediately."""
        try:
            ev = event.event
            msg = ev.message
            chat_type = getattr(msg, "chat_type", "p2p")
            chat_id = getattr(msg, "chat_id", "")
            content = getattr(msg, "content", "{}")
            sender = ev.sender
            open_id = getattr(getattr(sender, "sender_id", None), "open_id", "")
            message_id = str(getattr(msg, "message_id", "") or "")

            # Dedup
            if message_id:
                if message_id in FeishuClient._seen_message_ids:
                    _LOGGER.debug("跳过重复消息 %s", message_id)
                    return
                FeishuClient._seen_message_ids[message_id] = None
                FeishuClient._seen_message_ids.move_to_end(message_id)
                if len(FeishuClient._seen_message_ids) > FeishuClient._seen_limit:
                    FeishuClient._seen_message_ids.popitem(last=False)

            try:
                text = json.loads(content).get("text", "")
            except Exception:
                text = content or ""

            if not text:
                return

            _LOGGER.info("收到飞书消息 (%s): %s", open_id, text[:100])

            # Push to queue - worker thread will process (non-blocking)
            self._msg_queue.put({
                "text": text,
                "chat_type": chat_type,
                "chat_id": chat_id,
                "open_id": open_id,
            })
        except Exception as err:
            _LOGGER.error("飞书事件处理出错: %s", err)

    # ------------------------------------------------------------------ #
    # Worker thread: process messages from queue
    # ------------------------------------------------------------------ #
    def _worker_loop(self) -> None:
        """Worker thread: pull messages from queue, call AI, send replies."""
        while self._running and not self._stop_flag:
            try:
                msg = self._msg_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                text = msg["text"]
                chat_type = msg["chat_type"]
                chat_id = msg["chat_id"]
                open_id = msg["open_id"]

                # Build reply closure for this message
                def reply_fn(reply_text: str, as_card: bool = False) -> None:
                    try:
                        self._reply(chat_type, chat_id, open_id, reply_text, as_card)
                    except Exception as e:
                        _LOGGER.error("飞书回复失败: %s", e)

                self._call_mimo(text, user_id=open_id, conv_id=chat_id, reply_fn=reply_fn)
            except Exception as err:
                _LOGGER.error("工作线程处理出错: %s", err)

    # ------------------------------------------------------------------ #
    # MiMo serve (synchronous HTTP to 127.0.0.1)
    # ------------------------------------------------------------------ #
    def _call_mimo(self, text: str, user_id: str = "", conv_id: str = "",
                   reply_fn: Any = None) -> str:
        session_id = FeishuClient._session_store.get_session_id(conv_id) or conv_id or user_id or "feishu-default"
        try:
            # Ensure session exists
            session_id = self._mimo_client.ensure_session(session_id)
            if conv_id and not FeishuClient._session_store.get_session_id(conv_id):
                FeishuClient._session_store.set_session_id(conv_id, session_id)

            # Send message via MimoClientSync (synchronous, runs in worker thread)
            sent_reasoning = False

            # Use send_message_stream to get reasoning events for push
            events = self._mimo_client.send_message_stream(text, session_id)

            # Push reasoning to user as it arrives
            for event in events:
                if event.get("type") == "reasoning" and not sent_reasoning:
                    r = event.get("text", "").strip()
                    if r and reply_fn:
                        reply_fn(f"> 思考过程：\n\n{r}")
                        sent_reasoning = True

            # Collect final text response
            final = "\n".join(
                e["text"] for e in events
                if e.get("type") == "text" and e.get("text", "").strip()
            )

            if final and reply_fn:
                reply_fn(final, as_card=True)
            return final
        except Exception as err:
            _LOGGER.error("调用 MiMo 出错: %s", err)
            err_msg = f"⚠️ 调用 MiMo 失败: {err}"
            if reply_fn:
                reply_fn(err_msg)
            return err_msg

    # ------------------------------------------------------------------ #
    # Reply (via lark-oapi REST API)
    # ------------------------------------------------------------------ #
    def _reply(self, chat_type: str, chat_id: str, open_id: str, text: str,
               as_card: bool = False) -> None:
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
                if as_card:
                    if FeishuClient._model_name == "MiMo Code":
                        try:
                            req = urllib.request.Request(
                                f"{self._mimo_serve_url}/config",
                                headers={"Accept": "application/json"},
                            )
                            with urllib.request.urlopen(req, timeout=3) as r:
                                cfg = json.loads(r.read().decode("utf-8"))
                                model = cfg.get("model", "") or ""
                                if model:
                                    FeishuClient._model_name = model
                        except Exception:
                            pass
                    content = json.dumps({
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "blue",
                            "title": {"content": FeishuClient._model_name, "tag": "plain_text"},
                        },
                        "elements": [
                            {"tag": "markdown", "content": piece},
                            {"tag": "hr"},
                            {"tag": "note", "elements": [
                                {"tag": "plain_text", "content": "来自 Home Assistant MiMo Code"}
                            ]},
                        ],
                    }, ensure_ascii=False)
                    msg_type = "interactive"
                else:
                    content = json.dumps({"text": piece}, ensure_ascii=False)
                    msg_type = "text"

                req = (
                    CreateMessageRequest.builder()
                    .receive_id_type(rtype)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(rid)
                        .msg_type(msg_type)
                        .content(content)
                        .build()
                    )
                    .build()
                )
                self._api_client.im.v1.message.create(req)
        except Exception as err:
            _LOGGER.error("飞书回复出错: %s", err)

    @staticmethod
    def _chunk(text: str, size: int = 2000) -> list[str]:
        if len(text) <= size:
            return [text]
        return [text[i: i + size] for i in range(0, len(text), size)]

    # ------------------------------------------------------------------ #
    # One-shot connectivity test
    # ------------------------------------------------------------------ #
    @classmethod
    def test_connection(
        cls, app_id: str, app_secret: str, timeout: float = 10.0
    ) -> dict[str, Any]:
        if not _HAS_LARK:
            return {"success": False, "connected": False,
                    "error": f"lark-oapi 未安装: {_IMPORT_ERR}"}
        if not app_id or not app_secret:
            return {"success": False, "connected": False,
                    "error": "app_id / app_secret 不能为空"}

        probe = cls(app_id, app_secret, mimo_serve_url="http://127.0.0.1:9")
        probe.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if probe.is_connected:
                probe.stop()
                return {"success": True, "connected": True, "error": None}
            if probe.last_error:
                return {"success": False, "connected": False,
                        "error": probe.last_error}
            time.sleep(0.5)
        probe.stop()
        return {
            "success": False,
            "connected": False,
            "error": "连接超时（请确认飞书开放平台已开启「事件订阅-长连接」）",
        }

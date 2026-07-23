"""Feishu long-connection (WebSocket) client for MiMo Code Addon.

Uses the lark-oapi SDK WebSocket long-connection mode.
Architecture (following cn_im_hub pattern):
  WS thread: receives event -> push to queue -> return immediately (never block)
  Worker thread: pull from queue -> call MiMo serve -> send reply via API
  Retry loop: on disconnect, retry up to 8 times with 5s pause
"""

from __future__ import annotations

import asyncio
import aiohttp
import json
import logging
import os
import queue
import sys
import threading
import time

from media import parse_reply_segments, TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment, CardSegment
from media_utils import resolve_media_source, download_url_source, compress_image, upload_feishu_image, upload_feishu_file
from card import parse_card_source, build_feishu_card
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

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        mimo_serve_url: str = "http://127.0.0.1:14096",
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        show_reasoning: bool = True,
        on_message: Any = None,
        channel_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._mimo_serve_url = mimo_serve_url
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key
        self._show_reasoning = show_reasoning
        self._on_message = on_message
        self._channel_loop = channel_loop

        # Instance-level state (not shared across instances)
        self._model_name: str = "MiMo Code"
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_limit = 512

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

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_status(self) -> dict[str, Any]:
        """Return channel status dict for the channel manager."""
        return {
            "type": "feishu",
            "account_id": "default",
            "connected": self._connected,
            "status": self._status,
            "error": self._last_error,
        }

    # ------------------------------------------------------------------ #
    # Persistent session cache
    # ------------------------------------------------------------------ #
    def _load_sessions(self) -> None:
        """Sessions are now managed by SessionStore (loaded on init)."""
        pass

    def _save_sessions(self) -> None:
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
            .register_p2_card_action_trigger(self._on_card_action)
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

    def _on_card_action(self, event) -> None:
        """Handle card button click: extract action, push to queue for AI processing."""
        try:
            ev = event.event
            action = ev.action
            value = getattr(action, "value", {}) or {}
            action_data = value.get("action", "")

            if not action_data:
                return

            # Extract chat context from the event
            operator = getattr(ev, "operator", None)
            open_id = getattr(getattr(operator, "open_id", None), "open_id", "") if operator else ""

            _LOGGER.info("Feishu card action: %s by %s", action_data, open_id)

            # Push card action as a message to the AI
            self._msg_queue.put({
                "text": action_data,
                "chat_type": "p2p",  # Card actions are always P2P
                "chat_id": "",
                "open_id": open_id,
                "source": "card_action",
            })
        except Exception as err:
            _LOGGER.error("飞书卡片回调处理出错: %s", err)

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
                if message_id in self._seen_message_ids:
                    _LOGGER.debug("跳过重复消息 %s", message_id)
                    return
                self._seen_message_ids[message_id] = None
                self._seen_message_ids.move_to_end(message_id)
                if len(self._seen_message_ids) > self._seen_limit:
                    self._seen_message_ids.popitem(last=False)

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
        """Worker thread: own event loop, pull messages from queue, call AI, send replies.

        Uses a single run_until_complete(self._worker_main()) so the task context
        is stable across all messages (required by Python 3.14+ where asyncio.timeout()
        inside aiohttp demands a persistent task context, not recreated per message).
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._worker_main())
        except Exception as err:
            _LOGGER.error("Worker main loop crashed: %s", err)
            import traceback
            _LOGGER.error("Worker traceback: %s", traceback.format_exc())
        finally:
            loop.close()

    async def _worker_main(self) -> None:
        """Async worker main loop (runs inside a single task context).

        Each message: call AI → send reply via Feishu API.
        """
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

                # reply_fn sends reply back to Feishu
                async def _reply_fn(reply_text: str, as_card: bool = False) -> str | None:
                    try:
                        return await self._reply(chat_type, chat_id, open_id, reply_text, as_card)
                    except Exception as e:
                        _LOGGER.error("飞书回复失败: %s", e)
                        return None

                await self._call_mimo_async(text, user_id=open_id, conv_id=chat_id, reply_fn=_reply_fn)
            except Exception as err:
                _LOGGER.error("工作线程处理出错: %s", err)

    # ------------------------------------------------------------------ #
    # MiMo serve (via channel_manager 统一路径)
    # ------------------------------------------------------------------ #
    async def _call_mimo_async(self, text: str, user_id: str = "", conv_id: str = "",
                    reply_fn: Any = None) -> str:
        """Call mimo serve via channel_manager (async version).

        Routes the AI call through the channel event loop to ensure all aiohttp
        calls share the same event loop (avoids cross-loop Future errors).
        """
        try:
            # Build message dict for channel_manager
            message = {
                "text": text,
                "sender_id": user_id,
                "chat_id": conv_id,
                "account_id": "feishu",
            }

            # Reasoning callback for real-time push with PATCH updates
            reasoning_msg_id = None
            last_reasoning = ""

            async def _on_reasoning(r: str) -> None:
                nonlocal reasoning_msg_id, last_reasoning
                if not r or r == last_reasoning:
                    return
                last_reasoning = r
                display_text = f"💭 {r}"
                if reasoning_msg_id is None:
                    reasoning_msg_id = await reply_fn(display_text) if reply_fn else None
                else:
                    try:
                        await self._update_message(reasoning_msg_id, display_text)
                    except Exception:
                        pass

            # Call channel_manager._handle_message on the channel event loop
            if self._channel_loop and self._channel_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._on_message(message, on_reasoning=_on_reasoning),
                    self._channel_loop,
                )
                response = await asyncio.wrap_future(future)
            else:
                # Fallback: direct call (same loop)
                response = await self._on_message(message, on_reasoning=_on_reasoning)

            # Send final response
            if response and reply_fn:
                from media import parse_reply_segments, TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment, CardSegment
                from card import parse_card_source, build_feishu_card
                segments = parse_reply_segments(response)
                for seg in segments:
                    if isinstance(seg, TextSegment):
                        await reply_fn(seg.text, as_card=True)
                    elif isinstance(seg, ImageSegment):
                        await self._send_feishu_image("p2p", conv_id, user_id, seg.source)
                    elif isinstance(seg, VoiceSegment):
                        await reply_fn(f"[语音] {seg.text}", as_card=True)
                    elif isinstance(seg, FileSegment):
                        await self._send_feishu_file("p2p", conv_id, user_id, seg.source)
                    elif isinstance(seg, VideoSegment):
                        await self._send_feishu_video("p2p", conv_id, user_id, seg.source)
                    elif isinstance(seg, GifSegment):
                        await self._send_feishu_image("p2p", conv_id, user_id, seg.source)
                    elif isinstance(seg, CardSegment):
                        card_spec = parse_card_source(seg.source)
                        if card_spec:
                            card = build_feishu_card(card_spec)
                            self._send_feishu_card("p2p", conv_id, user_id, card)
                        else:
                            await reply_fn(seg.source, as_card=True)

            return response or ""
        except Exception as err:
            _LOGGER.error("调用 MiMo 出错: %s", err)
            err_msg = f"⚠️ 调用 MiMo 失败: {err}"
            if reply_fn:
                await reply_fn(err_msg)
            return err_msg

    # ------------------------------------------------------------------ #
    # Reply (via lark-oapi REST API)
    # ------------------------------------------------------------------ #
    async def _reply(self, chat_type: str, chat_id: str, open_id: str, text: str,
               as_card: bool = False) -> str | None:
        """Send a message and return message_id for PATCH updates."""
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
                    if self._model_name == "MiMo Code":
                        try:
                            timeout = aiohttp.ClientTimeout(total=3)
                            async with aiohttp.ClientSession() as s:
                                async with s.get(
                                    f"{self._mimo_serve_url}/config",
                                    headers={"Accept": "application/json"},
                                    timeout=timeout,
                                ) as r:
                                    cfg = json.loads(await r.read())
                                    model = cfg.get("model", "") or ""
                                    if model:
                                        self._model_name = model
                        except Exception:
                            pass
                    content = json.dumps({
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "blue",
                            "title": {"content": self._model_name, "tag": "plain_text"},
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
                result = self._api_client.im.v1.message.create(req)
                # Return message_id for PATCH updates
                if result and hasattr(result, 'data') and result.data:
                    return getattr(result.data, 'message_id', None)
        except Exception as err:
            _LOGGER.error("飞书回复出错: %s", err)
        return None

    async def _update_message(self, message_id: str, text: str) -> None:
        """Update an existing message (PATCH) for typing effect."""
        try:
            if self._api_client is None:
                return
            import json as _json
            content = _json.dumps({"text": text}, ensure_ascii=False)
            # lark-oapi SDK does not support PATCH directly; use REST API.
            token = await self._get_tenant_token()
            if not token:
                return
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
            data = _json.dumps({"msg_type": "text", "content": content}).encode("utf-8")
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as s:
                async with s.patch(
                    url,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    timeout=timeout,
                ) as resp:
                    await resp.read()
        except Exception as err:
            _LOGGER.debug("飞书消息更新失败: %s", err)

    async def _get_tenant_token(self) -> str | None:
        """Get Feishu tenant access token."""
        try:
            import json as _json
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            data = _json.dumps({"app_id": self._app_id, "app_secret": self._app_secret}).encode("utf-8")
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                ) as resp:
                    result = _json.loads(await resp.read())
                    return result.get("tenant_access_token")
        except Exception:
            return None

    @staticmethod
    def _chunk(text: str, size: int = 2000) -> list[str]:
        if len(text) <= size:
            return [text]
        return [text[i: i + size] for i in range(0, len(text), size)]

    # ------------------------------------------------------------------ #
    # Media sending (via lark-oapi REST API)
    # ------------------------------------------------------------------ #
    async def _send_feishu_image(self, chat_type: str, chat_id: str, open_id: str, source: str) -> None:
        """Send an image to Feishu."""
        try:
            # Resolve media source
            data, filename = None, None
            if source.startswith(("http://", "https://")):
                data, filename = await download_url_source(source)
            else:
                result = resolve_media_source(source)
                if result:
                    data, filename = result

            if not data:
                _LOGGER.warning("Failed to resolve media source: %s", source)
                return

            # Compress if image
            if filename and any(filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                data = await compress_image(data)

            # Upload to Feishu and send
            image_key = await upload_feishu_image(self._app_id, self._app_secret, data)

            if image_key:
                self._send_feishu_image_msg(chat_type, chat_id, open_id, image_key)
            else:
                _LOGGER.warning("Feishu image upload failed for: %s", source)

        except Exception as err:
            _LOGGER.error("飞书图片发送失败: %s", err)

    def _send_feishu_image_msg(self, chat_type: str, chat_id: str, open_id: str, image_key: str) -> None:
        """Send an image message via Feishu API."""
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

            import json as _json
            content = _json.dumps({"image_key": image_key}, ensure_ascii=False)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(rtype)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(rid)
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            self._api_client.im.v1.message.create(req)
        except Exception as err:
            _LOGGER.error("飞书图片消息发送失败: %s", err)

    async def _send_feishu_file(self, chat_type: str, chat_id: str, open_id: str, source: str) -> None:
        """Send a file to Feishu."""
        try:
            data, filename = None, None
            if source.startswith(("http://", "https://")):
                data, filename = await download_url_source(source)
            else:
                result = resolve_media_source(source)
                if result:
                    data, filename = result

            if not data:
                _LOGGER.warning("Failed to resolve file source: %s", source)
                return

            file_key = await upload_feishu_file(self._app_id, self._app_secret, data, filename or "file")

            if file_key:
                self._send_feishu_file_msg(chat_type, chat_id, open_id, file_key)
            else:
                _LOGGER.warning("Feishu file upload failed for: %s", source)

        except Exception as err:
            _LOGGER.error("飞书文件发送失败: %s", err)

    def _send_feishu_file_msg(self, chat_type: str, chat_id: str, open_id: str, file_key: str) -> None:
        """Send a file message via Feishu API."""
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

            import json as _json
            content = _json.dumps({"file_key": file_key}, ensure_ascii=False)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(rtype)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(rid)
                    .msg_type("file")
                    .content(content)
                    .build()
                )
                .build()
            )
            self._api_client.im.v1.message.create(req)
        except Exception as err:
            _LOGGER.error("飞书文件消息发送失败: %s", err)

    async def _send_feishu_video(self, chat_type: str, chat_id: str, open_id: str, source: str) -> None:
        """Send a video to Feishu."""
        try:
            data, filename = None, None
            if source.startswith(("http://", "https://")):
                data, filename = await download_url_source(source)
            else:
                result = resolve_media_source(source)
                if result:
                    data, filename = result

            if not data:
                _LOGGER.warning("Failed to resolve video source: %s", source)
                return

            file_key = await upload_feishu_file(
                self._app_id, self._app_secret, data, filename or "video.mp4", file_type="mp4"
            )

            if file_key:
                self._send_feishu_video_msg(chat_type, chat_id, open_id, file_key)
            else:
                _LOGGER.warning("Feishu video upload failed for: %s", source)

        except Exception as err:
            _LOGGER.error("飞书视频发送失败: %s", err)

    def _send_feishu_video_msg(self, chat_type: str, chat_id: str, open_id: str, file_key: str) -> None:
        """Send a video message via Feishu API."""
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

            import json as _json
            content = _json.dumps({"file_key": file_key, "image_key": ""}, ensure_ascii=False)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(rtype)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(rid)
                    .msg_type("media")
                    .content(content)
                    .build()
                )
                .build()
            )
            self._api_client.im.v1.message.create(req)
        except Exception as err:
            _LOGGER.error("飞书视频消息发送失败: %s", err)

    def _send_feishu_card(self, chat_type: str, chat_id: str, open_id: str, card: dict) -> None:
        """Send an interactive card to Feishu."""
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

            import json as _json
            content = _json.dumps(card, ensure_ascii=False)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(rtype)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(rid)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            self._api_client.im.v1.message.create(req)
            _LOGGER.debug("Feishu card sent to %s", open_id)
        except Exception as err:
            _LOGGER.error("飞书卡片发送失败: %s", err)

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

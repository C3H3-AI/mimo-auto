"""Channel manager for MiMo Code Addon.

Manages multiple IM channel connections (Feishu, WeChat Work, Personal WeChat)
and routes messages to mimo serve. All channels run in the same asyncio event loop.
Supports multi-account per channel type.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from client import MimoAIClient, MimoAPIError
from session_manager import MimoSessionManager
from ha_context import get_ha_context_builder
from media import parse_reply_segments, TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment
from base_channel import build_system_prompt, is_rate_limit, RATE_LIMIT_MESSAGE
from evolution_review import get_evolution_review
from action_confirm import get_confirm_manager, _SAFE_TOOLS
from feishu_client import FeishuClient
from wechat_client import WeChatWorkClient
from wechat_personal import PersonalWeChatClient, DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "/data/mimocode/mimo.json"


class ChannelManager:
    """Manages IM channel connections.

    All channels run as asyncio tasks in a single event loop.
    Uses MimoSessionManager for session persistence.
    Supports multi-account (multiple WeChat/Feishu instances).
    """

    def __init__(
        self,
        config: dict[str, Any],
        mimo_serve_url: str = "http://127.0.0.1:14095",
    ) -> None:
        self._config = config
        self._mimo_serve_url = mimo_serve_url
        self._channels: dict[str, Any] = {}
        self._running = False
        self._mimo_client = MimoAIClient(base_url=mimo_serve_url)
        self._session_manager = MimoSessionManager()
        # Per-channel serialization: only one in-flight send per channel_key at
        # a time, to avoid self-inflicted 409 "Session is busy" from concurrent
        # messages hitting the same session.
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._send_locks_guard = asyncio.Lock()

    @classmethod
    def from_config_file(
        cls,
        config_path: str = DEFAULT_CONFIG_PATH,
        mimo_serve_url: str = "http://127.0.0.1:14095",
    ) -> "ChannelManager":
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
        """Start all enabled channels (single event loop)."""
        if self._running:
            return
        self._running = True
        channels_config = self._config.get("channels", {})

        # Start Feishu
        feishu_config = channels_config.get("feishu", {})
        if feishu_config.get("enabled", False):
            await self._start_feishu(feishu_config)

        # Start WeChat Work (multi-account: dict or list)
        wechat_config = channels_config.get("wechat", {})
        if isinstance(wechat_config, dict):
            if wechat_config.get("enabled", False):
                await self._start_wechat(wechat_config, account_id="default")
        elif isinstance(wechat_config, list):
            for account in wechat_config:
                if account.get("enabled", False):
                    account_id = account.get("id", f"wechat_{len(self._channels)}")
                    await self._start_wechat(account, account_id=account_id)

        # Start Personal WeChat (multi-account: dict or list)
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
        """Return status for each channel."""
        status: dict[str, Any] = {}
        for name, client in self._channels.items():
            if isinstance(client, dict):
                status[name] = {
                    "connected": client.get("status") == "connected",
                    "status": client.get("status", "pending"),
                }
            else:
                s = client.get_status() if hasattr(client, 'get_status') else {}
                status[name] = s if s else {"connected": False, "status": "unknown"}
        return status

    # ------------------------------------------------------------------ #
    # Channel starters (each returns channel with BaseChannel interface)
    # ------------------------------------------------------------------ #

    async def _start_feishu(self, config: dict[str, Any]) -> None:
        """Start Feishu channel."""
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
            show_reasoning=bool(config.get("show_reasoning", True)),
            on_message=self._handle_message,
            channel_loop=asyncio.get_event_loop(),
        )
        client.start()  # sync (lark-oapi WS SDK needs thread)
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
            _LOGGER.warning("WeChat Work channel missing config: %s", account_id)
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

        def _on_state_change(new_buf: str) -> None:
            asyncio.create_task(self._persist_sync_buf(new_buf))

        client = PersonalWeChatClient(
            on_message=self._handle_message,
            base_url=config.get("base_url") or DEFAULT_BASE_URL,
            account_id=account_id,
            save_state_callback=_on_state_change,
            show_reasoning=bool(config.get("show_reasoning", True)),
            mimo_serve_url=self._mimo_serve_url,
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
    # Persistence helpers
    # ------------------------------------------------------------------ #

    async def _persist_sync_buf(self, new_buf: str) -> None:
        import os
        try:
            loop = asyncio.get_event_loop()
            cfg = await loop.run_in_executor(None, self._read_config_file)
            ch_cfg = cfg.setdefault("channels", {})

            # Handle both dict and list formats for personal_wechat
            existing = ch_cfg.get("personal_wechat", {})
            if isinstance(existing, list):
                # Multi-account list format — update first account's buf or all
                # (caller should provide account_id context eventually)
                for acct in existing:
                    creds = acct.setdefault("credentials", {})
                    if "get_updates_buf" in creds or not any(
                        a.get("credentials", {}).get("get_updates_buf")
                        for a in existing
                    ):
                        creds["get_updates_buf"] = new_buf
                        break
                else:
                    # No account has get_updates_buf yet — update first enabled
                    for acct in existing:
                        if acct.get("enabled", False):
                            acct.setdefault("credentials", {})["get_updates_buf"] = new_buf
                            break
            elif isinstance(existing, dict):
                # Single dict format
                if "credentials" not in existing:
                    existing["credentials"] = {}
                existing["credentials"]["get_updates_buf"] = new_buf
            else:
                ch_cfg["personal_wechat"] = {"enabled": True, "credentials": {"get_updates_buf": new_buf}}

            await loop.run_in_executor(None, self._write_config_file, cfg)
        except Exception as e:
            _LOGGER.warning("Failed to persist sync_buf: %s", e)

    @staticmethod
    def _read_config_file() -> dict[str, Any]:
        try:
            with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"channels": {}}

    @staticmethod
    def _write_config_file(cfg: dict[str, Any]) -> None:
        import os
        os.makedirs(os.path.dirname(DEFAULT_CONFIG_PATH), exist_ok=True)
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Unified message handling (single event loop, single MimoAIClient)
    # ------------------------------------------------------------------ #

    async def _handle_message(self, message: dict[str, Any], on_reasoning: Any = None) -> str:
        """Handle incoming message from any channel (async).

        Checks for confirmation replies first, then routes to AI.
        """
        text = message.get("text", "")
        channel_key = message.get("channel_key", "")
        sender_id = message.get("sender_id", "")
        chat_id = message.get("chat_id", "")
        account_id = message.get("account_id", "")

        # Build channel_key if not already set
        if not channel_key:
            channel_key = f"{sender_id}:{chat_id}" if chat_id else sender_id or "default"

        # Check for confirmation reply
        confirm_result = self._check_confirmation_reply(text, channel_key)
        if confirm_result is not None:
            return confirm_result

        media_info = message.get("media_info", "")
        ai_input = f"{text}\n{media_info}".strip() if media_info else text

        _LOGGER.info(
            "Processing message from %s/%s: %s",
            account_id or "unknown", sender_id, text[:100],
        )

        return await self._call_mimo_serve(
            ai_input, channel_key, on_reasoning=on_reasoning,
            sender_id=sender_id, chat_id=chat_id, account_id=account_id,
        )

    def _check_confirmation_reply(self, text: str, channel_key: str) -> str | None:
        """Check if the text is a confirmation reply (确认/取消).

        Returns response text if matched, None otherwise.
        """
        from action_confirm import get_confirm_manager

        text_lower = text.strip().lower()
        if text_lower not in ("确认", "取消", "approve", "reject", "yes", "no", "y", "n"):
            return None

        confirm_mgr = get_confirm_manager()

        # Find the most recent pending confirmation for this channel_key
        # (sorted by creation time, newest first)
        pending_list = sorted(
            [(cid, p) for cid, p in confirm_mgr._pending.items()
             if p.channel_key == channel_key or channel_key.startswith(p.sender_id)],
            key=lambda x: x[1].created_at,
            reverse=True,
        )

        if not pending_list:
            return None

        confirm_id, pending = pending_list[0]
        approved = text_lower in ("确认", "approve", "yes", "y")

        confirm_mgr.resolve_confirmation(confirm_id, approved)

        if approved:
            return f"确认执行：{pending.description}"
        else:
            return f"已取消：{pending.description}"

    async def _get_send_lock(self, channel_key: str) -> "asyncio.Lock":
        """Return (creating if needed) the per-channel serialization lock.

        Guarantees only one in-flight mimo request per channel_key at a time,
        so concurrent messages on the same session cannot collide with 409.
        """
        async with self._send_locks_guard:
            if channel_key not in self._send_locks:
                self._send_locks[channel_key] = asyncio.Lock()
            return self._send_locks[channel_key]

    async def _call_mimo_serve(
        self,
        text: str,
        channel_key: str = "default",
        on_reasoning: Any = None,
        sender_id: str = "",
        chat_id: str = "",
        account_id: str = "",
    ) -> str:
        """Call mimo serve via unified session manager.

        Sends are serialized per channel_key so concurrent messages on the same
        session don't collide with 409. Any 409 that survives the retry/backoff
        is degraded to a friendly message (inviting wait / side-question /
        fork) instead of a raw error string.
        """
        try:
            send_lock = await self._get_send_lock(channel_key)
            async with send_lock:
                return await self._dispatch_to_mimo(
                    text, channel_key, on_reasoning,
                    sender_id, chat_id, account_id,
                )
        except MimoAPIError as err:
            if err.status == 409:
                _LOGGER.warning("Session still busy after retries: %s", err)
                return (
                    "⚠️ 主线正在处理上一条消息，请稍候再试；"
                    "也可在网页端发起「旁问」或「派生对话」。"
                )
            _LOGGER.error("mimo serve HTTP %d: %s", err.status, err)
            return f"出错了（HTTP {err.status}），请稍后重试。"
        except Exception as err:
            _LOGGER.error("Error calling mimo serve: %s", err)
            return "出错了，请稍后重试。"

    async def _dispatch_to_mimo(
        self,
        text: str,
        channel_key: str,
        on_reasoning: Any,
        sender_id: str,
        chat_id: str,
        account_id: str,
    ) -> str:
        """Send one message to mimo serve with 409/404 auto-recovery.

        MUST be called while holding the per-channel send lock. Returns the
        assistant's text response (after rate-limit check and tool-call flow).
        """
        # 1. Get or create session
        session_id = await self._session_manager.get_or_create_session(
            self._mimo_client, channel_key
        )

        # 2. Build system prompt
        ha_ctx = get_ha_context_builder()
        device_context = await ha_ctx.get_context()
        evolution = get_evolution_review()
        lessons_context = evolution.get_lessons_context()
        system_prompt = build_system_prompt(device_context)
        if lessons_context:
            system_prompt = f"{system_prompt}\n\n{lessons_context}"

        # 3. Send message with auto-retry on 409/404
        collected_text: list[str] = []
        collected_tool_calls: list[dict] = []

        async def _on_event(event: dict) -> None:
            if event.get("type") == "text":
                collected_text.append(event["text"])
            elif event.get("type") == "reasoning":
                if on_reasoning:
                    r = event.get("text", "").strip()
                    if r:
                        try:
                            await on_reasoning(r)
                        except Exception:
                            pass
            elif event.get("type") == "tool-call":
                collected_tool_calls.append(event)

        max_retries = 3
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                await self._mimo_client.send_message(
                    text, session_id, system=system_prompt, on_event=_on_event
                )
                break
            except MimoAPIError as send_err:
                last_err = send_err
                if send_err.status in (409, 404):
                    _LOGGER.warning(
                        "Session %s busy/lost (HTTP %d, attempt %d/%d), "
                        "force-creating new session",
                        session_id, send_err.status, attempt + 1, max_retries,
                    )
                    session_id = await self._session_manager.get_or_create_session(
                        self._mimo_client, channel_key, force_new=True
                    )
                    collected_text.clear()
                    collected_tool_calls.clear()
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
        else:
            # All retries exhausted -> let the caller degrade gracefully.
            if last_err is not None:
                raise last_err

        response = "\n".join(collected_text)
        _LOGGER.info(
            "mimo serve returned: %s (tool_calls=%d)",
            response[:100] if response else "(empty)",
            len(collected_tool_calls),
        )

        # 4. Check rate limit
        if response and is_rate_limit(response):
            _LOGGER.warning("MiMo rate-limited, returning fallback message")
            return RATE_LIMIT_MESSAGE

        # 5. Handle tool calls (confirmation flow)
        if collected_tool_calls:
            result = await self._handle_tool_calls(
                collected_tool_calls, channel_key, sender_id, chat_id, account_id
            )
            if result:
                return result
            # If no confirmation needed (safe tools), continue with text response

        # 6. Schedule evolution review (fire-and-forget)
        if response:
            evolution = get_evolution_review()
            asyncio.create_task(
                evolution.schedule_review(text, response, self._mimo_client, session_id)
            )

        return response or ""

    async def _handle_tool_calls(
        self,
        tool_calls: list[dict],
        channel_key: str,
        sender_id: str,
        chat_id: str,
        account_id: str,
    ) -> str | None:
        """Handle tool calls from AI response.

        For safe tools: execute immediately.
        For sensitive tools: send confirmation card and wait for user response.

        Returns: response text if handled, None if should continue with text response.
        """
        confirm_mgr = get_confirm_manager()
        results: list[str] = []

        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            tool_args = tc.get("args", {})

            # Safe tools: execute immediately without confirmation
            if tool_name in _SAFE_TOOLS:
                result = await confirm_mgr.execute_tool(tool_name, tool_args)
                if result.get("success"):
                    _LOGGER.info("Safe tool executed: %s", tool_name)
                else:
                    _LOGGER.warning("Safe tool failed: %s - %s", tool_name, result.get("error"))
                continue

            # Sensitive tools: build confirmation
            pending = confirm_mgr.build_confirmation(
                tool_name=tool_name,
                tool_args=tool_args,
                channel_key=channel_key,
                sender_id=sender_id,
                chat_id=chat_id,
                account_id=account_id,
            )

            if not pending:
                continue

            # Build confirmation text for the channel
            confirm_text = (
                f"需要你确认执行以下操作：\n\n"
                f"**{pending.description}**\n\n"
                f"回复「确认」执行，回复「取消」放弃（5分钟内有效）"
            )

            # Send confirmation via the channel
            await self._send_confirmation(channel_key, pending.confirm_id, confirm_text)

            # Wait for user response (with timeout)
            approved = await confirm_mgr.wait_for_confirmation(pending.confirm_id)

            if approved:
                result = await confirm_mgr.execute_tool(tool_name, tool_args)
                if result.get("success"):
                    results.append(f"已执行：{pending.description}")
                    _LOGGER.info("Tool executed after confirmation: %s", tool_name)
                else:
                    err_msg = result.get("error", "未知错误")
                    results.append(f"执行失败：{err_msg}")
                    _LOGGER.warning("Tool execution failed: %s - %s", tool_name, err_msg)
            else:
                results.append(f"已取消：{pending.description}")
                _LOGGER.info("Tool cancelled by user: %s", tool_name)

        if results:
            return "\n".join(results)
        return None

    async def _send_confirmation(
        self, channel_key: str, confirm_id: str, text: str
    ) -> None:
        """Send confirmation request to the user via the appropriate channel."""
        # Find the channel client for this channel_key
        for key, client in self._channels.items():
            if isinstance(client, dict):
                continue
            # Match by channel_key prefix or account_id
            if channel_key.startswith(key) or key in channel_key:
                try:
                    if hasattr(client, 'send_confirmation'):
                        await client.send_confirmation(confirm_id, text)
                    else:
                        # Fallback: send as regular text
                        await self._send_text_fallback(client, text)
                except Exception as err:
                    _LOGGER.error("Failed to send confirmation: %s", err)
                return

        _LOGGER.warning("No channel found for confirmation: %s", channel_key)

    async def _send_text_fallback(self, client: Any, text: str) -> None:
        """Send confirmation as plain text (fallback for channels without card support)."""
        if hasattr(client, 'send_text'):
            await client.send_text(text)

    @property
    def is_running(self) -> bool:
        return self._running and len(self._channels) > 0

    @property
    def channels(self) -> dict[str, Any]:
        return self._channels.copy()

    @property
    def session_manager(self) -> MimoSessionManager:
        return self._session_manager

    def get_pending_logins(self) -> dict[str, Any]:
        """Get channels pending QR code login (for multi-account support)."""
        pending = {}
        for key, value in self._channels.items():
            if isinstance(value, dict) and value.get("status") == "pending_login":
                pending[key] = value
        return pending

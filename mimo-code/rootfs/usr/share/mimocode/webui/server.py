#!/usr/bin/env python3
"""MiMo Code Web UI - serves SPA and proxies all API calls to the mimo server."""
import asyncio
import http.server
import json
import logging
import os
import sys
import urllib.request
import urllib.error
import re
import shutil
from socketserver import ThreadingMixIn

_LOGGER = logging.getLogger(__name__)

MIMO_PORT = int(os.environ.get("MIMO_PORT", "14096"))
MIMO_API_BASE = f"http://127.0.0.1:{MIMO_PORT}"
WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(WEBUI_DIR, "dist")
PORT = int(os.environ.get("WEBUI_PORT", "8099"))
MIMO_WORKDIR = os.environ.get("MIMO_WORKDIR", "/data/mimocode")
# Config (incl. WeChat login credentials) MUST live in the persistent volume,
# never in the code dir (which is wiped on `ha addons update`).
CONFIG_FILE = os.environ.get("MIMO_CONFIG", os.path.join(MIMO_WORKDIR, "mimo.json"))
# Migrate a legacy config left in the (ephemeral) code dir so existing WeChat
# login credentials survive the move to the persistent volume.
_legacy_config = os.path.join(WEBUI_DIR, "mimo.json")
if not os.path.exists(CONFIG_FILE) and os.path.exists(_legacy_config):
    try:
        os.makedirs(MIMO_WORKDIR, exist_ok=True)
        shutil.copy2(_legacy_config, CONFIG_FILE)
        _LOGGER.info("Migrated legacy config %s -> %s", _legacy_config, CONFIG_FILE)
    except Exception as e:  # pragma: no cover - best effort migration
        _LOGGER.warning("Failed to migrate legacy config: %s", e)


def _build_config_from_env():
    """Build channel config from addon environment variables."""
    config = {"channels": {}}

    # Feishu
    feishu_enabled = os.environ.get("FEISHU_ENABLED", "false").lower() == "true"
    if feishu_enabled:
        config["channels"]["feishu"] = {
            "enabled": True,
            "app_id": os.environ.get("FEISHU_APP_ID", ""),
            "app_secret": os.environ.get("FEISHU_APP_SECRET", ""),
        }

    # WeChat Work
    wechat_enabled = os.environ.get("WECHAT_ENABLED", "false").lower() == "true"
    if wechat_enabled:
        config["channels"]["wechat"] = {
            "enabled": True,
            "corp_id": os.environ.get("WECHAT_CORP_ID", ""),
            "agent_id": os.environ.get("WECHAT_AGENT_ID", ""),
            "secret": os.environ.get("WECHAT_SECRET", ""),
            "token": os.environ.get("WECHAT_TOKEN", ""),
            "encoding_aes_key": os.environ.get("WECHAT_ENCODING_AES_KEY", ""),
        }

    # Personal WeChat
    personal_wechat_enabled = os.environ.get("PERSONAL_WECHAT_ENABLED", "false").lower() == "true"
    if personal_wechat_enabled:
        config["channels"]["personal_wechat"] = {
            "enabled": True,
        }

    # HA-MCP URL (from addon options)
    ha_mcp_url = os.environ.get("HA_MCP_URL", "")
    if ha_mcp_url:
        config["ha_mcp_url"] = ha_mcp_url

    return config


def _load_config():
    """Load config from file, falling back to env vars."""
    config = {}

    # Try to load from file first
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        pass

    # Merge with env vars: env fields override file, EXCEPT "credentials"
    # (preserves personal_wechat token saved during login)
    # If the stored config uses list format (multi-account), skip env merge entirely
    # for that channel — list mode means the user has already switched to multi-account.
    env_config = _build_config_from_env()
    if env_config.get("channels"):
        if "channels" not in config:
            config["channels"] = {}
        for ch_key, ch_val in env_config["channels"].items():
            existing = config["channels"].get(ch_key)
            if existing is None:
                config["channels"][ch_key] = ch_val
            elif isinstance(existing, list):
                # Multi-account mode: preserve list config, ignore env single-entry
                _LOGGER.info("Skipping env merge for %s: using multi-account list config", ch_key)
                continue
            elif isinstance(ch_val, dict) and isinstance(existing, dict):
                for k, v in ch_val.items():
                    if k == "credentials":
                        continue  # NEVER overwrite saved credentials from env
                    existing[k] = v

    # Save merged config
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write(f"[MiMo WebUI] Failed to save config: {e}\n")

    return config


# Channel manager instance
_channel_manager = None
# The event loop that drives the channel manager (kept alive forever)
_channel_manager_loop = None


def _get_channel_manager():
    """Get or create the channel manager instance."""
    global _channel_manager
    if _channel_manager is None:
        try:
            # Add current directory and data volume to path for imports
            # (data volume persists across restarts so hot-deployed modules are found)
            sys.path.insert(0, WEBUI_DIR)
            DATA_WEBUI = "/data/mimocode/webui"
            if os.path.isdir(DATA_WEBUI):
                sys.path.insert(0, DATA_WEBUI)
            from channel_manager import ChannelManager
            config = _load_config()
            _channel_manager = ChannelManager(
                config=config,
                mimo_serve_url=MIMO_API_BASE,
            )
        except ImportError as e:
            sys.stderr.write(f"[MiMo WebUI] Channel manager not available: {e}\n")
    return _channel_manager


def _reload_channels(config: dict) -> bool:
    """Reload the channel manager with a new config (runtime, no restart)."""
    global _channel_manager, _channel_manager_loop
    manager = _get_channel_manager()
    if manager is None or _channel_manager_loop is None:
        return False
    try:
        asyncio.run_coroutine_threadsafe(manager.reload(config), _channel_manager_loop)
        return True
    except Exception as e:
        sys.stderr.write(f"[MiMo WebUI] Reload error: {e}\n")
        return False


# _start_channels() is no longer used; channel init moved inline in __main__


class ThreadingMiMoServer(ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTP server that handles each request in a separate thread."""
    allow_reuse_address = True
    daemon_threads = True


class MiMoProxyHandler(http.server.SimpleHTTPRequestHandler):
    # HA ingress gateway IPs + localhost
    INGRESS_ALLOW = {"172.30.32.2", "127.0.0.1", "::1"}

    def __init__(self, *args, **kwargs):
        static_dir = DIST_DIR if os.path.isdir(DIST_DIR) else WEBUI_DIR
        super().__init__(*args, directory=static_dir, **kwargs)

    def _check_ingress(self) -> bool:
        """Reject requests not from HA ingress or localhost."""
        ip = self.client_address[0]
        if ip not in self.INGRESS_ALLOW:
            self.send_error(403, "Direct access denied; use HA ingress")
            return False
        return True

    def end_headers(self):
        try:
            # Aggressive no-cache for HTML to prevent stale SPA
            if hasattr(self, 'path') and self.path in ('/', '/index.html'):
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            else:
                self.send_header("Cache-Control", "no-cache, must-revalidate")
            super().end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            if not self._check_ingress():
                return
            # Health check endpoint for Supervisor (only /healthcheck, not /)
            if self.path == "/healthcheck":
                # Check if mimo serve is running
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{MIMO_PORT}/session",
                        method="GET",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status == 200:
                            self.send_response(200)
                            self.send_header("Content-Type", "text/plain")
                            self.end_headers()
                            self.wfile.write(b"OK")
                            return
                except Exception:
                    pass

            # ---- Channel management REST (served locally, not proxied) ----
            if self.path == "/api/channels/status":
                self._handle_channels_status()
                return
            if self.path == "/api/channels":
                self._handle_channels_get()
                return
            if self.path == "/api/accounts":
                self._handle_accounts_list()
                return

            # ---- Filesystem API (bypasses mimo serve's dir restriction) ----
            if self.path.startswith("/api/fs/list"):
                self._handle_fs_list()
                return
            if self.path.startswith("/api/fs/read"):
                self._handle_fs_read()
                return
            # NOTE: /api/fs/write is NOT routed here — writes only via PUT

            # ---- Multi-account management page ----
            if self.path in ("/accounts", "/accounts/"):
                self._serve_accounts_page()
                return

            # ---- Native TUI panel (currently unavailable) ----
            if self.path in ("/native-panel", "/native-panel/"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>MiMo Code</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f5f5f5;font-family:system-ui,sans-serif;padding:32px;max-width:600px;margin:0 auto}
h2{font-size:16px;margin-bottom:16px;color:#333}
p{font-size:13px;line-height:1.6;color:#555;margin-bottom:12px}
a{color:#1976d2;text-decoration:none}
.box{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px;margin-bottom:16px}
</style>
</head>
<body>
<h2>MiMo Code</h2>
<div class="box">
<p>请使用侧边栏 MiMo Code 面板或通过飞书/微信进行对话。</p>
<p><a href="/">← 返回主面板</a></p>
</div>
</body>
</html>""".encode("utf-8"))
                return

            if self.path.startswith("/api/"):
                self._proxy_request("GET")
            else:
                # Try to serve the file directly
                # Strip query string for file check
                file_path = self.path.split("?")[0].split("#")[0]
                if file_path == "/" or file_path == "":
                    # Root path -> serve index.html
                    super().do_GET()
                else:
                    # Check if the file exists in the static directory
                    local_path = file_path.lstrip("/")
                    full_path = os.path.join(self.directory, local_path)
                    if os.path.isfile(full_path):
                        super().do_GET()
                    else:
                        # SPA fallback: serve index.html for client-side routing
                        self.path = "/index.html"
                        super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            if not self._check_ingress():
                return
            if self.path == "/api/channels":
                self._handle_channels_post()
            elif self.path == "/api/channels/restart":
                self._handle_channels_restart()
            elif self.path == "/api/feishu/test":
                self._handle_feishu_test()
            elif self.path.startswith("/api/wechat/login/status"):
                self._handle_wechat_login_status()
            elif self.path.startswith("/api/wechat/login"):
                self._handle_wechat_login()
            elif self.path.startswith("/api/accounts/"):
                self._handle_accounts_post()
            elif self.path.startswith("/api/"):
                self._proxy_request("POST")
            else:
                self.send_error(405)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self):
        try:
            if not self._check_ingress():
                return
            if self.path.startswith("/api/accounts/"):
                self._handle_accounts_delete()
            elif self.path.startswith("/api/"):
                self._proxy_request("DELETE")
            else:
                self.send_error(405)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_PUT(self):
        try:
            if not self._check_ingress():
                return
            if self.path.startswith("/api/accounts/"):
                self._handle_accounts_put()
            elif self.path.startswith("/api/fs/write"):
                self._handle_fs_write()
            elif self.path.startswith("/api/"):
                self._proxy_request("PUT")
            else:
                self.send_error(405)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_PATCH(self):
        try:
            if self.path.startswith("/api/"):
                self._proxy_request("PATCH")
            else:
                self.send_error(405)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed = "null"
        if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
            allowed = origin
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _proxy_request(self, method):
        """Proxy request to the mimo serve API, stripping /api prefix."""
        target_path = self.path[4:]  # Strip /api prefix

        # Bug 3 fix: WebUI sends POST /api/config, but mimo serve expects PATCH /config
        if method == "POST" and target_path == "/config":
            method = "PATCH"

        target_url = f"{MIMO_API_BASE}{target_path}"

        # Special handling for streaming message responses
        is_stream = method == "POST" and re.match(r"^/session/[^/]+/message$", target_path)

        body = None
        content_length = self.headers.get("Content-Length")
        if content_length:
            body = self.rfile.read(int(content_length))

        req = urllib.request.Request(
            target_url, data=body, method=method,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Accept": "application/json, application/x-ndjson",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")

                if is_stream and "ndjson" in content_type:
                    # Bug 2 fix: stream NDJSON back chunk by chunk instead of buffering all
                    origin = self.headers.get("Origin", "")
                    allowed = "null"
                    if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
                        allowed = origin
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Access-Control-Allow-Origin", allowed)
                    self.end_headers()
                    try:
                        while True:
                            chunk = resp.read(8192)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    # Read full response body
                    data = resp.read()

                    # Bug 4 fix: smarter provider filtering — keep connected providers
                    # plus any provider whose id contains "mimo", "mi", or "xiaomi"
                    if target_path == "/provider":
                        try:
                            raw = json.loads(data)
                            if isinstance(raw, dict) and "all" in raw:
                                all_providers = raw["all"]
                                connected = raw.get("connected", [])
                                connected_set = set()
                                if isinstance(connected, list):
                                    connected_set.update(connected)

                                filtered = []
                                for p in all_providers:
                                    pid = p.get("id", "").lower()
                                    # Keep if: connected, or id matches mimo/mi/xiaomi
                                    if (pid in connected_set or
                                        p.get("id") in connected_set or
                                        p.get("connected") or
                                        "mimo" in pid or
                                        "xiaomi" in pid or
                                        pid == "mi" or
                                        pid.startswith("mi-")):
                                        filtered.append(p)

                                raw["all"] = filtered
                                data = json.dumps(raw, ensure_ascii=False).encode()
                        except (json.JSONDecodeError, Exception):
                            pass

                    origin = self.headers.get("Origin", "")
                    allowed = "null"
                    if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
                        allowed = origin
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", allowed)
                    self.end_headers()
                    self.wfile.write(data)

        except urllib.error.HTTPError as e:
            origin = self.headers.get("Origin", "")
            allowed = "null"
            if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
                allowed = origin
            self.send_response(e.code)
            data = e.read()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
        except urllib.error.URLError as e:
            # Fallback: try mimo run directly for chat
            if is_stream:
                self._handle_chat_via_mimo(body)
            else:
                self.send_error(502, f"Proxy error: {e.reason}")

    def _handle_chat_via_mimo(self, body):
        """Fallback: run mimo subprocess to handle chat when API is unavailable."""
        import subprocess
        text = ""
        if body:
            try:
                req_data = json.loads(body)
                text = req_data.get("message", "")
            except (json.JSONDecodeError, Exception):
                text = body.decode("utf-8", errors="replace")

        if not text:
            result = json.dumps({"role": "assistant", "content": "Empty message"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            try:
                self.wfile.write(result.encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        try:
            os.makedirs(MIMO_WORKDIR, exist_ok=True)
            result = subprocess.run(
                ["/usr/local/bin/mimo", "run", "--json", text],
                cwd=MIMO_WORKDIR, capture_output=True, text=True, timeout=180,
            )
            response = json.dumps({
                "role": "assistant",
                "content": result.stdout.strip() or result.stderr.strip() or "No response",
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response.encode())
        except subprocess.TimeoutExpired:
            self.send_error(504, "AI request timed out")
        except Exception as e:
            self.send_error(500, f"Chat error: {str(e)}")

    def log_message(self, format, *args):
        sys.stderr.write(f"[MiMo WebUI] {self.client_address[0]} - {format % args}\n")


# Login state storage
_login_states = {}


def _handle_wechat_login(self):
    """Handle WeChat login request - start QR code login flow."""
    try:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        data = json.loads(body) if body else {}

        # Import and use personal WeChat client
        sys.path.insert(0, WEBUI_DIR)
        from wechat_personal import PersonalWeChatClient

        # Create client and start login (store for reuse in status check)
        client = PersonalWeChatClient(
            on_message=lambda msg: asyncio.sleep(0),  # Dummy handler
        )

        async def do_login():
            login_session = await client.start_login()
            return login_session

        # Run async login
        loop = asyncio.new_event_loop()
        try:
            login_session = loop.run_until_complete(do_login())
        finally:
            loop.close()

        # Store login state (with the same client for status check reuse)
        session_key = login_session.session_key
        _login_states[session_key] = {
            "qrcode": login_session.qrcode,
            "qrcode_url": login_session.qrcode_url,
            "status": "waiting",
            "client": client,
        }

        # Return QR code to frontend
        response = json.dumps({
            "session_key": session_key,
            "qrcode": login_session.qrcode,
            "qrcode_url": login_session.qrcode_url,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())

    except Exception as e:
        sys.stderr.write(f"[MiMo WebUI] WeChat login error: {e}\n")
        response = json.dumps({"error": str(e)})
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())


def _handle_wechat_login_status(self):
    """Handle WeChat login status check."""
    try:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        data = json.loads(body) if body else {}

        session_key = data.get("session_key", "")
        if not session_key or session_key not in _login_states:
            response = json.dumps({"status": "error", "message": "Invalid session"})
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response.encode())
            return

        login_state = _login_states[session_key]
        client = login_state.get("client")

        # If no stored client, create one (shouldn't happen in normal flow)
        if client is None:
            sys.path.insert(0, WEBUI_DIR)
            from wechat_personal import PersonalWeChatClient
            client = PersonalWeChatClient(
                on_message=lambda msg: asyncio.sleep(0),
                base_url="https://ilinkai.weixin.qq.com",
            )

        # Check login status by polling
        async def check_status():
            result = await client.wait_login(
                type("LoginSession", (), {
                    "session_key": session_key,
                    "qrcode": login_state["qrcode"],
                    "qrcode_url": login_state["qrcode_url"],
                })(),
                timeout_ms=480000,
            )
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(check_status())
            if result.connected:
                login_state["status"] = "success"
                login_state["token"] = result.token
                login_state["account_id"] = result.account_id
                login_state["base_url"] = result.base_url
                login_state["user_id"] = result.user_id

                # Propagate credentials to channel manager so it can start polling messages
                mgr = _get_channel_manager()
                if mgr and _channel_manager_loop:
                    for key, ch_info in list(mgr._channels.items()):
                        if isinstance(ch_info, dict) and ch_info.get("status") == "pending_login" and "client" in ch_info:
                            client = ch_info["client"]
                            client.load_credentials({
                                "token": result.token,
                                "user_id": result.user_id,
                                "base_url": result.base_url,
                                "account_id": result.account_id or "default",
                            })
                            # Start client + replace pending dict on the channel event loop
                            async def _activate():
                                nonlocal client, key, mgr
                                await client.start()
                                mgr._channels[key] = client
                                _LOGGER.info("Personal WeChat channel activated: %s", result.account_id)
                                try:
                                    await client.send_text(
                                        to_user_id=result.user_id,
                                        text="你好！我是你的 Home Assistant 管家，已成功连接。有什么需要帮忙的吗？",
                                    )
                                    _LOGGER.info("Welcome message sent to %s", result.user_id)
                                except Exception as e:
                                    _LOGGER.warning("Failed to send welcome: %s", e)
                            asyncio.run_coroutine_threadsafe(
                                _activate(), _channel_manager_loop
                            )
                            break

                # Persist credentials to config file so they survive restart
                try:
                    cfg = _read_stored_config()
                    if "channels" not in cfg:
                        cfg["channels"] = {}
                    ch_cfg = cfg["channels"]

                    # Determine if this is a multi-account addition
                    is_add = login_state.get("is_add_account", False)
                    account_label = login_state.get("account_label")

                    if is_add:
                        # Multi-account mode: ensure personal_wechat is a list
                        existing = ch_cfg.get("personal_wechat", [])
                        if isinstance(existing, dict):
                            # Convert single dict format to list, preserving existing
                            existing_id = existing.get("id", existing.get("credentials", {}).get("account_id", "default"))
                            existing = [{"id": existing_id, **existing}] if existing.get("credentials") else []
                        elif not isinstance(existing, list):
                            existing = []

                        # Check for duplicate account_id in existing list
                        dup = False
                        for acct in existing:
                            creds = acct.get("credentials", {})
                            if creds.get("account_id") == result.account_id:
                                # Update existing credentials instead of duplicating
                                acct["credentials"] = {
                                    "token": result.token,
                                    "user_id": result.user_id,
                                    "base_url": result.base_url,
                                    "account_id": result.account_id or "default",
                                    "get_updates_buf": "",
                                }
                                acct["enabled"] = True
                                dup = True
                                _LOGGER.info("Updated existing account credentials: %s", result.account_id)
                                break

                        if not dup:
                            import uuid
                            account_id = f"wx_{uuid.uuid4().hex[:8]}"
                            existing.append({
                                "id": account_id,
                                "label": account_label or f"个人微信 {result.account_id[:8]}",
                                "enabled": True,
                                "show_reasoning": True,
                                "credentials": {
                                    "token": result.token,
                                    "user_id": result.user_id,
                                    "base_url": result.base_url,
                                    "account_id": result.account_id or "default",
                                    "get_updates_buf": "",
                                },
                            })
                            _LOGGER.info("Added new WeChat account: %s (%s)", account_id, result.account_id)

                        ch_cfg["personal_wechat"] = existing
                    else:
                        # Legacy single-account mode or first-time setup
                        existing = ch_cfg.get("personal_wechat", {})
                        if isinstance(existing, list):
                            # Already in list format, find or create entry
                            found = False
                            for acct in existing:
                                creds = acct.get("credentials", {})
                                if creds.get("account_id") == result.account_id:
                                    acct["credentials"] = {
                                        "token": result.token,
                                        "user_id": result.user_id,
                                        "base_url": result.base_url,
                                        "account_id": result.account_id or "default",
                                        "get_updates_buf": "",
                                    }
                                    acct["enabled"] = True
                                    found = True
                                    break
                            if not found:
                                import uuid
                                existing.append({
                                    "id": f"wx_{uuid.uuid4().hex[:8]}",
                                    "label": f"个人微信 {result.account_id[:8]}",
                                    "enabled": True,
                                    "show_reasoning": True,
                                    "credentials": {
                                        "token": result.token,
                                        "user_id": result.user_id,
                                        "base_url": result.base_url,
                                        "account_id": result.account_id or "default",
                                        "get_updates_buf": "",
                                    },
                                })
                            ch_cfg["personal_wechat"] = existing
                        else:
                            # Single dict format → set/update credentials
                            ch_cfg["personal_wechat"] = {
                                "id": "default",
                                "enabled": True,
                                "show_reasoning": True,
                                "credentials": {
                                    "token": result.token,
                                    "user_id": result.user_id,
                                    "base_url": result.base_url,
                                    "account_id": result.account_id or "default",
                                    "get_updates_buf": "",
                                },
                            }

                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2, ensure_ascii=False)
                    _LOGGER.info("WeChat credentials persisted to %s", CONFIG_FILE)
                except Exception as e:
                    _LOGGER.warning("Failed to persist WeChat credentials: %s", e)
        except TimeoutError:
            login_state["status"] = "waiting"
        except ValueError as e:
            if "expired" in str(e).lower():
                login_state["status"] = "expired"
            else:
                login_state["status"] = "error"
                login_state["error"] = str(e)
        finally:
            loop.close()

        response = json.dumps({
            "status": login_state["status"],
            "token": login_state.get("token"),
            "account_id": login_state.get("account_id"),
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())

    except Exception as e:
        sys.stderr.write(f"[MiMo WebUI] WeChat status check error: {e}\n")
        response = json.dumps({"status": "error", "message": str(e)})
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())


# --------------------------------------------------------------------------- #
# Channel management REST endpoints (served by the WebUI itself)
# --------------------------------------------------------------------------- #
_SECRET_KEYS = ("secret", "token", "aes_key", "key")


def _read_stored_config() -> dict:
    """Read the stored mimo.json config without env-merge side effects."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"channels": {}}


def _mask_secrets(obj: Any) -> Any:
    """Recursively mask secret fields in config dicts/lists."""
    if isinstance(obj, dict):
        return {
            k: ("********" if (any(s in k.lower() for s in _SECRET_KEYS) and v) else _mask_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_secrets(item) for item in obj]
    return obj


def _mask_channels(channels: dict) -> dict:
    """Mask secret fields so they are never sent back in plaintext."""
    return _mask_secrets(channels)


def _ensure_accounts_list(channels: dict, ch_type: str) -> list[dict]:
    """Normalize channel config to list-of-accounts format.

    - dict → list (wrap single account)
    - list → list (already multi-account)
    - missing/empty → []
    """
    cfg = channels.get(ch_type, {})
    if isinstance(cfg, list):
        return cfg
    if isinstance(cfg, dict):
        if cfg:
            account_id = cfg.get("id", cfg.get("credentials", {}).get("account_id", "default"))
            return [{"id": account_id, **cfg}]
        return []
    return []


def _save_multi_account_config(accounts: list[dict], ch_type: str) -> bool:
    """Save accounts list for a channel type to config file + reload."""
    stored = _read_stored_config()
    if "channels" not in stored:
        stored["channels"] = {}
    stored["channels"][ch_type] = accounts
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _LOGGER.error("Failed to save config: %s", e)
        return False
    # Reload channels at runtime
    _reload_channels(stored)
    return True


def _handle_accounts_list(self) -> None:
    """GET /api/accounts — list all accounts with connection status."""
    stored = _read_stored_config()
    channels = stored.get("channels", {})
    manager = _get_channel_manager()
    status = manager.get_status() if manager else {}

    # Normalize to flat list of account entries
    accounts = []
    for ch_type in ("feishu", "wechat", "personal_wechat"):
        # Get config
        raw = channels.get(ch_type, {})
        if isinstance(raw, dict) and raw:
            # Single account format
            entries = [{"id": raw.get("id", "default"), **raw}]
        elif isinstance(raw, list):
            entries = raw
        else:
            entries = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("id", "default")
            label = entry.get("label") or _channel_type_label(ch_type)
            ch_status = status.get(f"{ch_type}_{aid}") or status.get(ch_type, {})
            accounts.append({
                "type": ch_type,
                "id": aid,
                "label": label,
                "enabled": bool(entry.get("enabled", False)),
                "show_reasoning": bool(entry.get("show_reasoning", True)),
                "has_credentials": bool(entry.get("credentials")) or bool(entry.get("token")),
                "status": ch_status.get("status", "disconnected"),
                "connected": bool(ch_status.get("connected", False)),
            })

    self._send_json(200, {"accounts": accounts})


def _channel_type_label(ch_type: str) -> str:
    labels = {"feishu": "飞书", "wechat": "企业微信", "personal_wechat": "个人微信"}
    return labels.get(ch_type, ch_type)


def _handle_accounts_post(self) -> None:
    """POST /api/accounts/{type} — add a new account.

    For personal_wechat, starts QR login and returns session.
    For other types, expects full config in body.
    """
    # Parse path: /api/accounts/personal_wechat  →  ch_type = "personal_wechat"
    path = self.path.rstrip("/")
    parts = path.split("/")
    if len(parts) < 4:
        self._send_json(400, {"error": "Invalid path"})
        return
    ch_type = parts[3]

    if ch_type not in ("personal_wechat", "wechat", "feishu"):
        self._send_json(400, {"error": f"Unsupported channel type: {ch_type}"})
        return

    try:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    if ch_type == "personal_wechat":
        # Start QR login for a new WeChat account
        self._handle_add_personal_wechat(data)
    elif ch_type == "wechat":
        # Add WeChat Work account with provided config
        self._handle_add_wechat_work(data)
    elif ch_type == "feishu":
        # Add Feishu account
        self._handle_add_feishu(data)
    else:
        self._send_json(400, {"error": f"Unsupported: {ch_type}"})


def _handle_add_personal_wechat(self, data: dict) -> None:
    """Start QR login for adding a new personal WeChat account."""
    label = str(data.get("label", "")) or None
    try:
        sys.path.insert(0, WEBUI_DIR)
        from wechat_personal import PersonalWeChatClient

        # Create a temporary client to start login
        client = PersonalWeChatClient(
            on_message=lambda msg: asyncio.sleep(0),
        )

        async def do_login():
            login_session = await client.start_login()
            return login_session

        loop = asyncio.new_event_loop()
        try:
            login_session = loop.run_until_complete(do_login())
        finally:
            loop.close()

        session_key = login_session.session_key

        # Store login state (new format: includes label for multi-account)
        _login_states[session_key] = {
            "qrcode": login_session.qrcode,
            "qrcode_url": login_session.qrcode_url,
            "status": "waiting",
            "client": client,
            "account_label": label,
            "is_add_account": True,
        }

        response = json.dumps({
            "session_key": session_key,
            "qrcode": login_session.qrcode,
            "qrcode_url": login_session.qrcode_url,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())

    except Exception as e:
        _LOGGER.error("WeChat add account login error: %s", e)
        self._send_json(500, {"error": str(e)})


def _handle_add_wechat_work(self, data: dict) -> None:
    """Add a new WeChat Work account."""
    corp_id = (data.get("corp_id") or "").strip()
    agent_id = (data.get("agent_id") or "").strip()
    secret = (data.get("secret") or "").strip()
    token = (data.get("token") or "").strip()
    encoding_aes_key = (data.get("encoding_aes_key") or "").strip()
    label = (data.get("label") or "").strip() or f"企业微信 {agent_id}"
    enabled = bool(data.get("enabled", True))

    if not corp_id or not agent_id or not secret:
        self._send_json(400, {"error": "缺少必要参数: corp_id, agent_id, secret"})
        return

    import uuid
    account_id = f"ww_{uuid.uuid4().hex[:8]}"

    accounts = _ensure_accounts_list(_read_stored_config().get("channels", {}), "wechat")
    accounts.append({
        "id": account_id,
        "label": label,
        "enabled": enabled,
        "corp_id": corp_id,
        "agent_id": agent_id,
        "secret": secret,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
        "show_reasoning": True,
    })

    if _save_multi_account_config(accounts, "wechat"):
        self._send_json(200, {
            "success": True,
            "account": {"type": "wechat", "id": account_id, "label": label},
        })
    else:
        self._send_json(500, {"error": "保存配置失败"})


def _handle_add_feishu(self, data: dict) -> None:
    """Add a new Feishu account."""
    app_id = (data.get("app_id") or "").strip()
    app_secret = (data.get("app_secret") or "").strip()
    label = (data.get("label") or "").strip() or f"飞书 {app_id[:8]}"
    enabled = bool(data.get("enabled", True))

    if not app_id or not app_secret:
        self._send_json(400, {"error": "缺少必要参数: app_id, app_secret"})
        return

    import uuid
    account_id = f"fs_{uuid.uuid4().hex[:8]}"

    accounts = _ensure_accounts_list(_read_stored_config().get("channels", {}), "feishu")
    accounts.append({
        "id": account_id,
        "label": label,
        "enabled": enabled,
        "app_id": app_id,
        "app_secret": app_secret,
        "show_reasoning": True,
    })

    if _save_multi_account_config(accounts, "feishu"):
        self._send_json(200, {
            "success": True,
            "account": {"type": "feishu", "id": account_id, "label": label},
        })
    else:
        self._send_json(500, {"error": "保存配置失败"})


def _handle_accounts_delete(self) -> None:
    """DELETE /api/accounts/{type}/{id} — remove an account."""
    path = self.path.rstrip("/")
    parts = path.split("/")
    if len(parts) < 5:
        self._send_json(400, {"error": "Invalid path"})
        return
    ch_type = parts[3]
    account_id = parts[4]

    if ch_type not in ("personal_wechat", "wechat", "feishu"):
        self._send_json(400, {"error": f"Unsupported: {ch_type}"})
        return

    if ch_type == "feishu":
        self._send_json(400, {"error": "飞书目前仅支持单账号"})
        return

    stored = _read_stored_config()
    channels = stored.get("channels", {})
    accounts = _ensure_accounts_list(channels, ch_type)

    # Filter out the target account
    new_accounts = [a for a in accounts if a.get("id") != account_id]

    if len(new_accounts) == len(accounts):
        self._send_json(404, {"error": f"Account {account_id} not found"})
        return

    if _save_multi_account_config(new_accounts, ch_type):
        _LOGGER.info("Removed account %s/%s, %d remaining", ch_type, account_id, len(new_accounts))
        self._send_json(200, {"success": True})
    else:
        self._send_json(500, {"error": "保存配置失败"})


def _handle_accounts_put(self) -> None:
    """PUT /api/accounts/{type}/{id} — update account settings."""
    path = self.path.rstrip("/")
    parts = path.split("/")
    if len(parts) < 5:
        self._send_json(400, {"error": "Invalid path"})
        return
    ch_type = parts[3]
    account_id = parts[4]

    try:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        updates = json.loads(raw) if raw else {}
    except Exception as e:
        self._send_json(400, {"error": f"解析失败: {e}"})
        return

    stored = _read_stored_config()
    channels = stored.get("channels", {})
    accounts = _ensure_accounts_list(channels, ch_type)

    found = False
    for account in accounts:
        if account.get("id") == account_id:
            # Update allowed fields
            for field in ("enabled", "show_reasoning", "label", "corp_id", "agent_id",
                          "secret", "token", "encoding_aes_key"):
                if field in updates:
                    account[field] = updates[field]
            found = True
            break

    if not found:
        self._send_json(404, {"error": f"Account {account_id} not found"})
        return

    if _save_multi_account_config(accounts, ch_type):
        self._send_json(200, {"success": True})
    else:
        self._send_json(500, {"error": "保存配置失败"})


def _serve_accounts_page(self) -> None:
    """Serve the multi-account management HTML page."""
    page_path = os.path.join(WEBUI_DIR, "accounts.html")
    # Fallback to inline page if file doesn't exist
    if os.path.isfile(page_path):
        self.path = "/accounts.html"
        super().do_GET()
        return

    # Inline multi-account management page
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self.end_headers()
    self.wfile.write(_build_accounts_page_html().encode("utf-8"))


def _build_accounts_page_html() -> str:
    """Build the multi-account management HTML page (inline fallback)."""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>多账号管理 - MiMo Code</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f5f5f5;font-family:system-ui,sans-serif;padding:24px;max-width:800px;margin:0 auto;color:#333}
h1{font-size:20px;margin-bottom:8px;color:#1a1a1a}
h2{font-size:16px;margin-bottom:12px;color:#333;display:flex;align-items:center;gap:8px}
.subtitle{font-size:13px;color:#888;margin-bottom:20px}
.card{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px;margin-bottom:16px}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.badge-connected{background:#e8f5e9;color:#2e7d32}
.badge-disconnected{background:#f5f5f5;color:#999}
.badge-pending{background:#fff3e0;color:#e65100}
.badge-error{background:#ffebee;color:#c62828}
.badge-session_expired{background:#fce4ec;color:#ad1457}
.account-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #f0f0f0}
.account-row:last-child{border-bottom:none}
.account-info{flex:1}
.account-name{font-size:14px;font-weight:500;margin-bottom:2px}
.account-type{font-size:12px;color:#888}
.account-actions{display:flex;gap:8px;align-items:center}
.btn{padding:6px 14px;border:none;border-radius:6px;font-size:12px;cursor:pointer;transition:all .15s;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
.btn-primary{background:#1976d2;color:#fff}
.btn-primary:hover{background:#1565c0}
.btn-danger{background:#ef5350;color:#fff}
.btn-danger:hover{background:#d32f2f}
.btn-outline{background:transparent;border:1px solid #ddd;color:#666}
.btn-outline:hover{background:#f5f5f5;border-color:#ccc}
.btn-sm{padding:4px 10px;font-size:11px}
.toggle{position:relative;display:inline-block;width:36px;height:20px;margin:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#ccc;transition:.3s;border-radius:20px}
.slider:before{position:absolute;content:"";height:14px;width:14px;left:3px;bottom:3px;background:#fff;transition:.3s;border-radius:50%}
.toggle input:checked+.slider{background:#1976d2}
.toggle input:checked+.slider:before{transform:translateX(16px)}
.empty-state{text-align:center;padding:32px 16px;color:#999}
.empty-state p{font-size:13px;margin-bottom:12px}
.loading{text-align:center;padding:32px;color:#888;font-size:14px}
.qr-modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:1000;align-items:center;justify-content:center}
.qr-modal.show{display:flex}
.qr-modal-content{background:#fff;border-radius:12px;padding:24px;text-align:center;max-width:360px;width:90%}
.qr-modal-content h3{font-size:16px;margin-bottom:8px}
.qr-modal-content p{font-size:12px;color:#888;margin-bottom:16px}
.qr-modal-content img{max-width:280px;border:1px solid #eee;border-radius:8px;margin-bottom:12px}
.qr-modal-content .status-text{font-size:13px;color:#e65100;margin-bottom:8px}
.back-link{display:inline-block;margin-bottom:20px;color:#1976d2;text-decoration:none;font-size:13px}
.back-link:hover{text-decoration:underline}
.label-input{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px;margin-bottom:12px}
.label-input:focus{outline:none;border-color:#1976d2}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:12px;color:#666;margin-bottom:4px}
.form-group input{width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px}
.form-group input:focus{outline:none;border-color:#1976d2}
.account-label-icon{font-size:16px;margin-right:4px}
</style>
</head>
<body>
<h1>多账号管理</h1>
<p class="subtitle">管理所有 IM 通道的多个账号</p>
<a href="/" class="back-link">← 返回主面板</a>

<div id="accounts-loading" class="loading">加载中...</div>
<div id="accounts-content" style="display:none"></div>

<div id="qr-modal" class="qr-modal">
  <div class="qr-modal-content">
    <h3>添加个人微信</h3>
    <p>请使用微信扫描二维码登录</p>
    <div id="qrcode-container"><img id="qrcode-img" src="" alt="QR Code"/></div>
    <div id="qr-status" class="status-text">等待扫码...</div>
    <button class="btn btn-outline" onclick="closeQRModal()">取消</button>
  </div>
</div>

<div id="add-wechat-modal" class="qr-modal">
  <div class="qr-modal-content" style="text-align:left;max-width:400px">
    <h3 style="text-align:center;margin-bottom:16px">添加企业微信</h3>
    <div class="form-group">
      <label>企业 ID (corp_id)</label>
      <input id="ww-corp-id" type="text" placeholder="wx..."/>
    </div>
    <div class="form-group">
      <label>应用 Agent ID</label>
      <input id="ww-agent-id" type="text" placeholder="1000001"/>
    </div>
    <div class="form-group">
      <label>应用 Secret</label>
      <input id="ww-secret" type="password" placeholder="..."/>
    </div>
    <div class="form-group">
      <label>Token (可选)</label>
      <input id="ww-token" type="text" placeholder="..."/>
    </div>
    <div class="form-group">
      <label>Encoding AES Key (可选)</label>
      <input id="ww-aes-key" type="text" placeholder="..."/>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px;justify-content:center">
      <button class="btn btn-primary" onclick="submitWechatWork()">添加</button>
      <button class="btn btn-outline" onclick="closeWechatWorkModal()">取消</button>
    </div>
  </div>
</div>

<script>
const CHANNEL_LABELS = {feishu:'飞书', wechat:'企业微信', personal_wechat:'个人微信'};
const CHANNEL_ICONS = {feishu:'📘', wechat:'🏢', personal_wechat:'💬'};

async function loadAccounts() {
  document.getElementById('accounts-loading').style.display = 'block';
  document.getElementById('accounts-content').style.display = 'none';
  try {
    const resp = await fetch('/api/accounts');
    const data = await resp.json();
    renderAccounts(data.accounts || []);
  } catch(e) {
    document.getElementById('accounts-loading').innerHTML = '加载失败: ' + e.message;
  }
}

function renderAccounts(accounts) {
  document.getElementById('accounts-loading').style.display = 'none';
  const container = document.getElementById('accounts-content');
  container.style.display = 'block';

  // Group by channel type
  const grouped = {};
  for (const a of accounts) {
    if (!grouped[a.type]) grouped[a.type] = [];
    grouped[a.type].push(a);
  }

  const order = ['feishu', 'wechat', 'personal_wechat'];

  let html = '';
  for (const chType of order) {
    const list = grouped[chType] || [];
    const icon = CHANNEL_ICONS[chType] || '📡';
    const label = CHANNEL_LABELS[chType] || chType;
    const canAdd = chType === 'personal_wechat' || chType === 'wechat';
    const isSingle = chType === 'feishu';

    html += '<div class="card">';
    html += '<div class="card-header">';
    html += '<h2>' + icon + ' ' + label + '</h2>';
    html += '<div>';
    if (canAdd) {
      html += '<button class="btn btn-primary btn-sm" onclick="addAccount(\'' + chType + '\')">+ 添加账号</button>';
    }
    html += '</div></div>';

    if (list.length === 0) {
      html += '<div class="empty-state"><p>暂无账号配置</p>';
      if (canAdd) {
        html += '<button class="btn btn-outline" onclick="addAccount(\'' + chType + '\')">添加账号</button>';
      }
      html += '</div>';
    } else {
      for (const acct of list) {
        const statusClass = acct.connected ? 'badge-connected' : (acct.status === 'pending_login' ? 'badge-pending' : (acct.status === 'error' ? 'badge-error' : (acct.status === 'session_expired' ? 'badge-session_expired' : 'badge-disconnected')));
        const statusText = acct.connected ? '已连接' : (acct.status === 'pending_login' ? '待登录' : (acct.status === 'session_expired' ? '会话过期' : (acct.status === 'error' ? '错误' : '未连接')));
        const displayName = acct.label || acct.id || '默认';

        html += '<div class="account-row">';
        html += '<div class="account-info">';
        html += '<div class="account-name">' + displayName + '<span class="badge ' + statusClass + '" style="margin-left:8px">' + statusText + '</span></div>';
        html += '<div class="account-type">ID: ' + acct.id + (acct.has_credentials ? ' • 已认证' : ' • 未认证') + '</div>';
        html += '</div>';
        html += '<div class="account-actions">';

        if (!isSingle) {
          // Toggle enabled
          html += '<label class="toggle"><input type="checkbox" ' + (acct.enabled ? 'checked' : '') + ' onchange="toggleAccount(\'' + chType + '\',\'' + acct.id + '\',this.checked)"/><span class="slider"></span></label>';
          // Delete
          html += '<button class="btn btn-danger btn-sm" onclick="deleteAccount(\'' + chType + '\',\'' + acct.id + '\',\'' + displayName.replace(/'/g,"\\'") + '\')">删除</button>';
        }

        html += '</div></div>';
      }
    }
    html += '</div>';
  }

  container.innerHTML = html;
}

async function toggleAccount(type, id, enabled) {
  try {
    await fetch('/api/accounts/' + type + '/' + id, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: enabled}),
    });
    // Reload after a short delay for channel manager to restart
    setTimeout(loadAccounts, 1000);
  } catch(e) {
    alert('操作失败: ' + e.message);
  }
}

async function deleteAccount(type, id, name) {
  if (!confirm('确定要删除账号 "' + name + '" 吗？')) return;
  try {
    const resp = await fetch('/api/accounts/' + type + '/' + id, {method: 'DELETE'});
    const data = await resp.json();
    if (data.success) {
      setTimeout(loadAccounts, 1500);
    } else {
      alert('删除失败: ' + (data.error || '未知错误'));
    }
  } catch(e) {
    alert('删除失败: ' + e.message);
  }
}

let addAccountType = '';
let qrSessionKey = '';
let qrPollTimer = null;

function addAccount(type) {
  addAccountType = type;
  if (type === 'personal_wechat') {
    startPersonalWechatAdd();
  } else if (type === 'wechat') {
    document.getElementById('add-wechat-modal').classList.add('show');
  }
}

function closeWechatWorkModal() {
  document.getElementById('add-wechat-modal').classList.remove('show');
}

async function submitWechatWork() {
  const data = {
    corp_id: document.getElementById('ww-corp-id').value.trim(),
    agent_id: document.getElementById('ww-agent-id').value.trim(),
    secret: document.getElementById('ww-secret').value.trim(),
    token: document.getElementById('ww-token').value.trim(),
    encoding_aes_key: document.getElementById('ww-aes-key').value.trim(),
    enabled: true,
  };
  if (!data.corp_id || !data.agent_id || !data.secret) {
    alert('请填写企业 ID、Agent ID 和 Secret');
    return;
  }
  try {
    const resp = await fetch('/api/accounts/wechat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    const result = await resp.json();
    if (result.success) {
      alert('企业微信账号已添加，正在重新加载通道...');
      closeWechatWorkModal();
      setTimeout(loadAccounts, 2000);
    } else {
      alert('添加失败: ' + (result.error || '未知错误'));
    }
  } catch(e) {
    alert('添加失败: ' + e.message);
  }
}

async function startPersonalWechatAdd() {
  try {
    const resp = await fetch('/api/accounts/personal_wechat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({label: '个人微信 ' + new Date().toLocaleDateString()}),
    });
    const data = await resp.json();
    if (data.error) {
      alert('启动登录失败: ' + data.error);
      return;
    }

    qrSessionKey = data.session_key;

    // Show QR modal
    const modal = document.getElementById('qr-modal');
    document.getElementById('qrcode-img').src = data.qrcode_url || 'data:image/png;base64,' + data.qrcode;
    document.getElementById('qr-status').textContent = '等待扫码...';
    modal.classList.add('show');

    // Start polling
    startQRPolling();
  } catch(e) {
    alert('启动登录失败: ' + e.message);
  }
}

function startQRPolling() {
  if (qrPollTimer) clearInterval(qrPollTimer);

  qrPollTimer = setInterval(async () => {
    try {
      const resp = await fetch('/api/wechat/login/status', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_key: qrSessionKey}),
      });
      const data = await resp.json();

      if (data.status === 'success') {
        clearInterval(qrPollTimer);
        qrPollTimer = null;
        document.getElementById('qr-status').textContent = '登录成功！账号已添加';
        document.getElementById('qr-status').style.color = '#2e7d32';
        setTimeout(() => {
          closeQRModal();
          loadAccounts();
        }, 1500);
      } else if (data.status === 'expired') {
        clearInterval(qrPollTimer);
        qrPollTimer = null;
        document.getElementById('qr-status').textContent = '二维码已过期，请刷新页面重试';
        document.getElementById('qr-status').style.color = '#c62828';
      } else {
        document.getElementById('qr-status').textContent = '等待扫码...';
      }
    } catch(e) {
      console.error('QR poll error:', e);
    }
  }, 2000);
}

function closeQRModal() {
  document.getElementById('qr-modal').classList.remove('show');
  if (qrPollTimer) {
    clearInterval(qrPollTimer);
    qrPollTimer = null;
  }
}

// Initial load
loadAccounts();
</script>
</body>
</html>"""



def _handle_channels_status(self) -> None:
    """GET /api/channels/status — live connection status."""
    manager = _get_channel_manager()
    status = manager.get_status() if manager else {}
    payload = json.dumps({"status": status}).encode("utf-8")
    origin = self.headers.get("Origin", "")
    allowed = "null"
    if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
        allowed = origin
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", allowed)
    self.end_headers()
    self.wfile.write(payload)


def _handle_channels_restart(self) -> None:
    """POST /api/channels/restart — reload channel manager with current config."""
    try:
        stored = _read_stored_config()
        ok = _reload_channels(stored)
        self._send_json(200, {"success": ok, "message": "通道已重新加载" if ok else "重载失败"})
    except Exception as e:
        self._send_json(500, {"error": str(e)})


def _handle_channels_get(self) -> None:
    """GET /api/channels — current config (secrets masked) + status."""
    stored = _read_stored_config()
    channels = _mask_channels(stored.get("channels", {}))
    manager = _get_channel_manager()
    status = manager.get_status() if manager else {}
    payload = json.dumps({
        "channels": channels,
        "status": status,
    }, ensure_ascii=False).encode("utf-8")
    origin = self.headers.get("Origin", "")
    allowed = "null"
    if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
        allowed = origin
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", allowed)
    self.end_headers()
    self.wfile.write(payload)


def _handle_channels_post(self) -> None:
    """POST /api/channels — persist config and reload channels at runtime."""
    try:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        incoming = json.loads(raw)
    except Exception as e:
        self._send_json(400, {"error": f"请求体解析失败: {e}"})
        return

    # Preserve existing secrets that the client sent back as the mask placeholder
    stored = _read_stored_config()
    stored_channels = stored.get("channels", {})
    new_channels = incoming.get("channels", {})

    for name, ch in new_channels.items():
        if not isinstance(ch, dict):
            continue
        existing = stored_channels.get(name, {})
        for k, v in ch.items():
            if isinstance(v, str) and v == "********" and k in existing:
                ch[k] = existing[k]

    merged = {"channels": new_channels}

    # Persist
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except Exception as e:
        self._send_json(500, {"error": f"保存失败: {e}"})
        return

    # Runtime reload (no addon restart needed)
    ok = _reload_channels(merged)

    manager = _get_channel_manager()
    status = manager.get_status() if manager else {}
    self._send_json(200, {
        "success": True,
        "reloaded": ok,
        "channels": _mask_channels(new_channels),
        "status": status,
    })


def _handle_feishu_test(self) -> None:
    """POST /api/feishu/test — probe WS long-connection with submitted creds."""
    try:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw)
    except Exception:
        data = {}

    app_id = (data.get("app_id") or "").strip()
    app_secret = (data.get("app_secret") or "").strip()

    # Allow testing with the stored secret if the placeholder was sent back
    if app_secret == "********":
        stored = _read_stored_config()
        app_secret = stored.get("channels", {}).get("feishu", {}).get("app_secret", "")

    try:
        from feishu_client import FeishuClient
        result = FeishuClient.test_connection(app_id, app_secret, timeout=10.0)
    except Exception as e:
        result = {"success": False, "connected": False, "error": str(e)}

    self._send_json(200, result)


def _send_json(self, code: int, obj: dict) -> None:
    """Helper to send a JSON response."""
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    origin = self.headers.get("Origin", "")
    allowed = "null"
    if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
        allowed = origin
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", allowed)
    self.end_headers()
    self.wfile.write(payload)


# ------------------------------------------------------------------ #
# Filesystem API (bypasses mimo serve's project-dir restriction)
# ------------------------------------------------------------------ #
def _sanitize_fs_path(raw: str) -> str | None:
    """Resolve and validate a filesystem path. Returns None if path is outside allowed dirs."""
    from pathlib import Path
    ALLOWED_PREFIXES = ["/data/mimocode", "/usr/share/mimocode"]
    try:
        p = Path(raw).resolve()
        for prefix in ALLOWED_PREFIXES:
            if str(p).startswith(prefix):
                return str(p)
        return None
    except Exception:
        return None


def _handle_fs_list(self) -> None:
    """GET /api/fs/list?path=... — list directory contents."""
    import urllib.parse
    from pathlib import Path
    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
    dir_path = qs.get("path", ["/"])[0]
    safe = _sanitize_fs_path(dir_path)
    if not safe:
        self._send_json(403, {"error": "path not allowed"})
        return
    try:
        p = Path(safe)
        if not p.is_dir():
            self._send_json(200, {"entries": [], "error": "not a directory"})
            return
        entries = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if child.is_dir() else "file",
                    "size": st.st_size if child.is_file() else 0,
                })
            except PermissionError:
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if child.is_dir() else "file",
                })
        self._send_json(200, {"entries": entries, "path": dir_path})
    except Exception as e:
        self._send_json(500, {"error": str(e)})


def _handle_fs_read(self) -> None:
    """GET /api/fs/read?path=... — read file content."""
    import urllib.parse
    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
    file_path = qs.get("path", [""])[0]
    safe = _sanitize_fs_path(file_path)
    if not safe:
        self._send_json(403, {"error": "path not allowed"})
        return
    try:
        from pathlib import Path
        p = Path(safe)
        if not p.is_file():
            self._send_json(404, {"error": "file not found"})
            return
        content = p.read_bytes()
        origin = self.headers.get("Origin", "")
        allowed = "null"
        if origin and ("172.30.32" in origin or "homeassistant" in origin or "localhost" in origin):
            allowed = origin
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.end_headers()
        self.wfile.write(content)
    except Exception as e:
        self._send_json(500, {"error": str(e)})


def _handle_fs_write(self) -> None:
    """PUT /api/fs/write?path=... — write file content (body = raw text)."""
    import urllib.parse
    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
    file_path = qs.get("path", [""])[0]
    safe = _sanitize_fs_path(file_path)
    if not safe:
        self._send_json(403, {"error": "path not allowed"})
        return
    try:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        from pathlib import Path
        p = Path(file_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        self._send_json(200, {"success": True, "path": file_path})
    except Exception as e:
        self._send_json(500, {"error": str(e)})


# Bind handler functions as instance methods of MiMoProxyHandler.
# They are defined as module-level functions (for readability) but are dispatched
# via self._handle_* in do_GET/do_POST, so they must be attached to the class.
MiMoProxyHandler._handle_wechat_login = _handle_wechat_login
MiMoProxyHandler._handle_wechat_login_status = _handle_wechat_login_status
MiMoProxyHandler._handle_channels_status = _handle_channels_status
MiMoProxyHandler._handle_channels_get = _handle_channels_get
MiMoProxyHandler._handle_channels_post = _handle_channels_post
MiMoProxyHandler._handle_channels_restart = _handle_channels_restart
MiMoProxyHandler._handle_feishu_test = _handle_feishu_test
MiMoProxyHandler._send_json = _send_json

# Multi-account management handlers
MiMoProxyHandler._handle_accounts_list = _handle_accounts_list
MiMoProxyHandler._handle_accounts_post = _handle_accounts_post
MiMoProxyHandler._handle_accounts_delete = _handle_accounts_delete
MiMoProxyHandler._handle_accounts_put = _handle_accounts_put
MiMoProxyHandler._handle_add_personal_wechat = _handle_add_personal_wechat
MiMoProxyHandler._handle_add_wechat_work = _handle_add_wechat_work
MiMoProxyHandler._serve_accounts_page = _serve_accounts_page

# File system handlers
MiMoProxyHandler._handle_fs_list = _handle_fs_list
MiMoProxyHandler._handle_fs_read = _handle_fs_read
MiMoProxyHandler._handle_fs_write = _handle_fs_write


if __name__ == "__main__":
    # Configure logging for _LOGGER messages
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    
    # Pre-warm lark_oapi — import is very slow on SD cards (30s+).
    # Doing it here before anything else so channels (feishu) don't block later.
    sys.stderr.write("[MiMo WebUI] Warming up lark_oapi (may take a while on slow storage)...\n")
    try:
        __import__("lark_oapi")
        sys.stderr.write("[MiMo WebUI] lark_oapi ready\n")
    except Exception as e:
        sys.stderr.write(f"[MiMo WebUI] lark_oapi warmup failed: {e}\n")
    
    # Start channel manager via ThreadPoolExecutor (avoids asyncio thread issues)
    import threading, functools

    def _start_channels_direct():
        """Create ChannelManager and run start() synchronously in its own event loop thread."""
        global _channel_manager_loop
        try:
            mgr = _get_channel_manager()
            if mgr is None:
                return
            loop = asyncio.new_event_loop()
            _channel_manager_loop = loop
            asyncio.set_event_loop(loop)
            # Run start() synchronously (it will complete, leaving feishu WS in daemon threads)
            loop.run_until_complete(mgr.start())
            sys.stderr.write(f"[MiMo WebUI] Channels active: {list(mgr._channels.keys())}\n")
            # Keep loop alive for future reloads
            def _keep():
                asyncio.set_event_loop(loop)
                loop.run_forever()
            threading.Thread(target=_keep, daemon=True).start()
        except Exception as e:
            sys.stderr.write(f"[MiMo WebUI] Channel start error: {e}\n")
            import traceback as _tb
            _tb.print_exc()

    threading.Thread(target=_start_channels_direct, daemon=True).start()

    # Start HTTP server (blocks forever)
    server = ThreadingMiMoServer(("0.0.0.0", PORT), MiMoProxyHandler)
    sys.stderr.write(f"[MiMo WebUI] Server listening on port {PORT} (mimo -> {MIMO_API_BASE})\n")
    sys.stderr.write(f"[MiMo WebUI] Config file: {CONFIG_FILE}\n")
    server.serve_forever()
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
    env_config = _build_config_from_env()
    if env_config.get("channels"):
        if "channels" not in config:
            config["channels"] = {}
        for ch_key, ch_val in env_config["channels"].items():
            if ch_key not in config["channels"]:
                config["channels"][ch_key] = ch_val
            elif isinstance(ch_val, dict):
                existing = config["channels"][ch_key]
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
            # Add current directory to path for imports
            sys.path.insert(0, WEBUI_DIR)
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
    def __init__(self, *args, **kwargs):
        static_dir = DIST_DIR if os.path.isdir(DIST_DIR) else WEBUI_DIR
        super().__init__(*args, directory=static_dir, **kwargs)

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

            # ---- Filesystem API (bypasses mimo serve's dir restriction) ----
            if self.path.startswith("/api/fs/list"):
                self._handle_fs_list()
                return
            if self.path.startswith("/api/fs/read"):
                self._handle_fs_read()
                return
            if self.path.startswith("/api/fs/write"):
                self._handle_fs_write()
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
            if self.path == "/api/channels":
                self._handle_channels_post()
            elif self.path == "/api/feishu/test":
                self._handle_feishu_test()
            elif self.path.startswith("/api/wechat/login/status"):
                self._handle_wechat_login_status()
            elif self.path.startswith("/api/wechat/login"):
                self._handle_wechat_login()
            elif self.path.startswith("/api/"):
                self._proxy_request("POST")
            else:
                self.send_error(405)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/"):
                self._proxy_request("DELETE")
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
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
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
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Access-Control-Allow-Origin", "*")
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

                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            data = e.read()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
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
                    if "personal_wechat" not in cfg["channels"]:
                        cfg["channels"]["personal_wechat"] = {"enabled": True}
                    cfg["channels"]["personal_wechat"]["credentials"] = {
                        "token": result.token,
                        "user_id": result.user_id,
                        "base_url": result.base_url,
                        "account_id": result.account_id or "default",
                        "get_updates_buf": "",
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


def _mask_channels(channels: dict) -> dict:
    """Mask secret fields so they are never sent back in plaintext."""
    out: dict = {}
    for name, ch in (channels or {}).items():
        if isinstance(ch, dict):
            out[name] = {
                k: ("********" if (any(s in k.lower() for s in _SECRET_KEYS) and v) else v)
                for k, v in ch.items()
            }
        else:
            out[name] = ch
    return out


def _handle_channels_status(self) -> None:
    """GET /api/channels/status — live connection status."""
    manager = _get_channel_manager()
    status = manager.get_status() if manager else {}
    payload = json.dumps({"status": status}).encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(payload)


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
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", "*")
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
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(payload)


# ------------------------------------------------------------------ #
# Filesystem API (bypasses mimo serve's project-dir restriction)
# ------------------------------------------------------------------ #
def _sanitize_fs_path(raw: str) -> str | None:
    """Resolve and validate a filesystem path. Returns None if path is outside allowed dirs."""
    from pathlib import Path
    ALLOWED_PREFIXES = ["/data", "/config", "/usr/share/mimocode"]
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
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
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


# Bind channel/wechat handler functions as instance methods of MiMoProxyHandler.
# They are defined as module-level functions (for readability) but are dispatched
# via self._handle_* in do_GET/do_POST, so they must be attached to the class.
MiMoProxyHandler._handle_wechat_login = _handle_wechat_login
MiMoProxyHandler._handle_wechat_login_status = _handle_wechat_login_status
MiMoProxyHandler._handle_channels_status = _handle_channels_status
MiMoProxyHandler._handle_channels_get = _handle_channels_get
MiMoProxyHandler._handle_channels_post = _handle_channels_post
MiMoProxyHandler._handle_feishu_test = _handle_feishu_test
MiMoProxyHandler._send_json = _send_json


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
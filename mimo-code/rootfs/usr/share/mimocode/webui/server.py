#!/usr/bin/env python3
"""MiMo Code Web UI - serves SPA and proxies all API calls to the mimo server."""
import http.server
import json
import os
import sys
import urllib.request
import urllib.error
import re
from socketserver import ThreadingMixIn

MIMO_PORT = int(os.environ.get("MIMO_PORT", "14096"))
MIMO_API_BASE = f"http://127.0.0.1:{MIMO_PORT}"
WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(WEBUI_DIR, "dist")
PORT = int(os.environ.get("WEBUI_PORT", "8099"))
MIMO_WORKDIR = os.environ.get("MIMO_WORKDIR", "/data/mimocode")


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
            if self.path.startswith("/api/"):
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


if __name__ == "__main__":
    server = ThreadingMiMoServer(("0.0.0.0", PORT), MiMoProxyHandler)
    sys.stderr.write(f"[MiMo WebUI] Server listening on port {PORT} (mimo -> {MIMO_API_BASE})\n")
    server.serve_forever()

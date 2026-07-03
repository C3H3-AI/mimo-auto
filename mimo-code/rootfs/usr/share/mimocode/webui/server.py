#!/usr/bin/env python3
"""MiMo Code Web UI - serves the SPA and proxies API calls to the mimo server."""

import http.server
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

MIMO_PORT = int(os.environ.get("MIMO_PORT", "14096"))
MIMO_API_BASE = f"http://127.0.0.1:{MIMO_PORT}"
WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("WEBUI_PORT", "8099"))
MIMO_WORKDIR = "/data/mimocode"


class MiMoProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBUI_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy_request("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_request("POST")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_request("DELETE")
        else:
            self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            self._proxy_request("PATCH")
        else:
            self.send_error(405)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _proxy_request(self, method):
        target_path = self.path[4:]
        target_url = f"{MIMO_API_BASE}{target_path}"

        if self.path.startswith("/api/proxy/"):
            target_url = self.path[11:]

        # Handle chat messages via mimo run subprocess
        # Browser may call /message, /messages, or /chat
        is_chat_post = method == "POST" and "/session/" in target_path and (
            target_path.endswith("/message") or
            target_path.endswith("/messages") or
            target_path.endswith("/chat")
        )
        is_chat_get = method == "GET" and "/session/" in target_path and (
            target_path.endswith("/message") or
            target_path.endswith("/messages")
        )

        if is_chat_post:
            self._handle_chat()
            return
        if is_chat_get:
            self._send_messages_stub()
            return

        body = None
        content_length = self.headers.get("Content-Length")
        if content_length:
            body = self.rfile.read(int(content_length))

        req = urllib.request.Request(
            target_url,
            data=body,
            method=method,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/json"),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "application/json")
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
            self.wfile.write(data)
        except urllib.error.URLError as e:
            self.send_error(502, f"Proxy error: {e.reason}")

    def _handle_chat(self):
        """Handle chat message by running mimo run as subprocess."""
        body_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(body_len) if body_len else b"{}"

        text = ""
        try:
            req_data = json.loads(body)
            msg = req_data.get("message", "")
            parts = req_data.get("parts", [])
            if parts and isinstance(parts, list) and len(parts) > 0:
                text = parts[0].get("text", "")
            if not text:
                text = msg
        except (json.JSONDecodeError, Exception):
            text = body.decode("utf-8", errors="replace")

        if not text:
            result_text = "Empty message"
            data = json.dumps({
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": result_text}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        try:
            result = subprocess.run(
                ["/usr/local/bin/mimo", "run", "--model", "mimo/mimo-auto", text],
                cwd=MIMO_WORKDIR,
                capture_output=True,
                text=True,
                timeout=180,
            )

            response_text = result.stdout.strip()
            if not response_text:
                response_text = result.stderr.strip() or "No response"

            data = json.dumps({
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": response_text}]
            }).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        except subprocess.TimeoutExpired:
            self.send_error(504, "AI request timed out")
        except Exception as e:
            self.send_error(500, f"Chat error: {str(e)}")

    def _send_messages_stub(self):
        """Return an empty message list (browser polls this via GET)."""
        data = json.dumps([]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        sys.stderr.write("[MiMo WebUI] %s - %s\n" % (self.client_address[0], format % args))


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), MiMoProxyHandler)
    sys.stderr.write(f"[MiMo WebUI] Server listening on port {PORT}\n")
    server.serve_forever()

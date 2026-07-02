#!/usr/bin/env python3
"""MiMo Code Web UI - serves the SPA and proxies API calls to the mimo server."""

import http.server
import json
import os
import sys
import urllib.request
import urllib.error

MIMO_PORT = int(os.environ.get("MIMO_PORT", "14096"))
MIMO_API_BASE = f"http://127.0.0.1:{MIMO_PORT}"
WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("WEBUI_PORT", "8099"))


class MiMoProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBUI_DIR, **kwargs)

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
                self.send_response(resp.status)
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "application/json")
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

    def log_message(self, format, *args):
        sys.stderr.write("[MiMo WebUI] %s - %s\n" % (self.client_address[0], format % args))


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), MiMoProxyHandler)
    sys.stderr.write(f"[MiMo WebUI] Server listening on port {PORT}\n")
    server.serve_forever()

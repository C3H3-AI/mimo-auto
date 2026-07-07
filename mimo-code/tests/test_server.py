"""Comprehensive tests for MiMo Code WebUI server.py.

Covers all 6 bug fixes plus edge cases and error handling paths.
"""
import json
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch, call, Mock
from io import BytesIO
import urllib.request
import urllib.error

# Add the source directory to path
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "rootfs", "usr", "share", "mimocode", "webui")
sys.path.insert(0, SRC_DIR)

# Must set env vars before import
os.environ["MIMO_PORT"] = "14096"
os.environ["WEBUI_PORT"] = "8099"
os.environ["MIMO_WORKDIR"] = "/tmp/mimocode_test"

# Now import the module under test
import server

# Path to the HA custom_components root
HA_COMPONENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))),
    "custom_components", "mimo_auto"
)


def _make_mock_headers(content_type="application/json"):
    """Create a mock headers object for testing _proxy_request.

    Returns a MagicMock where .get returns None for Content-Length
    and the given content_type for Content-Type.
    """
    mock = MagicMock()
    def side_effect(key, default=None):
        if key == "Content-Length":
            return None
        if key == "Content-Type":
            return content_type
        return default
    mock.get.side_effect = side_effect
    return mock


def _make_real_handler(path="/api/test"):
    """Create a real MiMoProxyHandler instance with minimal mocking.

    This is needed when testing methods that call send_response(),
    send_header(), end_headers(), etc. — the real handler methods.
    """
    mock_request = MagicMock()
    mock_request.makefile.return_value = BytesIO()
    inst = server.MiMoProxyHandler(mock_request, ("127.0.0.1", 54321), None)
    inst.wfile = BytesIO()
    inst.rfile = BytesIO()
    inst.path = path
    inst.headers = _make_mock_headers()
    inst.requestline = "GET /api/test HTTP/1.1"
    inst.request_version = "HTTP/1.1"
    inst.close_connection = True
    return inst


def _make_mock_instance(path="/api/test", content_type="application/json"):
    """Create a minimal Mock for calling _proxy_request directly.

    Explicitly sets all attributes that _proxy_request might call,
    so that we don't rely on Mock's auto-creation (which can shadow
    class-level patches).
    """
    inst = Mock()
    inst.path = path
    inst.headers = _make_mock_headers(content_type)
    inst.rfile = Mock()
    inst.rfile.read.return_value = None
    inst.wfile = BytesIO()
    inst.send_response = Mock()
    inst.send_header = Mock()
    inst.end_headers = Mock()
    inst.send_error = Mock()
    inst.log_message = Mock()
    # _handle_chat_via_mimo is NOT set here, so accessing it on a Mock()
    # will auto-create a sub-Mock — that's fine since the fallback test
    # just checks it was called (the auto-created mock records the call)
    return inst


class TestBug1ThreadingMixIn(unittest.TestCase):
    """Bug 1: server.py 单线程阻塞 → ThreadingMixIn"""

    def test_server_inherits_threading_mixin(self):
        """Verify ThreadingMiMoServer inherits from ThreadingMixIn + HTTPServer."""
        from socketserver import ThreadingMixIn
        import http.server
        self.assertTrue(issubclass(server.ThreadingMiMoServer, ThreadingMixIn))
        self.assertTrue(issubclass(server.ThreadingMiMoServer, http.server.HTTPServer))

    def test_daemon_threads_enabled(self):
        """Verify daemon_threads = True so threads don't prevent shutdown."""
        self.assertTrue(server.ThreadingMiMoServer.daemon_threads)

    def test_allow_reuse_address(self):
        """Verify allow_reuse_address = True so port reuse works."""
        self.assertTrue(server.ThreadingMiMoServer.allow_reuse_address)

    def test_server_routes_to_correct_handler(self):
        """Verify server instantiation uses MiMoProxyHandler."""
        with patch.object(server.ThreadingMiMoServer, 'serve_forever'):
            with patch.object(sys, 'argv', ['server.py']):
                srv = server.ThreadingMiMoServer(("0.0.0.0", 18099),
                                                  server.MiMoProxyHandler)
                self.assertEqual(srv.server_address, ("0.0.0.0", 18099))
                srv.server_close()


class TestBug2StreamingResponse(unittest.TestCase):
    """Bug 2: 流式响应被全量缓冲 → chunked streaming"""

    @patch("urllib.request.urlopen")
    def test_stream_branch_sends_chunked(self, mock_urlopen):
        """Verify streaming branch uses chunked transfer encoding."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/x-ndjson"}
        mock_resp.read.side_effect = [
            b'{"info":{"role":"assistant"},"parts":[{"type":"text","text":"Hello"}]}\n',
            b'{"info":{"role":"assistant"},"parts":[{"type":"text","text":"World"}]}\n',
            b'',
        ]
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inst = _make_real_handler("/api/session/test123/message")
        inst._proxy_request("POST")

        output = inst.wfile.getvalue()
        self.assertIn(b"Transfer-Encoding: chunked", output)
        self.assertIn(b"Hello", output)
        self.assertIn(b"World", output)

    @patch("urllib.request.urlopen")
    def test_non_stream_branch_buffers_full(self, mock_urlopen):
        """Verify non-streaming branch reads full body with Content-Length."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inst = _make_real_handler("/api/session")
        inst._proxy_request("GET")

        output = inst.wfile.getvalue()
        # {"status":"ok"} is 15 bytes
        self.assertIn(b"Content-Length: 15", output)
        self.assertIn(b'{"status":"ok"}', output)

    @patch("urllib.request.urlopen")
    def test_stream_chunk_size_is_8192(self, mock_urlopen):
        """Verify streaming uses resp.read(8192) (8KB chunks)."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/x-ndjson"}
        mock_resp.read.side_effect = [b"chunk1", b"chunk2", b""]
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inst = _make_real_handler("/api/session/test123/message")
        inst._proxy_request("POST")

        self.assertTrue(
            any(len(c.args) > 0 and c.args[0] == 8192
                for c in mock_resp.read.mock_calls),
            "resp.read() should be called with 8192 chunk size"
        )

    @patch("urllib.request.urlopen")
    def test_stream_flushes_after_each_chunk(self, mock_urlopen):
        """Verify each chunk is flushed immediately."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/x-ndjson"}
        mock_resp.read.side_effect = [b"chunk1", b"chunk2", b""]
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inst = _make_real_handler("/api/session/test123/message")
        inst.wfile = MagicMock()

        inst._proxy_request("POST")

        flush_count = sum(1 for _ in inst.wfile.flush.mock_calls)
        write_count = sum(1 for _ in inst.wfile.write.mock_calls)
        self.assertGreaterEqual(flush_count, 2,
                                "flush() should be called after each chunk")
        self.assertGreaterEqual(write_count, 2,
                                "write() should be called for each chunk")


class TestBug3ConfigPostToPatch(unittest.TestCase):
    """Bug 3: /config POST → PATCH"""

    def test_post_config_converts_to_patch_directly(self):
        """Direct unit test: POST /config → PATCH conversion in _proxy_request."""
        inst = _make_mock_instance("/api/config")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b'{"ok":true}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            server.MiMoProxyHandler._proxy_request(inst, "POST")

            call_req = mock_urlopen.call_args[0][0]
            self.assertEqual(call_req.method, "PATCH",
                             "POST /api/config should become PATCH /config")
            self.assertIn("/config", call_req.full_url)

    def test_post_other_paths_keep_post_method(self):
        """Verify POST /api/session keeps POST method."""
        inst = _make_mock_instance("/api/session")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b'{"id":"abc"}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            server.MiMoProxyHandler._proxy_request(inst, "POST")

            call_req = mock_urlopen.call_args[0][0]
            self.assertEqual(call_req.method, "POST",
                             "POST /api/session should remain POST")

    def test_no_fake_success_code_for_config(self):
        """Verify _proxy_request doesn't have a hardcoded 200 for /config.

        The fix (Bug 3) should forward the request instead of returning
        a fake 200. We verify by checking that _proxy_request for /config
        actually calls urlopen (i.e., forwards the request).
        """
        inst = _make_mock_instance("/api/config")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b'{"ok":true}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            server.MiMoProxyHandler._proxy_request(inst, "POST")

            # Verify urlopen was actually called (request is proxied, not faked)
            mock_urlopen.assert_called_once()

    def test_no_fake_success_pattern_in_source(self):
        """Verify no hardcoded 'send_response(200)' for /config bypass exists.

        Read the source and assert there is no pattern where the code
        checks path == "/config" and immediately returns 200 without
        calling urlopen.
        """
        with open(os.path.join(SRC_DIR, "server.py"), "r", encoding="utf-8") as f:
            source = f.read()

        # The legitimate send_response(200) calls are:
        #   1. In the stream branch (after urlopen success)
        #   2. In the non-stream branch (after urlopen success)
        #   3. In _handle_chat_via_mimo (fallback)
        #   4. In do_OPTIONS
        # None of these bypass the proxy for /config specifically.
        # The test just verifies the code doesn't short-circuit /config.
        lines = source.split('\n')
        fake_config_found = False
        for i, line in enumerate(lines):
            # Look for a pattern where path check on /config is
            # followed by send_response(200) within 3 lines,
            # WITHOUT any urlopen call between them
            if '"/config"' in line or "'/config'" in line:
                for j in range(i + 1, min(i + 4, len(lines))):
                    if 'send_response(200)' in lines[j]:
                        # Check if there's a urlopen call between config check
                        # and send_response — if not, it's a fake response
                        between = '\n'.join(lines[i:j + 1])
                        if 'urlopen' not in between:
                            fake_config_found = True

        self.assertFalse(fake_config_found,
                         "Found suspicious pattern: /config check followed by "
                         "send_response(200) without urlopen")


class TestBug4ProviderFiltering(unittest.TestCase):
    """Bug 4: provider 裁剪丢失模型"""

    def _test_provider_filter(self, raw_data):
        """Helper: call _proxy_request for /provider and return output."""
        inst = _make_mock_instance("/api/provider")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = json.dumps(raw_data).encode()
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            server.MiMoProxyHandler._proxy_request(inst, "GET")
            return inst.wfile.getvalue()

    def test_connected_providers_kept(self):
        """Verify connected providers are kept regardless of name."""
        raw_data = {
            "all": [
                {"id": "openai/gpt4", "name": "OpenAI GPT-4"},
                {"id": "anthropic/claude", "name": "Anthropic Claude"},
            ],
            "connected": ["openai/gpt4"]
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b"openai/gpt4", output,
                      "Connected provider should be kept")
        self.assertNotIn(b"anthropic/claude", output,
                         "Non-connected, non-mimo provider should be filtered out")

    def test_mimo_providers_kept_by_name(self):
        """Verify providers with 'mimo' in id are kept."""
        raw_data = {
            "all": [
                {"id": "mimo/mimo-auto", "name": "MiMo Auto"},
                {"id": "custom/mimo-local", "name": "MiMo Local"},
            ],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b"mimo/mimo-auto", output)
        self.assertIn(b"custom/mimo-local", output)

    def test_xiaomi_providers_kept(self):
        """Verify providers with 'xiaomi' in id are kept."""
        raw_data = {
            "all": [{"id": "xiaomi/xiaoai", "name": "XiaoAi"}],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b"xiaomi/xiaoai", output)

    def test_mi_prefix_providers_kept(self):
        """Verify providers starting with 'mi-' are kept."""
        raw_data = {
            "all": [
                {"id": "mi-vision", "name": "Mi Vision"},
                {"id": "mi-assistant", "name": "Mi Assistant"},
            ],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b"mi-vision", output)
        self.assertIn(b"mi-assistant", output)

    def test_exact_mi_provider_kept(self):
        """Verify provider with exact id 'mi' is kept."""
        raw_data = {
            "all": [{"id": "mi", "name": "Mi"}],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b'"mi"', output)

    def test_irrelevant_providers_filtered_out(self):
        """Verify non-mimo, non-connected providers are filtered out."""
        raw_data = {
            "all": [
                {"id": "openai/gpt4", "name": "OpenAI GPT-4"},
                {"id": "google/gemini", "name": "Google Gemini"},
            ],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertNotIn(b"openai/gpt4", output)
        self.assertNotIn(b"google/gemini", output)

    def test_provider_with_connected_flag_kept(self):
        """Verify providers with connected=True flag are kept."""
        raw_data = {
            "all": [
                {"id": "custom/my-model", "name": "My Model", "connected": True},
            ],
            "connected": []
        }
        output = self._test_provider_filter(raw_data)
        self.assertIn(b"custom/my-model", output)

    def test_invalid_json_does_not_crash(self):
        """Verify invalid JSON from provider endpoint doesn't crash."""
        inst = _make_mock_instance("/api/provider")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b"not-valid-json{{{"
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            # Should not raise
            server.MiMoProxyHandler._proxy_request(inst, "GET")

            output = inst.wfile.getvalue()
            self.assertIn(b"not-valid-json", output,
                          "Invalid JSON should pass through unchanged")


class TestBug5IndexHtmlResponseFormat(unittest.TestCase):
    """Bug 5: index.html {info, parts} response format"""

    def test_frontend_has_info_parts_branch(self):
        """Verify index.html has a branch for {info, parts} format."""
        html_path = os.path.join(SRC_DIR, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("result.info && result.parts", content,
                      "index.html should detect {info, parts} format")
        self.assertIn("result.parts.filter(p => p.type === 'text')", content,
                      "Should filter text parts from the response")

        # {info, parts} check should come BEFORE result.content (higher priority)
        info_parts_idx = content.index("result.info && result.parts")
        content_idx = content.index("result.content")
        self.assertLess(info_parts_idx, content_idx,
                        "{info, parts} check should come before content fallback")


class TestBug6S6Dependency(unittest.TestCase):
    """Bug 6: s6 缺少依赖"""

    def setUp(self):
        self.root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rootfs"
        )

    def test_dependency_file_exists(self):
        """Verify the dependencies.d/mimocode file exists."""
        dep_path = os.path.join(
            self.root, "etc", "s6-overlay", "s6-rc.d",
            "mimocode-webui", "dependencies.d", "mimocode"
        )
        self.assertTrue(os.path.exists(dep_path),
                        f"Dependency file should exist at {dep_path}")

    def test_dependency_content_is_correct(self):
        """Verify the dependency file contains 'mimocode'."""
        dep_path = os.path.join(
            self.root, "etc", "s6-overlay", "s6-rc.d",
            "mimocode-webui", "dependencies.d", "mimocode"
        )
        with open(dep_path, "r") as f:
            content = f.read().strip()
        self.assertEqual(content, "mimocode",
                         "Dependency file should contain 'mimocode'")

    def test_user_contents_d_mentions_both_services(self):
        """Verify user/contents.d has both mimocode and mimocode-webui."""
        base = os.path.join(
            self.root, "etc", "s6-overlay", "s6-rc.d", "user", "contents.d"
        )
        self.assertTrue(os.path.exists(os.path.join(base, "mimocode")))
        self.assertTrue(os.path.exists(os.path.join(base, "mimocode-webui")))


class TestProxyRequestCore(unittest.TestCase):
    """Core proxy request functionality tests."""

    @patch("urllib.request.urlopen")
    def test_target_path_strips_api_prefix(self, mock_urlopen):
        """Verify /api prefix is stripped from target URL."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = b'{}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inst = _make_mock_instance("/api/session/abc-123")
        server.MiMoProxyHandler._proxy_request(inst, "GET")

        call_req = mock_urlopen.call_args[0][0]
        self.assertIn("/session/abc-123", call_req.full_url,
                      "Target URL should strip /api prefix")

    def test_is_stream_regex_matches_correctly(self):
        """Direct test of the stream regex pattern."""
        pattern = re.compile(r"^/session/[^/]+/message$")
        self.assertIsNotNone(pattern.match("/session/abc-123/message"))
        self.assertIsNotNone(pattern.match("/session/session123/message"))
        self.assertIsNone(pattern.match("/session/abc-123/message/extra"))
        self.assertIsNone(pattern.match("/config"))
        self.assertIsNone(pattern.match("/session"))
        self.assertIsNone(pattern.match("/session/abc-123/messages"))


class TestErrorHandling(unittest.TestCase):
    """Error handling path tests."""

    @patch("urllib.request.urlopen")
    def test_http_error_returns_error_code(self, mock_urlopen):
        """Verify HTTPError returns the error code and body."""
        error_body = MagicMock()
        error_body.read.return_value = b'{"error":"not found"}'
        url_error = urllib.error.HTTPError(
            "http://127.0.0.1:14096/session/xyz", 404,
            "Not Found", {}, error_body
        )
        mock_urlopen.side_effect = url_error

        inst = _make_mock_instance("/api/session/xyz")
        server.MiMoProxyHandler._proxy_request(inst, "GET")

        # With httperror, send_response should be called with 404
        inst.send_response.assert_called_with(404)

    @patch("urllib.request.urlopen")
    def test_connection_error_triggers_fallback_for_stream(self, mock_urlopen):
        """Verify URLError on stream triggers _handle_chat_via_mimo fallback."""
        url_error = urllib.error.URLError("Connection refused")
        mock_urlopen.side_effect = url_error

        inst = _make_mock_instance("/api/session/abc/message")
        inst.rfile.read.return_value = b'{"message":"hello"}'

        # _proxy_request calls self._handle_chat_via_mimo on URLError.
        # Since the Mock auto-creates it, we can verify it was called.
        server.MiMoProxyHandler._proxy_request(inst, "POST")
        inst._handle_chat_via_mimo.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_connection_error_sends_502_for_non_stream(self, mock_urlopen):
        """Verify URLError on non-stream sends 502."""
        url_error = urllib.error.URLError("Connection refused")
        mock_urlopen.side_effect = url_error

        inst = _make_mock_instance("/api/session")
        server.MiMoProxyHandler._proxy_request(inst, "GET")
        inst.send_error.assert_called_with(
            502, "Proxy error: Connection refused"
        )


class TestChatFallback(unittest.TestCase):
    """Fallback _handle_chat_via_mimo tests."""

    @patch("os.makedirs")
    @patch("subprocess.run")
    def test_fallback_runs_mimo_command(self, mock_run, mock_makedirs):
        """Verify fallback calls 'mimo run --json' with the message."""
        mock_run.return_value.stdout = "Hello from mimo"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        inst = Mock()
        inst.wfile = BytesIO()
        inst.send_response = Mock()
        inst.send_header = Mock()
        inst.end_headers = Mock()
        inst.send_error = Mock()

        server.MiMoProxyHandler._handle_chat_via_mimo(
            inst, b'{"message":"hello"}')

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "/usr/local/bin/mimo")
        self.assertEqual(cmd[1], "run")
        self.assertEqual(cmd[2], "--json")
        self.assertEqual(cmd[3], "hello")

    @patch("os.makedirs")
    def test_fallback_empty_message_returns_early(self, mock_makedirs):
        """Verify empty message in fallback returns early with error."""
        inst = Mock()
        inst.wfile = BytesIO()
        inst.send_response = Mock()
        inst.send_header = Mock()
        inst.end_headers = Mock()

        server.MiMoProxyHandler._handle_chat_via_mimo(inst, b"{}")

        # Should send a 200 response with "Empty message"
        inst.send_response.assert_called_with(200)
        output = inst.wfile.getvalue()
        self.assertIn(b"Empty message", output)

    @patch("os.makedirs")
    @patch("subprocess.run")
    def test_fallback_timeout_returns_504(self, mock_run, mock_makedirs):
        """Verify timeout in fallback returns 504."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="mimo", timeout=180)

        inst = Mock()
        inst.wfile = BytesIO()
        inst.send_response = Mock()
        inst.send_header = Mock()
        inst.end_headers = Mock()
        inst.send_error = Mock()

        server.MiMoProxyHandler._handle_chat_via_mimo(
            inst, b'{"message":"hello"}')

        inst.send_error.assert_called_once()
        args = inst.send_error.call_args[0]
        self.assertEqual(args[0], 504)


class TestEndToEndAPI(unittest.TestCase):
    """High-level end-to-end API behavior tests."""

    def test_all_http_verbs_supported(self):
        """Verify all expected HTTP methods are supported for /api/ paths."""
        handler = server.MiMoProxyHandler
        self.assertTrue(hasattr(handler, 'do_GET'))
        self.assertTrue(hasattr(handler, 'do_POST'))
        self.assertTrue(hasattr(handler, 'do_DELETE'))
        self.assertTrue(hasattr(handler, 'do_PATCH'))
        self.assertTrue(hasattr(handler, 'do_OPTIONS'))

    def test_cors_headers_in_options(self):
        """Verify OPTIONS response has proper CORS headers."""
        inst = MagicMock()
        server.MiMoProxyHandler.do_OPTIONS(inst)

        inst.send_response.assert_called_once_with(200)
        inst.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
        inst.send_header.assert_any_call(
            "Access-Control-Allow-Methods",
            "GET, POST, DELETE, PATCH, OPTIONS"
        )

    def test_get_api_calls_proxy(self):
        """Verify GET /api/session calls _proxy_request."""
        inst = _make_real_handler("/api/session")
        with patch.object(server.MiMoProxyHandler,
                          '_proxy_request') as mock_proxy:
            server.MiMoProxyHandler.do_GET(inst)
            mock_proxy.assert_called_once_with("GET")

    def test_get_non_api_does_not_call_proxy(self):
        """Verify GET to non-/api/ paths doesn't call _proxy_request."""
        inst = _make_real_handler("/index.html")
        with patch.object(server.MiMoProxyHandler,
                          '_proxy_request') as mock_proxy:
            try:
                server.MiMoProxyHandler.do_GET(inst)
            except Exception:
                pass  # May fail trying to serve a file, that's fine
            mock_proxy.assert_not_called()


class TestCodeQuality(unittest.TestCase):
    """Code quality checks."""

    def test_server_py_compiles(self):
        """Verify server.py has valid Python syntax."""
        import py_compile
        filepath = os.path.join(SRC_DIR, "server.py")
        try:
            py_compile.compile(filepath, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"server.py syntax error: {e}")

    def test_tcp_proxy_py_compiles(self):
        """Verify tcp_proxy.py has valid Python syntax."""
        import py_compile
        filepath = os.path.join(SRC_DIR, "tcp_proxy.py")
        try:
            py_compile.compile(filepath, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"tcp_proxy.py syntax error: {e}")

    def test_no_absolute_paths_in_html(self):
        """Verify index.html uses relative base path for API calls."""
        html_path = os.path.join(SRC_DIR, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("window.location.pathname", content,
                      "Should use relative base path for API calls")
        self.assertIn("const API = BASE_PATH + '/api'", content,
                      "API path should be constructed from relative base path")


class TestHAComponentCompatibility(unittest.TestCase):
    """Compatibility checks between WebUI server and HA components."""

    def test_agent_impl_has_json_stream_parser(self):
        """Verify agent_impl.py has _parse_json_stream method."""
        ha_path = os.path.join(HA_COMPONENTS_DIR, "agent_impl.py")
        self.assertTrue(os.path.exists(ha_path),
                        f"agent_impl.py should exist at {ha_path}")
        with open(ha_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("_parse_json_stream", content,
                      "agent_impl.py should have a JSON stream parser")
        self.assertIn('info = obj.get("info", {})', content,
                      "Should extract 'info' from NDJSON response")
        self.assertIn('parts = obj.get("parts", [])', content,
                      "Should extract 'parts' from NDJSON response")

    def test_agent_impl_text_part_extraction(self):
        """Verify agent_impl.py extracts text parts from {info, parts} response."""
        ha_path = os.path.join(HA_COMPONENTS_DIR, "agent_impl.py")
        with open(ha_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('ptype == "text"', content,
                      "Should filter for text-type parts")
        self.assertIn('part.get("text", "")', content,
                      "Should extract text content from parts")


class TestEndToEndFlow(unittest.TestCase):
    """End-to-end data flow tests."""

    def test_send_message_payload_format(self):
        """Verify the message payload format sent by frontend matches agent_impl.py."""
        html_path = os.path.join(SRC_DIR, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('body: { message: text, parts: [{ type: \'text\', text: text }] }',
                      html,
                      "Frontend should send {message, parts} to /session/{id}/message")

        ha_path = os.path.join(HA_COMPONENTS_DIR, "agent_impl.py")
        with open(ha_path, "r", encoding="utf-8") as f:
            agent = f.read()

        self.assertIn('"message": message', agent,
                      "agent_impl should send 'message' field")
        self.assertIn('"parts": [{"type": "text", "text": message}]', agent,
                      "agent_impl should send 'parts' field with text type")

    def test_info_parts_response_compatibility(self):
        """Verify end-to-end: both WebUI frontend and HA agent_impl
        can parse the {info, parts} response format from mimo serve."""
        html_path = os.path.join(SRC_DIR, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        ha_path = os.path.join(HA_COMPONENTS_DIR, "agent_impl.py")
        with open(ha_path, "r", encoding="utf-8") as f:
            agent_content = f.read()

        self.assertIn("parts", html_content, "Frontend should reference 'parts'")
        self.assertIn("parts", agent_content, "Agent should reference 'parts'")
        self.assertIn("info", html_content, "Frontend should reference 'info'")
        self.assertIn("info", agent_content, "Agent should reference 'info'")


if __name__ == "__main__":
    unittest.main()

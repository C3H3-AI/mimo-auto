"""Coordinator for managing the MiMo Auto server process lifecycle."""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUTO_INSTALL,
    CONF_MIMO_BIN,
    CONF_PORT,
    DEFAULT_AUTO_INSTALL,
    DEFAULT_MIMO_BIN,
    DEFAULT_PORT,
    HEALTH_CHECK_INTERVAL_SECONDS,
    MAX_RESTART_ATTEMPTS,
    SERVER_START_TIMEOUT_SECONDS,
    SERVER_STOP_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class MiMoCoordinator:
    """Manages the lifecycle of the mimo serve subprocess.

    This coordinator is responsible for starting, stopping, and monitoring
    the health of the `mimo serve` process. It handles automatic restarts
    if the process crashes unexpectedly.
    """

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the coordinator.

        Args:
            hass: The HomeAssistant instance.
            config: Configuration dictionary containing port and binary path.
        """
        self._hass = hass
        self._port: int = config.get(CONF_PORT, DEFAULT_PORT)
        self._mimo_bin: str = config.get(CONF_MIMO_BIN, DEFAULT_MIMO_BIN)
        self._auto_install: bool = config.get(CONF_AUTO_INSTALL, DEFAULT_AUTO_INSTALL)
        self._process: asyncio.subprocess.Process | None = None
        self._health_check_task: asyncio.Task | None = None
        self._restart_count: int = 0
        self._external_mode: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        self._server_url: str = f"http://127.0.0.1:{self._port}"

    @property
    def is_running(self) -> bool:
        """Return whether the mimo server process is currently running."""
        return self._process is not None and self._process.returncode is None

    @property
    def port(self) -> int:
        """Return the port the server is configured to run on."""
        return self._port

    @property
    def server_url(self) -> str:
        """Return the base URL of the server."""
        return self._server_url

    async def start_server(self) -> bool:
        """Start or connect to the mimo serve process.

        First attempts to connect to an already-running server at the configured
        port. If none is found, resolves and starts the mimo binary as a subprocess.

        This dual-mode approach supports:
        - Docker deployments where mimo runs on the host (host networking)
        - Local installations where HA starts mimo as a subprocess directly

        Returns:
            True if the server started or connected successfully, False otherwise.
        """
        async with self._lock:
            if self.is_running:
                _LOGGER.warning("MiMo server is already running")
                return True

            # Step 1: Try connecting to an existing server first
            if await self._check_server_healthy():
                _LOGGER.info(
                    "Connected to existing MiMo server at %s",
                    self._server_url,
                )
                self._mark_running()
                return True

            # Step 2: No existing server found, try to start one as subprocess
            mimo_path = await self._resolve_mimo_binary()
            if mimo_path is None:
                if self._auto_install:
                    _LOGGER.info("mimo binary not found, attempting auto-install...")
                    installed = await self._auto_install_mimo()
                    if installed:
                        mimo_path = await self._resolve_mimo_binary()

                if mimo_path is None:
                    _LOGGER.error(
                        "Could not find mimo binary and no existing server at %s. "
                        "Searched PATH and configured path: %s. "
                        "Install it with: npm install -g @mimo-ai/cli",
                        self._server_url,
                        self._mimo_bin,
                    )
                    return False

            _LOGGER.info(
                "Starting MiMo server: %s serve --port %d",
                mimo_path,
                self._port,
            )

            try:
                self._process = await asyncio.create_subprocess_exec(
                    mimo_path,
                    "serve",
                    "--port",
                    str(self._port),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, PermissionError) as err:
                _LOGGER.error("Failed to start mimo process: %s", err)
                self._process = None
                return False

            # Wait for the server to become responsive
            if not await self._wait_for_server_ready(SERVER_START_TIMEOUT_SECONDS):
                _LOGGER.error(
                    "MiMo server did not become ready within %d seconds",
                    SERVER_START_TIMEOUT_SECONDS,
                )
                await self._stop_process()
                return False

            _LOGGER.info(
                "MiMo server started successfully on port %d (PID: %d)",
                self._port,
                self._process.pid,
            )
            self._restart_count = 0

            # Start health check loop
            self._start_health_check()

            # Start background readers for stdout/stderr to prevent pipe buffer issues
            asyncio.create_task(self._read_stream(self._process.stdout, "stdout"))
            asyncio.create_task(self._read_stream(self._process.stderr, "stderr"))

            return True

    def _mark_running(self) -> None:
        """Mark coordinator as 'running' when connected to an external server.

        Sets a sentinel process so that is_running returns True, and
        starts a lightweight health check loop.
        """
        from types import SimpleNamespace
        self._process = SimpleNamespace(returncode=None, pid="external")
        self._external_mode = True
        self._restart_count = 0
        self._start_health_check()

    async def stop_server(self) -> bool:
        """Stop the mimo serve subprocess gracefully.

        Sends a SIGTERM (or CTRL_BREAK_EVENT on Windows) and waits for
        the process to exit. If the process does not stop within the timeout,
        it is killed forcefully.

        Returns:
            True if the server stopped successfully, False otherwise.
        """
        async with self._lock:
            if not self.is_running:
                _LOGGER.debug("MiMo server is not running, nothing to stop")
                return True

            return await self._stop_process()

    async def restart_server(self) -> bool:
        """Restart the mimo serve subprocess.

        Stops the server if running, then starts it again. Resets the
        restart counter.

        Returns:
            True if the server restarted successfully, False otherwise.
        """
        _LOGGER.info("Restarting MiMo server...")
        await self.stop_server()
        self._restart_count = 0
        return await self.start_server()

    async def async_check_health(self) -> bool:
        """Check if the server is healthy by making a lightweight request.

        Sends a GET request to the /session endpoint of the server to verify
        it is still responsive. In external server mode, simply checks HTTP.

        Returns:
            True if the server responded successfully, False otherwise.
        """
        if not self._process or self._process.returncode is not None:
            _LOGGER.warning("MiMo server process is no longer running")
            return False

        # In external server mode, just check HTTP reachability
        if getattr(self._process, "pid", None) == "external":
            return await self._check_server_healthy()

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"http://127.0.0.1:{self._port}/session",
                    timeout=5,
                ) as response:
                    if response.status == 200:
                        return True
                    _LOGGER.warning(
                        "MiMo server health check returned status %d",
                        response.status,
                    )
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("MiMo server health check failed: %s", err)
            return False

    async def _resolve_mimo_binary(self) -> str | None:
        """Resolve the full path to the mimo binary.

        Checks the configured path first, then falls back to PATH lookup.
        On Windows, handles .cmd and .exe extensions.

        Returns:
            The full path to the mimo binary, or None if not found.
        """
        # If user configured a specific path, use it directly
        if self._mimo_bin != DEFAULT_MIMO_BIN:
            mimo_path = await self._hass.async_add_executor_job(
                shutil.which, self._mimo_bin
            )
            if mimo_path:
                return mimo_path
            # Maybe it's a direct path
            return self._mimo_bin if self._mimo_bin else None

        # Try to find mimo in PATH
        mimo_path = await self._hass.async_add_executor_job(shutil.which, "mimo")
        if mimo_path:
            return mimo_path

        # On Windows, check common locations
        if self._hass.config.config_dir and self._hass.config.config_dir.startswith(
            ("C:", "D:")
        ):
            common_paths = [
                r"C:\Users\duola\.workbuddy\binaries\node\versions\22.22.2\mimo.cmd",
                r"C:\Users\duola\.workbuddy\binaries\node\versions\22.22.2\mimo",
                r"C:\Users\duola\AppData\Roaming\npm\mimo.cmd",
                r"C:\Users\duola\AppData\Roaming\npm\mimo",
            ]
            for path in common_paths:
                exists = await self._hass.async_add_executor_job(
                    _check_file_exists, path
                )
                if exists:
                    return path

        return None

    async def _auto_install_mimo(self) -> bool:
        """Attempt to auto-install mimo via npm.

        Returns:
            True if installation succeeded, False otherwise.
        """
        # Check if node/npm is available
        node_path = await self._hass.async_add_executor_job(shutil.which, "node")
        npm_path = await self._hass.async_add_executor_job(shutil.which, "npm")

        if not node_path or not npm_path:
            _LOGGER.warning(
                "Cannot auto-install mimo: Node.js or npm not found. "
                "Install Node.js first, then run: npm install -g @mimo-ai/cli"
            )
            return False

        _LOGGER.info("Auto-installing mimo via npm (node=%s, npm=%s)...", node_path, npm_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                npm_path, "install", "-g", "@mimo-ai/cli",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
            if proc.returncode == 0:
                _LOGGER.info("mimo auto-install succeeded")
                return True
            else:
                _LOGGER.error(
                    "mimo auto-install failed (exit=%d): %s",
                    proc.returncode,
                    (stderr or stdout).decode("utf-8", errors="replace")[:500],
                )
                return False
        except asyncio.TimeoutError:
            _LOGGER.error("mimo auto-install timed out after 120 seconds")
            return False
        except Exception as err:
            _LOGGER.error("mimo auto-install error: %s", err)
            return False

    async def _stop_process(self) -> bool:
        """Stop the underlying process.

        Returns:
            True if the process stopped, False otherwise.
        """
        # Stop health check first (even if process is already None, we
        # need to cancel the health check loop for external mode tracking)
        self._stop_health_check()

        if self._process is None:
            return True

        pid = self._process.pid
        _LOGGER.info("Stopping MiMo server (PID: %d)...", pid)

        try:
            if self._process.returncode is None:
                # Check if we're in "external server" mode (no real process to stop)
                if getattr(self._process, "pid", None) == "external":
                    _LOGGER.debug("External server mode - no local process to stop")
                    # Just mark as not running
                    return True

                # Send termination signal
                if self._hass.config.config_dir and self._hass.config.config_dir.startswith(
                    ("C:", "D:")
                ):
                    # Windows
                    self._process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self._process.terminate()

                # Wait for process to exit
                try:
                    async with asyncio.timeout(SERVER_STOP_TIMEOUT_SECONDS):
                        await self._process.wait()
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "MiMo server did not stop gracefully, killing (PID: %d)",
                        pid,
                    )
                    self._process.kill()
                    await self._process.wait()

            _LOGGER.info("MiMo server stopped (PID: %d)", pid)
        except ProcessLookupError:
            _LOGGER.debug("MiMo server process already exited (PID: %d)", pid)
        except Exception as err:
            _LOGGER.error("Error stopping MiMo server: %s", err)
            return False
        finally:
            self._process = None

        return True

    async def _check_server_healthy(self) -> bool:
        """Check if a mimo server is already running at the configured URL.

        Tests the /session endpoint (which returns 200 with a session list)
        and the root / endpoint. The root returns 503 ("Web UI unavailable")
        which is also a valid server response.

        Returns:
            True if a server responded, False otherwise.
        """
        for path in ("/session", "/"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self._server_url}{path}",
                        timeout=3,
                    ) as response:
                        _LOGGER.debug(
                            "Server check %s -> HTTP %d",
                            path,
                            response.status,
                        )
                        # 200 = healthy, 503 = web UI unavailable but server works
                        if response.status in (200, 503, 404):
                            return True
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.debug("Server check %s failed: %s", path, err)
                continue
        return False

    async def _wait_for_server_ready(self, timeout: int) -> bool:
        """Poll the server until it responds or timeout is reached.

        Args:
            timeout: Maximum number of seconds to wait.

        Returns:
            True if the server became ready, False on timeout.
        """
        start_time = self._hass.loop.time()
        while (self._hass.loop.time() - start_time) < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{self._port}/session",
                        timeout=2,
                    ) as response:
                        if response.status == 200:
                            return True
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass

            # Also check if the process crashed during startup
            if self._process and self._process.returncode is not None:
                _LOGGER.error(
                    "MiMo server process exited during startup with code %d",
                    self._process.returncode,
                )
                return False

            await asyncio.sleep(0.5)

        return False

    def _start_health_check(self) -> None:
        """Start the periodic health check loop."""
        self._stop_health_check()
        self._health_check_task = self._hass.async_create_task(
            self._health_check_loop(),
            name="mimo_auto_health_check",
        )

    def _stop_health_check(self) -> None:
        """Stop the periodic health check loop."""
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            self._health_check_task = None

    async def _health_check_loop(self) -> None:
        """Periodic health check loop.

        Checks the server health every HEALTH_CHECK_INTERVAL_SECONDS.
        If the server becomes unresponsive, attempts to restart it
        (up to MAX_RESTART_ATTEMPTS times).
        """
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)

                if not self.is_running:
                    _LOGGER.warning("MiMo server process has exited unexpectedly")
                    await self._handle_crash()
                    continue

                healthy = await self.async_check_health()
                if not healthy:
                    _LOGGER.warning("MiMo server is not healthy, attempting restart")
                    await self._handle_crash()

            except asyncio.CancelledError:
                _LOGGER.debug("Health check loop cancelled")
                break
            except Exception as err:
                _LOGGER.error("Unexpected error in health check loop: %s", err)

    async def _handle_crash(self) -> None:
        """Handle a server crash with automatic restart logic."""
        self._restart_count += 1

        # External server mode: never permanently give up — the server
        # can be restarted independently on the host and may come back.
        if self._external_mode:
            if self._restart_count > MAX_RESTART_ATTEMPTS:
                _LOGGER.warning(
                    "MiMo server health check failed %d times. "
                    "Resetting retry counter and will keep polling...",
                    self._restart_count,
                )
                self._restart_count = 0
            await self._stop_process()  # sets _process = None in finally
            await self.start_server()   # reconnects to external server
            return

        if self._restart_count > MAX_RESTART_ATTEMPTS:
            _LOGGER.error(
                "MiMo server has crashed %d times. "
                "Giving up after %d attempts.",
                self._restart_count,
                MAX_RESTART_ATTEMPTS,
            )
            return

        _LOGGER.info(
            "Attempting to restart MiMo server (attempt %d/%d)...",
            self._restart_count,
            MAX_RESTART_ATTEMPTS,
        )

        await self._stop_process()

        success = await self.start_server()
        if success:
            _LOGGER.info("MiMo server restart successful")
        else:
            _LOGGER.error("MiMo server restart attempt %d failed", self._restart_count)

    @staticmethod
    async def _read_stream(
        stream: asyncio.StreamReader | None, stream_name: str
    ) -> None:
        """Read and log data from a subprocess stream.

        This prevents pipe buffer deadlocks by continuously reading
        from the stdout/stderr pipes of the subprocess.

        Args:
            stream: The stream reader to read from.
            stream_name: Name of the stream for logging ("stdout" or "stderr").
        """
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    if stream_name == "stderr":
                        _LOGGER.debug("[mimo stderr] %s", text)
                    else:
                        _LOGGER.debug("[mimo stdout] %s", text)
        except (ValueError, OSError) as err:
            _LOGGER.debug("Stream reader for %s ended: %s", stream_name, err)


def _check_file_exists(path: str) -> bool:
    """Check if a file exists at the given path.

    Args:
        path: The file path to check.

    Returns:
        True if the file exists, False otherwise.
    """
    import os
    return os.path.isfile(path)

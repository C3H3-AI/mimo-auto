"""SSH client for HA system operations.

Provides SSH connection management for system-level operations:
updates, backups, host management, and configuration changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Default SSH connection parameters
DEFAULT_SSH_TIMEOUT = 30
DEFAULT_COMMAND_TIMEOUT = 60


class SSHClient:
    """SSH client for HA system operations.

    Uses async SSH to execute commands on the HA host.
    """

    def __init__(
        self,
        hass,
        host: str | None = None,
        port: int = 22,
        username: str = "root",
        key_path: str | None = None,
        timeout: int = DEFAULT_SSH_TIMEOUT,
    ) -> None:
        """Initialize the SSH client.

        Args:
            hass: HomeAssistant instance.
            host: SSH host address. If None, auto-detect from HA config.
            port: SSH port.
            username: SSH username.
            key_path: Path to SSH private key.
            timeout: Connection timeout in seconds.
        """
        self._hass = hass
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path or os.path.expanduser("~/.ssh/id_ha")
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        """Check if SSH client is configured."""
        return self._host is not None

    async def execute_command(
        self,
        command: str,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Execute a command via SSH.

        Args:
            command: Command to execute.
            timeout: Command timeout in seconds.

        Returns:
            Dict with stdout, stderr, and return_code.
        """
        if not self._host:
            return {"error": "SSH host not configured"}

        try:
            # Build SSH command
            ssh_cmd = [
                "ssh",
                "-i", self._key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=" + str(self._timeout),
                "-p", str(self._port),
                f"{self._username}@{self._host}",
                command,
            ]

            # Execute via asyncio subprocess
            process = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {"error": f"Command timed out after {timeout}s"}

            return {
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
                "return_code": process.returncode,
            }

        except Exception as err:
            _LOGGER.error("SSH command failed: %s", err)
            return {"error": str(err)}

    async def execute_ha_command(self, command: str) -> dict[str, Any]:
        """Execute an HA CLI command.

        Args:
            command: HA CLI command (e.g., "core restart", "backups new").

        Returns:
            Command execution result.
        """
        return await self.execute_command(f"ha {command}")

    async def restart_ha(self) -> dict[str, Any]:
        """Restart Home Assistant Core.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("core restart")

    async def update_ha(self) -> dict[str, Any]:
        """Update Home Assistant Core.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("core update")

    async def create_backup(self, name: str | None = None) -> dict[str, Any]:
        """Create a Home Assistant backup.

        Args:
            name: Optional backup name.

        Returns:
            Command execution result.
        """
        cmd = "backups new"
        if name:
            cmd += f' --name "{name}"'
        return await self.execute_ha_command(cmd)

    async def list_backups(self) -> dict[str, Any]:
        """List all Home Assistant backups.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("backups list")

    async def restore_backup(self, slug: str) -> dict[str, Any]:
        """Restore a Home Assistant backup.

        Args:
            slug: Backup slug to restore.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command(f"backups restore {slug}")

    async def reboot_host(self) -> dict[str, Any]:
        """Reboot the HA host.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("host reboot")

    async def shutdown_host(self) -> dict[str, Any]:
        """Shutdown the HA host.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("host shutdown")

    async def get_host_info(self) -> dict[str, Any]:
        """Get HA host information.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command("info")

    async def get_addon_info(self, addon: str) -> dict[str, Any]:
        """Get information about an addon.

        Args:
            addon: Addon slug.

        Returns:
            Command execution result.
        """
        return await self.execute_ha_command(f"addons info {addon}")

    async def health_check(self) -> bool:
        """Check if SSH connection is working.

        Returns:
            True if SSH is reachable, False otherwise.
        """
        if not self._host:
            return False

        result = await self.execute_command("echo ok", timeout=5)
        return result.get("return_code") == 0 and result.get("stdout") == "ok"

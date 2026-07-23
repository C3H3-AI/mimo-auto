#!/usr/bin/env python3
"""Deploy the latest mimo-code webui files to the running HA addon container,
and prepare the persistent bind mount so the files survive container restart."""

import base64, subprocess, sys, tempfile, os

REPO = r"D:\ai-hub\integrations\mimo_auto"
CODE_DIR = os.path.join(REPO, "mimo-code", "rootfs", "usr", "share", "mimocode", "webui")
S6_FILE = os.path.join(REPO, "mimo-code", "rootfs", "etc", "s6-overlay", "s6-rc.d", "mimocode-webui", "run")

# Files to deploy (code files only, not assets/SPA — those were already deployed)
FILES = [
    "client.py", "channel_manager.py", "feishu_client.py",
    "server.py", "session_store.py",
    "ha_context.py", "ha_entities.py", "ha_services.py",
    "base_channel.py", "card.py", "evolution_review.py",
    "media.py", "media_utils.py",
]

SSH_CMD = [
    "ssh", "-i", os.path.expanduser("~/.ssh/id_ha"),
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=20",
    "root@api.homediy.top",
]

def run_remote(script: str) -> str:
    """Run a bash script on the remote host via SSH pipe."""
    proc = subprocess.run(
        SSH_CMD + ["bash", "-s"],
        input=script.encode("utf-8"),
        capture_output=True, timeout=120
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"SSH exited {proc.returncode}")
        print(f"STDERR: {err[:500]}")
    return out + err

def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")

def main():
    # Build the remote script
    lines = [
        "set -e",
        'echo "=== Step 1: copy files to persistent bind mount ==="',
        # Copy the files already on host at /data/mimocode/webui/ to the REAL persistent path
        'cp -r /data/mimocode/webui/. /mnt/data/supervisor/apps/data/local_mimo-code/mimocode/webui/ 2>/dev/null || true',
        'echo "=== Step 2: deploy updated files via base64 ==="',
    ]

    for fname in FILES:
        local_path = os.path.join(CODE_DIR, fname)
        if not os.path.exists(local_path):
            print(f"WARNING: {local_path} not found, skipping")
            continue
        b = b64(local_path)
        # Use python3 on Alpine to decode (base64 -d may not support -w)
        lines.append(f'echo "{b}" | python3 -m base64 -d > /tmp/{fname}')
        lines.append(f'docker cp /tmp/{fname} addon_local_mimo-code:/usr/share/mimocode/webui/{fname}')
        # Also write to persistent mount
        lines.append(f'cp /tmp/{fname} /mnt/data/supervisor/apps/data/local_mimo-code/mimocode/webui/{fname}')
        lines.append(f'echo "  {fname} deployed"')

    # Deploy s6 overlay script
    s6_b64 = b64(S6_FILE)
    lines.append('echo "=== Step 3: deploy s6 overlay script ==="')
    lines.append(f'echo "{s6_b64}" | python3 -m base64 -d > /tmp/mimocode-webui-run')
    lines.append('docker cp /tmp/mimocode-webui-run addon_local_mimo-code:/etc/s6-overlay/s6-rc.d/mimocode-webui/run')
    # Also copy to persistent mount (for after restart)
    lines.append('cp /tmp/mimocode-webui-run /mnt/data/supervisor/apps/data/local_mimo-code/mimocode/webui/run 2>/dev/null || true')

    lines.append('echo "=== Step 4: verify ==="')
    lines.append('echo "MimoAPIError:"')
    lines.append("docker exec addon_local_mimo-code grep -c 'class MimoAPIError' /usr/share/mimocode/webui/client.py")
    lines.append('echo "s6 overlay:"')
    lines.append("docker exec addon_local_mimo-code grep 'Overlay deployed code' /etc/s6-overlay/s6-rc.d/mimocode-webui/run")
    lines.append('echo "server.py migration:"')
    lines.append("docker exec addon_local_mimo-code grep 'Migrated legacy' /usr/share/mimocode/webui/server.py")
    lines.append('echo "session_store path:"')
    lines.append("docker exec addon_local_mimo-code grep 'DEFAULT_PATH' /usr/share/mimocode/webui/session_store.py")
    lines.append('echo "persistent mount files:"')
    lines.append('ls /mnt/data/supervisor/apps/data/local_mimo-code/mimocode/webui/ | head -20')

    lines.append('echo "=== Step 5: restart ==="')
    lines.append('ha apps restart local_mimo-code 2>&1')
    lines.append('echo "Restart issued. Waiting 20s..."')
    lines.append('sleep 20')
    lines.append('echo "=== Step 6: post-restart verify ==="')
    lines.append("docker exec addon_local_mimo-code grep -c 'class MimoAPIError' /usr/share/mimocode/webui/client.py 2>/dev/null || echo 'MimoAPIError: 0 (failed)'")
    lines.append('docker logs --tail 8 addon_local_mimo-code 2>&1')
    lines.append('echo DONE')

    script = "\n".join(lines)
    print("Running deployment script on HA host...")
    print(f"Script size: {len(script)} bytes, {len(FILES)} files")
    result = run_remote(script)
    print(result[-2000:] if len(result) > 2000 else result)

if __name__ == "__main__":
    main()

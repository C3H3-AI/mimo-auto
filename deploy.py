#!/usr/bin/env python3
"""Deploy MiMo WebUI to HA addon container."""
import os
import subprocess
import tarfile
import tempfile
import time

WEBUI_DIR = r'D:\ai-hub\integrations\mimo_auto\webui'
SSH_KEY = os.path.expanduser('~/.ssh/id_ha')
SSH_HOST = 'root@api.homediy.top'
CONTAINER = 'addon_local_mimo-code'
ADDON_PATH = '/usr/share/mimocode/webui/dist'

def run(cmd, **kwargs):
    print(f"[CMD] {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return subprocess.run(cmd, **kwargs)

def main():
    dist_dir = os.path.join(WEBUI_DIR, 'dist')
    if not os.path.isdir(dist_dir):
        print(f'dist/ not found at {dist_dir}')
        return 1

    # Create tar
    tar_path = os.path.join(tempfile.gettempdir(), 'mimo-dist.tar.gz')
    with tarfile.open(tar_path, 'w:gz') as tar:
        tar.add(dist_dir, arcname='dist')
    print(f'Created tar: {tar_path}')

    # SCP upload
    print('=== Uploading to remote ===')
    result = run([
        'scp', '-i', SSH_KEY, '-o', 'StrictHostKeyChecking=no',
        tar_path, f'{SSH_HOST}:/tmp/mimo-dist.tar.gz'
    ])
    if result.returncode != 0:
        print('SCP failed!')
        return 1

    # Deploy to container
    print('=== Deploying to container ===')
    cmds = [
        'rm -rf /tmp/mimo-dist && mkdir /tmp/mimo-dist',
        'tar xzf /tmp/mimo-dist.tar.gz -C /tmp/mimo-dist',
        f'docker exec {CONTAINER} rm -rf {ADDON_PATH}',
        f'docker cp /tmp/mimo-dist/dist/. {CONTAINER}:{ADDON_PATH}',
        f'docker restart {CONTAINER}',
    ]
    ssh_cmd = ' && '.join(cmds)
    result = run([
        'ssh', '-i', SSH_KEY, '-o', 'StrictHostKeyChecking=no',
        SSH_HOST, ssh_cmd
    ])
    if result.returncode != 0:
        print('Deploy failed!')
        return 1

    print('Deploy OK, waiting for restart...')
    time.sleep(12)

    # Verify
    print('=== Verifying SPA ===')
    result = run([
        'ssh', '-i', SSH_KEY, '-o', 'StrictHostKeyChecking=no',
        SSH_HOST,
        f"docker exec {CONTAINER} sh -c 'curl -s -o /dev/null -w \"%{{http_code}}\" http://127.0.0.1:8099/ && echo .ok'"
    ], capture_output=True, text=True)
    ok = '200' in result.stdout
    print(f'SPA: {"OK" if ok else "FAILED"}')
    print(f'Output: {result.stdout}')
    return 0 if ok else 1

if __name__ == '__main__':
    exit(main())

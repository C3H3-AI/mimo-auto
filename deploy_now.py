#!/usr/bin/env python3
"""Deploy: write files to remote temp dir, then docker cp into container."""
import base64, subprocess, sys, os, time

REPO = r"D:\ai-hub\integrations\mimo_auto"
CODE = os.path.join(REPO, "mimo-code", "rootfs", "usr", "share", "mimocode", "webui")
S6 = os.path.join(REPO, "mimo-code", "rootfs", "etc", "s6-overlay", "s6-rc.d", "mimocode-webui", "run")
FILES = ["client.py", "channel_manager.py", "feishu_client.py", "server.py", "session_store.py"]
CONTAINER = "addon_local_mimo-code"

def b64(p):
    with open(p, "rb") as f: return base64.b64encode(f.read()).decode()

# Generate shell script that will run on the HA host
script_lines = ["set -e"]
script_lines.append("TMPD=/tmp/mimo_deploy_$$")
script_lines.append("mkdir -p $TMPD")

for fn in FILES:
    b = b64(os.path.join(CODE, fn))
    script_lines.append(f'echo "{b}" | python3 -m base64 -d > $TMPD/{fn}')

sb = b64(S6)
script_lines.append(f'echo "{sb}" | python3 -m base64 -d > $TMPD/mimocode-webui-run')

script_lines.append('echo "=== files written to $TMPD ==="')

# docker cp all files
for fn in FILES:
    script_lines.append(f'docker cp $TMPD/{fn} {CONTAINER}:/usr/share/mimocode/webui/{fn}')

script_lines.append(f'docker cp $TMPD/mimocode-webui-run {CONTAINER}:/etc/s6-overlay/s6-rc.d/mimocode-webui/run')

script_lines.append("rm -rf $TMPD")
script_lines.append("echo '=== verify ==='")
script_lines.append(f'docker exec {CONTAINER} grep -c "class MimoAPIError" /usr/share/mimocode/webui/client.py')
script_lines.append(f'docker exec {CONTAINER} grep -c "Overlay deployed code" /etc/s6-overlay/s6-rc.d/mimocode-webui/run')
script_lines.append("echo DONE")

remote_script = "\n".join(script_lines)

print("Executing remote deployment script...")
proc = subprocess.run(
    ["ssh", "-i", os.path.expanduser("~/.ssh/id_ha"),
     "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
     "root@api.homediy.top", "bash", "-s"],
    input=remote_script.encode("utf-8"),
    capture_output=True, timeout=120
)

out = proc.stdout.decode().strip()
err = proc.stderr.decode().strip()
if proc.returncode != 0:
    print(f"ERROR (rc={proc.returncode}): {err[:200]}")
print(out)

if proc.returncode == 0:
    print("=== Deploy OK. Now killing webui process for reload ===")
    subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/id_ha"),
         "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
         "root@api.homediy.top",
         "docker", "exec", CONTAINER,
         "sh", "-c", "pkill -INT -f 'python.*server.py' || true"],
        timeout=30
    )
    time.sleep(5)
    proc2 = subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/id_ha"),
         "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
         "root@api.homediy.top",
         "docker", "exec", CONTAINER,
         "grep", "-c", "class MimoAPIError", "/usr/share/mimocode/webui/client.py"],
        capture_output=True, timeout=15, text=True
    )
    print(f"After reload MimoAPIError: {proc2.stdout.strip()}")
    if proc2.stdout.strip() == "1":
        print("NEW CODE IS ACTIVE.")

# Error check: if grep output has unexpected format, it means file exists but grep found 0
# "grep: ... No such file" would mean file doesn't exist
# "0" would be file exists but pattern not found
# "1" is success

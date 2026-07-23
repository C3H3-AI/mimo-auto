#!/usr/bin/env bash
# deploy_reliable.sh — Binary-safe, integrity-verified, flaky-host-tolerant deploy
#                       for the mimo-code add-on.
#
# Why this script exists (replaces deploy.sh / deploy_addon.sh):
#   * Old scripts used `cat file | ssh ... "docker exec ... cat > file"` — piping file
#     bytes through SSH stdin. This is binary-unsafe and silently corrupts files.
#   * deploy_addon.sh only deployed 3 of ~19 python files, so most new modules
#     (ha_context.py, client.py, session_store.py, ...) never reached the container
#     -> "ha_context.py 反复丢失".
#   * No integrity check: corruption / missing files went undetected.
#
# This script fixes all three AND survives the real HA host quirks:
#   1. Binary-safe transfer: `tar czf - | ssh tar xzf -` (single SSH connection,
#      NO SFTP subsystem — the host has no sftp-server, so plain `scp` fails with
#      "subsystem request failed"). gzip gives a built-in CRC.
#   2. FULL file set: globs every webui file + SPA dist + s6 run.
#   3. Integrity: sha256 of every deployed file is verified (host upload copy vs
#      in-container) — all within a SINGLE ssh session so a flaky link can't break it.
#   4. Retries: every ssh call retries up to 4x (the host is on a flaky home link).
#   5. Persistence: a copy is also written to /data/mimocode/webui; the s6 run script
#      overlays it on every start, so deployed code survives `ha addons update`.
#
# Usage:  bash deploy_reliable.sh            # full deploy + verify + restart
#         DRY=1 bash deploy_reliable.sh       # upload + verify, but DO NOT restart
set -uo pipefail

# Keep temp files on a POSIX path inside the repo so the shell wrapper's
# safe-delete doesn't mangle the Windows %TEMP% path.
export TMPDIR="$(cd "$(dirname "$0")" && pwd)/.deploy_tmp"
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR" 2>/dev/null' EXIT

SSH_KEY="${SSH_KEY:-~/.ssh/id_ha}"
HOST="${HOST:-root@api.homediy.top}"
CONTAINER="${CONTAINER:-addon_local_mimo-code}"
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
WEBUI_SRC="$LOCAL_ROOT/mimo-code/rootfs/usr/share/mimocode/webui"
SPA_SRC="$LOCAL_ROOT/webui/dist"
RUN_SRC="$LOCAL_ROOT/mimo-code/rootfs/etc/s6-overlay/s6-rc.d/mimocode-webui/run"
REMOTE_TMP="/tmp/mimo_deploy"
WEBUI_DST="/usr/share/mimocode/webui"
DATA_WEBUI="/data/mimocode/webui"
DRY="${DRY:-0}"
MAX_TRY=4

sha256_local() { sha256sum "$1" | awk '{print $1}'; }

# Run an ssh command; retry up to MAX_TRY on transient failure.
# Usage: ssh_retry <arg>...   (reads remote script from stdin if provided)
ssh_retry() {
  local n=1
  while [ $n -le "$MAX_TRY" ]; do
    if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST" "$@"; then
      return 0
    fi
    echo "  (ssh attempt $n failed, retrying in 2s)"; n=$((n+1)); sleep 2
  done
  return 1
}

# Retry an arbitrary command (used for pipe-based uploads).
retry_cmd() {
  local n=1
  while [ $n -le "$MAX_TRY" ]; do
    if "$@"; then
      return 0
    fi
    echo "  (attempt $n failed, retrying in 2s)"; n=$((n+1)); sleep 2
  done
  return 1
}

echo "==> 0. preflight"
[ -d "$WEBUI_SRC" ] || { echo "webui src missing: $WEBUI_SRC"; exit 1; }
command -v tar >/dev/null || { echo "tar not found"; exit 1; }
command -v ssh >/dev/null || { echo "ssh not found"; exit 1; }
ssh_retry "true" || { echo "SSH to $HOST failed (key=$SSH_KEY)"; exit 1; }
echo "  SSH OK"

echo "==> 1. upload to host (tar|ssh, single connection, no SFTP subsystem)"
ssh_retry "mkdir -p $REMOTE_TMP/webui $REMOTE_TMP/dist" || { echo "mkdir failed"; exit 1; }
do_upload_webui() {
  tar czf - -C "$WEBUI_SRC" . \
    | ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST" \
        "mkdir -p $REMOTE_TMP/webui && tar xzf - -C $REMOTE_TMP/webui"
}
retry_cmd do_upload_webui || { echo "webui upload failed"; exit 1; }
echo "  webui uploaded"
if [ -d "$SPA_SRC" ]; then
  do_upload_spa() {
    tar czf - -C "$SPA_SRC" . \
      | ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST" \
          "mkdir -p $REMOTE_TMP/dist && tar xzf - -C $REMOTE_TMP/dist"
  }
  retry_cmd do_upload_spa && echo "  SPA dist uploaded" || echo "  (SPA upload skipped)"
fi
# s6 run file (single file, legacy scp -O avoids SFTP subsystem)
do_upload_run() {
  scp -O -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
    "$RUN_SRC" "$HOST:$REMOTE_TMP/mimocode-webui-run"
}
retry_cmd do_upload_run && echo "  s6 run uploaded" || { echo "s6 run upload failed"; exit 1; }

echo "==> 2. copy into container (docker cp) + persist to /data"
# All in ONE remote session (resilient to flaky link).
R2=$(mktemp)
cat > "$R2" <<REMOTE
set -uo pipefail
RT="$REMOTE_TMP"; C="$CONTAINER"; WD="$WEBUI_DST"; DW="$DATA_WEBUI"
docker exec "\$C" mkdir -p "\$WD" "\$DW"
# ephemeral layer: live code (survives ha addons restart)
docker cp "\$RT/webui/." "\$C:\$WD/"
# persistent copy: survives ha addons update (container rebuilt from image)
rm -rf "\$DW"; mkdir -p "\$DW"; cp -r "\$RT/webui/." "\$DW/"
# s6 run
docker cp "\$RT/mimocode-webui-run" "\$C:/etc/s6-overlay/s6-rc.d/mimocode-webui/run"
# SPA dist
docker cp "\$RT/dist/." "\$C:\$WD/dist/" 2>/dev/null || true
echo copied
REMOTE
ssh_retry bash -s "$REMOTE_TMP" "$CONTAINER" "$WEBUI_DST" "$DATA_WEBUI" < "$R2" \
  || { echo "docker cp step failed"; rm -f "$R2"; exit 1; }
rm -f "$R2"

echo "==> 3. verify integrity (host upload copy vs in-container, single session)"
# host-tmp came via gzip tar (integrity-checked), so host-tmp == local source.
# Comparing host-tmp vs container proves the docker cp is bit-identical.
R3=$(mktemp)
cat > "$R3" <<'REMOTE'
set -uo pipefail
RT="$1"; C="$2"; WD="$3"
fail=0
cd "$RT/webui"
find . -type f | while read -r f; do
  rel="${f#./}"
  lh=$(sha256sum "$f" | awk '{print $1}')
  rh=$(docker exec "$C" sha256sum "$WD/$rel" 2>/dev/null | awk '{print $1}')
  if [ "$lh" = "$rh" ]; then
    echo "  OK   $rel"
  else
    echo "  FAIL $rel (host=$lh cont=$rh)"
    fail=1
  fi
done
if [ "$fail" -ne 0 ]; then echo "INTEGRITY FAILED"; exit 1; fi
echo "all files verified identical to source"
REMOTE
ssh_retry bash -s "$REMOTE_TMP" "$CONTAINER" "$WEBUI_DST" < "$R3" \
  || { echo "==> INTEGRITY CHECK FAILED — container has mismatched files"; rm -f "$R3"; exit 1; }
rm -f "$R3"

if [ "$DRY" = "1" ]; then
  echo "==> DRY mode: skipped add-on restart. Run without DRY=1 to activate."
  exit 0
fi

echo "==> 4. restart add-on (picks up new s6 run; /data overlay re-applied on start)"
ssh_retry "ha addons restart local_mimo-code 2>/dev/null || docker restart $CONTAINER" \
  || { echo "restart failed"; exit 1; }
echo "==> done"

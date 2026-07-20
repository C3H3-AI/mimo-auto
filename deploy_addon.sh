#!/usr/bin/env bash
# Deploy MiMo Code add-on webui + channel code to the running HA add-on container.
set -euo pipefail

SSH_KEY=~/.ssh/id_ha
HOST=root@api.homediy.top
CONTAINER=addon_local_mimo-code
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
WEBUI_PY_DIR="$LOCAL_ROOT/mimo-code/rootfs/usr/share/mimocode/webui"
RUN_SRC="$LOCAL_ROOT/mimo-code/rootfs/etc/s6-overlay/s6-rc.d/mimocode-webui/run"
REMOTE_TMP=/tmp/mimo_deploy

echo "==> 1. SCP built SPA + python + run script to HA host"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$HOST" "mkdir -p $REMOTE_TMP/dist $REMOTE_TMP/webui"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -r "$LOCAL_ROOT/webui/dist/." "$HOST:$REMOTE_TMP/dist/"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
  "$WEBUI_PY_DIR/server.py" "$WEBUI_PY_DIR/channel_manager.py" "$WEBUI_PY_DIR/feishu_client.py" \
  "$HOST:$REMOTE_TMP/webui/"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$RUN_SRC" "$HOST:$REMOTE_TMP/mimocode-webui-run"

echo "==> 2. docker cp into container"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$HOST" "
  docker exec $CONTAINER sh -c 'mkdir -p /usr/share/mimocode/webui/dist /data/mimocode'
  docker cp $REMOTE_TMP/dist/. $CONTAINER:/usr/share/mimocode/webui/dist/
  docker cp $REMOTE_TMP/webui/server.py $CONTAINER:/usr/share/mimocode/webui/server.py
  docker cp $REMOTE_TMP/webui/channel_manager.py $CONTAINER:/usr/share/mimocode/webui/channel_manager.py
  docker cp $REMOTE_TMP/webui/feishu_client.py $CONTAINER:/usr/share/mimocode/webui/feishu_client.py
  docker cp $REMOTE_TMP/mimocode-webui-run $CONTAINER:/etc/s6-overlay/s6-rc.d/mimocode-webui/run
  echo COPIED
"

echo "==> 3. Restart add-on (picks up new run script + code)"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$HOST" "ha addons restart local_mimo-code 2>/dev/null || docker restart $CONTAINER"
echo "==> done"

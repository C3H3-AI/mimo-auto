#!/bin/bash
# MiMo Auto Addon 部署脚本
# 用法: bash deploy.sh [all|python|s6|restart]

set -e

REMOTE="root@api.homediy.top"
SSH_KEY="~/.ssh/id_ha"
CONTAINER="addon_local_mimo-code"
SRC="D:/ai-hub/integrations/mimo_auto/mimo-code/rootfs/usr/share/mimocode/webui"
S6_SRC="D:/ai-hub/integrations/mimo_auto/mimo-code/rootfs/etc/s6-overlay/s6-rc.d"

deploy_python() {
    echo "=== 部署 Python 文件 ==="
    for f in "$SRC"/*.py; do
        name=$(basename "$f")
        echo "  $name"
        cat "$f" | ssh -4 -i $SSH_KEY -o StrictHostKeyChecking=no $REMOTE \
            "docker exec -i $CONTAINER sh -c 'cat > /usr/share/mimocode/webui/$name'"
    done
    echo "Python 文件部署完成"
}

deploy_s6() {
    echo "=== 部署 s6 服务文件 ==="
    for svc in ha-mcp mimocode-webui; do
        for f in type run finish; do
            src_file="$S6_SRC/$svc/$f"
            if [ -f "$src_file" ]; then
                echo "  $svc/$f"
                cat "$src_file" | ssh -4 -i $SSH_KEY -o StrictHostKeyChecking=no $REMOTE \
                    "docker exec -i $CONTAINER sh -c 'cat > /etc/s6-overlay/s6-rc.d/$svc/$f'"
            fi
        done
        # dependencies
        dep_dir="$S6_SRC/$svc/dependencies.d"
        if [ -d "$dep_dir" ]; then
            for dep in "$dep_dir"/*; do
                if [ -f "$dep" ]; then
                    name=$(basename "$dep")
                    echo "  $svc/dependencies.d/$name"
                    ssh -4 -i $SSH_KEY -o StrictHostKeyChecking=no $REMOTE \
                        "docker exec $CONTAINER touch /etc/s6-overlay/s6-rc.d/$svc/dependencies.d/$name"
                fi
            done
        fi
    done
    echo "s6 文件部署完成"
}

restart() {
    echo "=== 重启 Addon ==="
    ssh -4 -i $SSH_KEY -o StrictHostKeyChecking=no $REMOTE \
        "docker restart $CONTAINER"
    echo "重启完成"
}

case "${1:-all}" in
    python)  deploy_python ;;
    s6)      deploy_s6 ;;
    restart) restart ;;
    all)     deploy_python; deploy_s6; restart ;;
    *)       echo "用法: $0 [all|python|s6|restart]" ;;
esac

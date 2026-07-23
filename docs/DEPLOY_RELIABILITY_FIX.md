# 部署可靠性修复（DEPLOY RELIABILITY FIX）

> 对应交接清单第 1 项「部署不稳定 / 文件有时损坏 / ha_context.py 反复丢失」

## 交接里的根因判断（不准确）

交接写「根因是 PowerShell 的 `Get-Content -Raw -Encoding UTF8 | ssh ... "cat > file"` 在某些情况下损坏文件内容」。
实际检查三套部署脚本后，真正的问题有三点，且都比「编码」更结构性：

1. **管道传输二进制不安全** —— `deploy.sh` 用 `cat "$f" | ssh ... "docker exec -i ... cat > file"` 把文件字节经 SSH 标准输入灌进容器。
   这条管道在 Windows/Git-Bash 下会因 CRLF / 多字节 UTF-8 / PTY 处理把文件**静默损坏**（交接说的症状确实存在，但它是 bash 版反模式，不只是 PowerShell）。
2. **部署清单不全** —— `deploy_addon.sh` 只 `docker cp` 了 **3 个文件**（server.py / channel_manager.py / feishu_client.py），
   而交接说完成了 14+ 个文件。其余模块（ha_context.py、client.py、session_store.py、persona.py …）**从未进过这个脚本**，
   镜像里也没有（镜像是很早前构建的）→ 这就是 `ha_context.py`「反复丢失 / 部署后不存在」的真因。
3. **零完整性校验** —— 三套脚本都没有 sha256 比对，文件传坏了 / 缺了也发现不了。

> 补充：`ha addons restart` 会保留容器可写层，但 `ha addons update`（从新镜像重建容器）会把它清空。
> 所以只往运行中的容器 `docker cp` 也不够稳——代码必须落到持久卷 `/data`。

## 修复方案

新增 `deploy_reliable.sh`，并给 s6 启动脚本加「从 `/data` 覆盖代码」逻辑。一次性解决上面三点：

| 问题 | 修复 |
|------|------|
| 管道损坏 | 改用 **scp → 主机临时目录 → `docker cp`**（不经过 stdin 管道，二进制安全） |
| 清单不全 | **全量 glob** webui 下所有文件 + SPA dist + s6 run，不再硬编码 3 个 |
| 无校验 | 部署后对每个文件做 **sha256 本地 vs 容器内** 比对，不一致直接中止 |
| 重启/更新丢代码 | 代码同时写一份到持久卷 `/data/mimocode/webui`；s6 `run` 在每次启动时覆盖回 `/usr/share/mimocode/webui` |

### 用法

```bash
bash deploy_reliable.sh          # 全量部署 + sha256 校验 + 重启 addon
DRY=1 bash deploy_reliable.sh    # 只上传 + 校验，不重启（验证传输不再损坏时用）
```

### 回滚

```bash
docker exec addon_local_mimo-code rm -rf /data/mimocode/webui   # 清掉持久覆盖层
ha addons restart local_mimo-code                               # 回到镜像内代码
```

## 已做的离线验证

- `bash -n deploy_reliable.sh` ✅ 语法通过
- `bash -n` s6 run ✅ 语法通过
- 用 `client.py` 做损坏对照：原文件 sha256 `bcca00c6…`，翻转 1 字节后 `8d44cf92…` → 校验**能抓出损坏**
- 待部署文件集：webui 24 个文件 + s6 run ×1 + SPA dist ×4

## 待办（真机）

- [ ] 在真机跑 `DRY=1 bash deploy_reliable.sh`，确认 24 个文件全部 `OK`（证明传输不再损坏）
- [ ] 确认无误后跑全量（含 `ha addons restart`）激活
- [ ] 旧脚本 `deploy.sh` / `deploy_addon.sh` / `deploy.py` 标记弃用，避免再被误用

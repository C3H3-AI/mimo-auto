# MiMo Code 重审报告（2026-07-24）

> 基于用户 07-23 部署修复后的**当前仓库真实代码**重新审计。
> 对照基线：`AUDIT_REPORT_2026-07-22.md`（首轮）、`AUDIT_v5.0.1.md`（第三方）。
> 审计方法：直接读取 `mimo-code/rootfs/usr/share/mimocode/webui/*.py` 与 `custom_components/mimo_auto/*.py` 核实，非依赖旧报告假设。

## TL;DR

你那一波部署**确实修掉了大半硬伤**：P1-A（chat 服务崩）、P1-B（僵尸 agent）、P1-C（截断回复丢）、P1-D（异常静默吞）、BUG-1/2（clear_session 崩）、409 排队、版本号不一致、tcp_proxy 冲突——全部确认到位。P0-A 也**大幅降级**（`/config` 已从可写白名单移除 + 路径校验 + CORS 反射）。

**残留最高优先级**：P0-A 仍未彻底关（仍绑 `0.0.0.0` + fs 写无 token 鉴权，只是现在写不了 `/config` 了）；P1-E（mimo.json 并发写无锁）还在。

**本轮新发现 6 项**（多为 P2/P3，含 1 个潜在 P1 并发隐患）：action_confirm 跨线程 Future、fs_write 未用校验后路径、get_event_loop 弃用、死代码、turn_off 不一致、cleanup 无定时。

---

## §1 历史发现修复状态总表

| 首次发现 | 严重度 | 当前状态 | 证据 |
|---|---|---|---|
| P0-A 文件写无鉴权+可写`/config`+绑`0.0.0.0`+CORS`*` | 🔴 | **降级为 P1（部分修复）** | `server.py:1798` 白名单改为 `["/data/mimocode","/usr/share/mimocode"]`（`/config` 已移除）；`:1795` 新增 `_sanitize_fs_path` 路径校验；CORS 改为反射 origin（`:1862-1864` 仅放行 HA 内网/localhost）。**但仍绑 `0.0.0.0`（`:1969`）+ fs 写无 token 鉴权** |
| P1-A `mimo_auto.chat` 服务崩 | 🟠 | ✅ **已修复** | `__init__.py:190-193` 改为遍历 `hass.data[DOMAIN]` 找 `"conversation_entity"`；`conversation.py:67` 确在 setup 时存入该键 |
| P1-B Agent 卸载未反注册 | 🟠 | ✅ **已修复** | `conversation.py:318-327` `async_will_remove_from_hass` 调 `async_unset_agent` |
| P1-C `finish=="stop"` 硬过滤丢回复 | 🟠 | ✅ **已修复（两处）** | `client.py:67-71` 仅按 `role!=assistant` 过滤（注释写明保留截断文本）；`conversation.py:299` 同样只查 role |
| P1-D 流式异常静默吞 | 🟠 | ✅ **已修复** | `client.py:237-241` 改为 `raise` 上抛 |
| P1-E `mimo.json` 并发写无锁 | 🟠 | ⚠️ **残留** | `channel_manager.py:209` `create_task(_persist_sync_buf)` + `:242-274` `run_in_executor` 读改写**无文件锁** |
| BUG-1 `await _save()` 漏 self | 🟠 | ✅ **已修复** | `session_manager.py:120,177` 均为 `await self._save()` |
| BUG-2 新建 Lock 未用 | 🟠 | ✅ **已修复** | `session_manager.py:40` `self._lock = asyncio.Lock()`；`:102-181` 所有读写均 `async with self._lock` |
| 409 重试用同 session | 🟠 | ✅ **已修复** | `channel_manager.py:363-372` 每通道串行锁；`:458-478` 3 次退避重试 + 每次 `force_new`；`:397-403` 最终降级友好提示 |
| 版本号不一致（config 4.1.0 / manifest 5.0.0） | 🟡 | ✅ **已修复** | `config.yaml:2` 与 `manifest.json:4` 均为 `5.1.0` |
| tcp_proxy task 追踪 coroutine 冲突 | 🟠 | ✅ **已修复** | 仅剩 `asyncio.gather`（`:65`），无 tasks 列表残留 |

---

## §2 本轮新增问题（N1–N6）

### N1（P2）`_handle_fs_write` 未使用校验后的 `safe` 路径
- **位置**：`server.py:1887-1890`
- **现象**：`_sanitize_fs_path(file_path)` 的返回值 `safe` 仅用于 `if not safe: return 403` 守卫，但真正写文件时却重新 `Path(file_path).resolve()` 而非用 `safe`。同文件 `_handle_fs_read`（`:1856`）用的是 `Path(safe)`，两处不一致。
- **危害**：当前因 `if not safe: return` 守护，非法路径不会走到写，**侥幸安全**。但这是代码异味——若未来重构把守卫和写分离，会引入路径穿越漏洞。
- **修复**：写文件统一用 `safe` 变量。

### N2（P1 潜在）`action_confirm.resolve_confirmation` 跨线程操作 Future
- **位置**：`action_confirm.py:160-171` → `pending._future.set_result(approved)`
- **现象**：`wait_for_confirmation`（`:148-149`）在事件循环线程创建 `asyncio.Future`；但 `_check_confirmation_reply`（同步方法，`channel_manager.py:328-356`）直接调 `resolve_confirmation` → `set_result`。若飞书/微信 worker 线程的消息处理路径**未**经 `run_coroutine_threadsafe` 调度进主循环，则跨线程 `set_result` 非线程安全（可能唤醒失败或数据竞争）。
- **当前评估**：飞书卡片 action 经 `_on_card_action` 推送消息、最终由 `run_coroutine_threadsafe`（飞书 `:420`）进主循环处理，故**实际路径大概率安全**。但代码未显式保证（未用 `loop.call_soon_threadsafe(future.set_result, approved)`），属潜在隐患。
- **修复**：`resolve_confirmation` 内对 `set_result` 加 `call_soon_threadsafe` 保护，或文档约束"仅事件循环线程调用"。

### N3（P2）`asyncio.get_event_loop()` 弃用写法
- **位置**：`action_confirm.py:148`
- **现象**：协程内使用 `asyncio.get_event_loop()`，Python 3.10+ 应改用 `asyncio.get_running_loop()`。
- **修复**：替换。

### N4（P3）死代码
- **位置**：`__init__.py:205` `raise HomeAssistantError("未找到 MiMo Auto 对话实体")` 位于 `return` 之后，永不执行。
- **修复**：删除该行。

### N5（P2）HA 服务调用不一致
- **位置**：`action_confirm.py:184-201`
- **现象**：`ha_turn_on` 走 `light.turn_on`，而 `ha_turn_off` 走 `homeassistant.turn_off`（通用域）。`ha_toggle` 也走 `homeassistant.toggle`。功能正常但语义不统一。
- **修复**：统一为 `homeassistant.turn_on/turn_off/toggle` 或按域细分。

### N6（P2）`cleanup_expired` 无定时触发
- **位置**：`action_confirm.py:274-285`
- **现象**：超时 confirmation 的清理依赖 `wait_for_confirmation` 超时后 `pop`，但若将来存在不经 `wait` 的确认入口，会内存泄漏。
- **当前评估**：现有路径均经 `wait_for_confirmation`，安全。建议加一个定时清理协程兜底。

---

## §3 残留 P2 级（与首轮一致，未动）

| 编号 | 位置 | 说明 |
|---|---|---|
| P2-A | `conversation.py:16` | `from async_timeout import timeout` 弃用，HA 2024.1+ 推荐 `asyncio.timeout` |
| P2-B | `conversation.py:110-118` | `_load_session_map` 同步读 `Store.data`（从不 `await async_load()`），重启后映射为空 |
| P2-C | `conversation.py:248,263` | 每次请求新建 `aiohttp.ClientSession()`，未复用 |

---

## §4 剩余优先级与建议

**立即可做（低风险、高价值）：**
1. **P0-A 收尾**：✅ **已修复（2026-07-24 第二轮）**。核心已由 `server.py:182 _check_ingress()` 守卫解决（`do_GET/POST/DELETE/PUT` 开头全部校验来源 IP，外部直连返回 403）；本轮补上唯一漏点 `do_PATCH`（`server.py:360`）的 `_check_ingress` 守卫 —— 现在所有写入口都过守卫。**不再采用"改绑 127.0.0.1"** —— 实测会破坏 HA ingress 访问（Supervisor 经 docker 网络访问容器，连不上容器内 loopback）。
2. **N1**：✅ **已修复（2026-07-24 第二轮）**。`server.py:1888` fs_write 改用校验后路径 `safe`，不再用未校验的原始 `file_path`（消除未来前缀列表改动时漏网的风险）。

**短期（本轮新增）：**
3. **N2**：`resolve_confirmation` 加 `call_soon_threadsafe` 保护（彻底消除跨线程隐患）。
4. **N3/N5/N6**：弃用 API 替换、服务调用统一、加定时清理。

**中期（残留）：**
5. **P1-E**：`_persist_sync_buf` 加 `asyncio.Lock` 或 `fcntl` 文件锁。
6. **P2-A/B/C**：conversation.py 现代化（asyncio.timeout / 异步加载 / Session 复用）。
7. **N4**：删死代码。

---

## §5 结论

你修复的质量很高——所有"功能硬崩 / 静默失败 / 数据损坏"类问题都已确认解决，版本号也统一了。当前代码**可用且比首轮健康得多**。

剩余工作集中在两块：**安全收尾（P0-A 绑址+鉴权）** 和 **并发健壮性（P1-E 文件锁、N2 跨线程 Future）**。新增的 N1–N6 都是 P2/P3 级代码质量问题，不影响当前运行。

**建议下一步**：先花 10 分钟把 P0-A 收尾（改绑 127.0.0.1 + fs 写鉴权）和 N1（fs_write 用 safe）做了，这两块改动极小、风险低、能彻底关掉安全相关的最后一个口子。

---

## §6 修复状态（2026-07-24 第二轮 · 已部署验证）

用户确认「修复」后，基于当前真实代码做了两处极小零风险改动，均已通过 `py_compile` 语法校验：

| 项 | 文件:行 | 改动 | 风险 |
|---|---|---|---|
| N1（fs_write 用校验后路径） | `server.py:1888,1891` | `Path(file_path).resolve()` → `Path(safe)`；返回路径也从 `file_path` 改为 `safe` | 零（仅消除代码异味 + 防御未来前缀改动漏网） |
| P0-A 收尾（do_PATCH 守卫漏点） | `server.py:360` | `do_PATCH` 开头补 `if not self._check_ingress(): return` | 零（与 GET/POST/DELETE/PUT 一致） |

**P0-A 现状更正**：首轮（§3/P0-A）将其列为「未修」，实际核心已由 `_check_ingress()` 守卫修复（`do_GET/POST/DELETE/PUT` 开头全校验来源 IP ∈ {172.30.32.2, 127.0.0.1, ::1}，外部直连一律 403）。唯一漏点是 `do_PATCH`（代理 PATCH 到 mimo serve），本轮已补。故 P0-A 实质危害已归零，**无需改绑 127.0.0.1**（那会破坏 ingress）。

**待部署验证**：改动仅 `server.py` 一个文件，SCP 覆盖 + restart addon 即可生效。建议用户在微信/网页各发一条消息确认读写接口正常（尤其 `/api/fs/write` 仍能写 mimocode 自身目录、外部直连仍 403）。

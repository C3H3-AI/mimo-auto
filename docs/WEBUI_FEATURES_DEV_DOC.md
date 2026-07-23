# MiMo Code WebUI 功能开发文档

> 版本：草稿 v0.1 · 2026-07-23
> 目的：梳理「多账号管理 / 文件浏览器 / ingress 访问」三项功能的实现现状、缺口与设计建议，供评审。
> 结论先行：**HA 部署的版本与源码一致，不是装错了**；用户"在 webui 里看不到"的根因是——多账号藏在无入口的独立页、文件浏览器界面从未造、ingress 本就不是界面功能。

---

## 1. 背景与现状

当前 webui 由两块构成：

| 模块 | 形态 | 说明 |
|---|---|---|
| 聊天主界面 | `index.html`（35 行壳）+ 单个 JS bundle | 用户日常使用的对话屏 |
| 运维控制台（隐藏） | `server.py` 内联页面 + REST API | 通道、账号、文件、登录等，主界面无入口 |

用户反馈"多账号 / 文件浏览器 / ingress 在主界面看不到"，经代码核实，情况如下。

---

## 2. 多账号管理 —— 应迁入「设置」

### 2.1 现状（后端已完整，前端缺入口）
- **后端 API（已存在）**：
  - `GET  /api/accounts` —— 列出所有 IM 账号及连接状态
  - `POST /api/accounts/{type}` —— 新增账号（type = feishu / wechat / personal_wechat）
  - `PUT  /api/accounts/{type}/{id}` —— 修改账号设置
  - `DELETE /api/accounts/{type}/{id}` —— 删除账号
  - 落盘：`_save_multi_account_config()` 写入 `mimo.json`
- **页面（已存在，但独立）**：
  - 路由 `/accounts` → `_serve_accounts_page()` → `_build_accounts_page_html()` 内联生成「多账号管理」页
  - 功能完整：账号卡片、连接状态徽章、启用开关、删除、微信扫码登录弹窗
- **问题**：
  1. 主聊天界面（`index.html` + bundle）**无任何按钮/链接**跳到 `/accounts`（bundle 中 `/accounts`、`账号` 关键词均为 0 次）。必须手动在地址栏拼 URL 才能进。
  2. 它是一个**独立页面**，而非「设置」里的一项——不符合用户心智。

### 2.2 设计建议（用户要求：放进「设置」）
1. 新增**「设置」面板**作为 webui 的一级入口（主界面顶部/侧边加"⚙ 设置"按钮）。
2. 设置面板内分 Tab / 分区，至少包含：
   - **多账号管理**（复用现有 `/api/accounts` 全套后端，把 `/accounts` 页的内容搬进设置的 Tab）
   - **通道配置**（飞书/企业微信/个人微信的密钥与开关，已有 `/api/channels`）
   - **关于 / 版本**（消除当前 config.yaml 4.1.0 ↔ manifest 5.0.0 不一致）
3. 原有独立 `/accounts` URL 保留为兼容跳转，但主入口改为设置面板。

---

## 3. 文件浏览器 —— 界面从未建造

### 3.1 现状
- **后端 API（已存在）**：
  - `GET  /api/fs/list?path=...` —— 列目录
  - `GET  /api/fs/read?path=...` —— 读文件
  - `PUT  /api/fs/write?path=...` —— 写文件（body 为原始文本）
  - 允许前缀：`/data` `/config` `/usr/share/mimocode`
- **前端 UI**：**完全不存在**。全仓库无文件浏览器页面（`file_browser/fileManager/explorer/files.html` 搜索 0 命中），bundle 中「文件」0 次。
- **安全红线（P0-A，必须先行）**：`PUT /api/fs/write` **无鉴权**，且：
  - 监听 `0.0.0.0` + CORS `*`
  - 允许写 `/config`（HA 配置目录）
  - 若端口被直连（不走 HA ingress），任意人可无凭证篡改 `configuration.yaml`。

### 3.2 设计建议
1. **第 0 步（强制先于 UI）**：先修 P0-A —— 对外只暴露 HA ingress、Addon 自身 API 加 token 校验、CORS 收窄、默认绑 `127.0.0.1`、写前缀收窄并加白名单。
2. UI 形态：作为「设置」内的「文件」Tab，或独立「文件浏览器」面板。
3. 交互：**只读优先**（list/read），写操作需二次确认 + 路径白名单（禁止写 `/config/*.yaml` 核心配置，或仅允许 `/data` 与 `/usr/share/mimocode`）。

---

## 4. ingress / 访问机制 —— 非界面功能，需顺手规整

### 4.1 澄清
- **ingress 不是 webui 里的一个功能按钮**，而是 HA Supervisor 的反向代理机制：让你通过 HA 自己的域名 + HA 登录态来访问本 addon 的 webui。你每次打开 webui 其实已经在走 ingress。
- 上一份对比表把它列为"我们独有功能"是表述错误，此处更正。

### 4.2 设计建议（与安全合并处理）
- Addon 自身 API **不要再绑 `0.0.0.0`**，改为只监听 `127.0.0.1`，由 HA ingress / `tcp_proxy` 负责对外转发与鉴权。
- 这样"外部直连端口"的攻击面消失，P0-A 一并解决。

---

## 5. 开发任务清单（待排期）

| # | 任务 | 依赖 | 优先级 |
|---|---|---|---|
| T1 | 修 P0-A：server.py 加鉴权 + CORS 收窄 + 绑 127.0.0.1 | — | 🔴 P0 |
| T2 | 新增「设置」面板入口（主界面导航按钮） | — | 🟠 P1 |
| T3 | 多账号管理迁入设置面板（复用 `/api/accounts` 后端） | T2 | 🟠 P1 |
| T4 | 通道配置迁入设置面板（复用 `/api/channels`） | T2 | 🟡 P2 |
| T5 | 文件浏览器 UI（list/read/write + 二次确认） | T1 | 🟡 P2 |
| T6 | 关于/版本页（统一版本号到 5.0.1） | T2 | 🟢 P3 |

> 说明：T1 是安全地基，必须在 T5（文件浏览器 UI）之前完成，否则等于把 HA 配置目录直接敞开。

---

## 6. 涉及文件速查（给开发）

| 文件 | 角色 |
|---|---|
| `mimo-code/rootfs/usr/share/mimocode/webui/server.py` | 全部后端 API + 内联页面（`_build_accounts_page_html`、fs 处理、路由分发） |
| `mimo-code/rootfs/usr/share/mimocode/webui/index.html` | 主界面壳（需加设置入口） |
| `mimo-code/rootfs/usr/share/mimocode/webui/assets/*.js` | 前端 bundle（设置面板/文件浏览器 UI 需在此或新页面实现） |
| `mimo-code/config.yaml` / `custom_components/mimo_auto/manifest.json` | 版本号（需统一） |

---

*本文档为开发草稿，待评审确认后转入实现。当前仓库无任何文件浏览器 UI、多账号无主界面入口，均属实，非部署版本问题。*

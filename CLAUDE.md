# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

sub2api-helper 是为 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)（LLM API 网关）提供的 FastAPI 边车服务：账号定时调度（跨午夜时间窗切换 `schedulable`）、用户邮件维护、自动化规则（闲置提醒、订阅倍率、月度余额奖励）、同步到 sub2api 自定义菜单的功能页（智商检测画廊、模型用量与费用统计、用户用量排名），以及可选的原生列表行按钮试点。Helper 的页面、资源与 API 全部挂载在 `/_helper/`。版本号（helper/VERSION）与上游正式 Release 保持一致，不独立递增。

## 常用命令

```sh
# 测试环境（Python 3.12）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 全部单元测试（CI 同款，从仓库根目录运行）
.venv/bin/python -m unittest discover -s tests -v

# 单个测试
.venv/bin/python -m unittest discover -s tests -p 'test_engine.py' -k test_overnight_boundaries_and_opening_weekday

# 本地起服务（单 worker；关闭 access log 避免记录自定义菜单 URL 中的 JWT）
HELPER_DB_PATH="$(mktemp -d)/helper.db" .venv/bin/uvicorn helper.app:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log --no-proxy-headers

# 共享样式：改动 ui/ 或静态页面类名后重新生成并提交 helper/static/style.css（CI 用 git diff 校验已提交）
npm --prefix ui ci && npm --prefix ui run build

# 静态 JS 语法检查（项目无通用 linter，CI 仅对静态脚本跑 node --check，工作流用 actionlint）
for script in helper/static/*.js; do node --check "$script" || exit 1; done

# GitHub Pages 演示站（复用生产前端 + site/demo-adapter.js 纯内存示例数据，无后端）
node site/build.mjs
python3 -m http.server 8090 --directory _site

# 版本一致性检查（VERSION / compose.yaml / README.md / ui/package*.json）
python scripts/version.py --check

# 容器与入口隔离冒烟（需先 docker build；ingress 冒烟使用真实 Nginx 与假上游）
docker build -t sub2api-helper:test .
python3 tests/container_smoke.py sub2api-helper:test
python3 tests/ingress_smoke.py sub2api-helper:test
```

浏览器回归（`tests/native_ui`、`benchmark_ui`、`gallery_ui`、`gallery_admin`、`console_ui`，各自 `node tests/<套件>/test.cjs`）需要 Chromium 与 `playwright-core`，可配置 `HELPER_CHROMIUM_PATH`、`HELPER_PLAYWRIGHT_PATH`、`HELPER_PYTHON_PATH`，步骤见 `tests/native_ui/README.md`。单元测试全部离线：`tests/mock_upstream.py` 提供假上游，无真实 Key、无付费模型调用。

## 架构

### 应用组装与调度引擎（`helper/app.py`、`helper/engine.py`、`helper/automation.py`）

单进程、单 worker（`uvicorn` `workers=1`，`python -m helper`）：SQLite（WAL、`synchronous=FULL`）与后台任务都不支持多实例。`lifespan` 启动三个后台循环：`engine.run()`（约每 30 秒在 `_tick_lock` 下核对调度与规则，API 写操作与引擎 tick 共享该锁串行化）、`compatibility_loop()`（每 6 小时对照 `docs/upstream-baseline.json` 探测上游接口并存入 `settings`）、`gallery.run()`（画廊定时生成，tick 默认 15 秒）。核心协作对象：`Store`（`objects`/`settings`/`audit`/`ledger` 四张 SQLite 表，`helper/store.py`）、`CredentialVault`（管理 Key 以 Fernet 加密落盘，主密钥在数据库旁 `<db_path>.key`，权限 `0600`，丢失不可恢复，`helper/credentials.py`）、`Upstream`（上游 API 唯一适配器）、`Engine` + `AutomationEngine`、`Gallery`。

调度与三类自动化规则：
- **账号时段（`_schedules`）**：仅按 `in_window()` 比对并切换上游账号 `schedulable`（一致则跳过写入，不碰账号 `status` 或凭据；跨午夜窗口归属开始日，如周五 23:00–周六 09:00 属周五）；冲突计划 fail-closed，显式保存“全天运行”会开启调度并清理该账号由行按钮创建的计划。
- **闲置提醒（`_rules`）**：通过 `store.ledger` 追踪状态，仅处理 `status=active`、`role=user` 且 `last_used_at` 明确的用户；SMTP 结果不确定（如 `DATA` 之后连接中断）记为待人工核对，绝不自动重发或禁用；发信前与可选禁用前均重新回读上游用户状态。
- **订阅倍率与月度奖励（`AutomationEngine`）**：同样依赖 `store.ledger` 保证幂等与所有权。`subscription_multiplier` 对临期且剩余额度高于阈值的订阅应用个人或分组默认倍率，条件失效后仅当当前值仍由本规则写入时才恢复基线；`monthly_rewards` 按规则时区结算上个完整自然月，逐用户汇总 `/api/v1/admin/usage/stats` 的 `actual_cost` 排名前三，首次调用 `add_balance`（带 `Idempotency-Key`）前在 `ledger` 冻结名单与金额快照，结果不确定时保留待核对记录，绝不重放入账。

三种鉴权路径，session 均为内存态：
- 管理端 `/_helper/api/admin/*`：`HELPER_ADMIN_TOKEN` 或绑定后的管理 Key 换取会话 token。
- 原生行按钮 `/_helper/api/*`（native_api.py）：浏览器中已登录的 sub2api 管理员 JWT；要求上游 `session_binding_enabled` 明确为 `false`，且逐次向上游重新验证身份。
- 用户门户 `/_helper/api/portal/*`（自定义菜单内嵌页）：sub2api 用户 JWT 换会话。普通用户看到脱敏榜单（"用户 #N"、仅本人有 `is_me` 标记），管理员看到真实名称；榜单窗口为规则时区的本自然月，仅列出可评奖的正常普通用户，按结算排序给出 `rank` 与预计奖励 `reward`；排名页（`feature.js`）复刻上游 `UserTokenRanking.vue` 的用量表（请求数、输入/输出/缓存/总 Token、费用）并在末列追加「预计奖励」，页内排序与 Top N 只改变显示顺序，名次和奖励始终取自接口。响应另附只读的奖项摘要 `rewards`（金额、统计截止时间、模式，不含规则内部字段）；页面文案是“统计至月末 23:59:59，月结后自动发放”，截止时间不是到账时刻。价格页（`/portal/models`）按 `group_id` 对比实际与官方单价，官方价由 `helper/pricing_catalog.py` 从公开价格库拉取并校验 SHA-256，实际价为官方价 × (`actual_cost`/`cost`) 的推算值——脱敏面向的是已登录普通用户，不是匿名开放访问。

菜单同步（app.py `sync_menu_modules`）只管理固定前缀 `sub2api-helper-<key>` 的 custom_menu_items 项，保留其他菜单，写后回读确认。

### 入口隔离与原生 UI 试点

默认部署不代理原站：nginx（deploy/nginx/sub2api-helper.conf）只把 `/_helper/` 交给 Helper，sub2api 的页面、管理 API、模型网关、SSE、WebSocket 全部直达原服务，Helper 停机不影响原站。原生行按钮需要两个开关同时开启：Helper 端 `HELPER_NATIVE_UI=true` 与 nginx 端 `set $helper_injection_enabled 1;`。

native_proxy.py 只代理“SPA 文档导航 GET”：`is_document_request` 要求 GET、Accept 含 text/html 或 xhtml、Sec-Fetch-Dest 为 document/iframe（或缺失）、且路径不属于 API/网关/静态资源根（NON_DOCUMENT_ROOTS）。非文档流量根本不会到达 Helper——nginx 直接送 sub2api；已进入文档路径的请求若返回内容不是可识别的 SPA shell（含 `id="app"` 挂载点与 `/assets/` 模块脚本），原样返回、不做注入。确认是 shell 后才复用上游 CSP nonce 注入 `/_helper/native.css` 与 `/_helper/native.js`；只有试点文档路径上的 404/502/503/504 由 nginx `error_page` 回退原站。nginx 的路由排除 map 与 native_proxy 的排除清单需同步维护（对照上游 gateway.go，见 docs/upstream-compatibility.md）。`HELPER_LEGACY_ROUTES=true` 仅用于旧独立域名迁移，临时暴露旧 `/api/*` 与 `/features/*` 别名。

### 上游契约与安全边界（`helper/upstream.py`、`helper/schemas.py`）

Upstream 集中适配 sub2api 的管理与用户 API（含分页读取与菜单读写），便于对照 `docs/upstream-baseline.json` 审阅；模型调用由 `benchmark.py` 与 `gallery_generation.py` 直接发起，SPA 文档代理由 `native_proxy.py` 处理。管理请求用 `x-api-key`，用户请求用 `Authorization: Bearer <JWT>`。管理 Key 绝不附加到用户或模型网关请求；模型调用（benchmark、画廊）一律使用所选分组已有的普通 API Key，由服务端解析，浏览器只接收 Key 名称与 ID。输入模型（`schemas.py` `Input`）统一设置 `extra="forbid"`；对外错误与 Pydantic 校验响应不得回显原始输入、上游 URL、响应体或凭据（`app.py` 定制了 `validation_error` 处理）。

### 画廊与智商检测

- `benchmark.py`：固定 5 题文字题的确定性冒烟（“固定题自检”），走 `/v1/chat/completions` 或 `/v1/responses`。
- `gallery.py`：分组配置（模型 + 该分组的管理员模型 Key + 提示词 + 间隔，默认 30 分钟）持久化在 SQLite 中。重启把进行中任务标记为中断、过期时段直接跳过不补跑（避免付费重试）。“同题对比”（`gallery_comparison`）让多个分组收到完全相同的提示词、各自用本组模型与 Key 生成同一轮（round）作品；全局同时只允许一个生成任务。
- `gallery_generation.py`：模型输出经白名单净化（允许的 HTML/SVG 标签、CSS 属性与函数、本地引用），产物只有 html/text/image 三种；前端在空 `sandbox` iframe + 严格 CSP（`ARTWORK_CSP`）中渲染，绝不插入应用 DOM。
- 数据都在 `/data`（`helper.db` 与 `.key`），容器以 UID 10001 运行。

### 前端资源与 Pages 演示构建（`ui/build.mjs`、`site/build.mjs`）

- `helper/static/` 下除 `style.css` 外均为手写源码（直接编辑并提交）。`helper/static/style.css` 是唯一生成文件：由 `ui/build.mjs` 拼接 `ui/upstream.css`（vendored 自上游 `frontend/src/style.css`）与 `ui/adapters.css`，经 Tailwind CLI 编译产出。修改 `ui/` 或静态页面中的 Tailwind 类名后必须运行 `npm --prefix ui run build` 并提交更新后的 `helper/static/style.css`。
- `site/build.mjs` 从生产 `helper/static/*` 构建静态演示站 `_site/`，注入 `site/demo-adapter.js`（拦截 `window.fetch` 返回纯内存示例数据，配合 CSP `connect-src 'none'`）。构建脚本对 `helper/static/index.html`、`app.js`、`native.js`、`gallery-admin.js` 的关键锚点字符串有硬性断言，重构这些前端文件时需同步验证 `node site/build.mjs`。

### 发布与跟版（`scripts/sync_upstream_release.py`、`scripts/version.py`）

`.github/workflows/upstream.yaml` 每小时运行 `watch_upstream.py` 检查上游正式 Release，按序为最早未同步版本准备同步分支并由 `open_sync_pr.py` 维护唯一同步 PR。`scripts/sync_upstream_release.py`（`prepare` / `check` / `approve`，完整流程见 `docs/releases.md`）负责校验上游源码指纹（`docs/upstream-baseline.json`）、发布清单（`docs/upstream-release.json`）、审阅批准记录（`docs/upstream-release-approval.json`）以及 vendored 样式文件哈希（`ui/upstream.css`、`ui/upstream.tailwind.config.js`）。PR 合并并通过 CI 后由 `image.yaml` 调用 `publish_release.py` 发布同版本双架构镜像与 GitHub Release（无 `latest` 浮动 tag）。`scripts/version.py` 统一校验或设置 `helper/VERSION`、`compose.yaml`、`README.md` 与 `ui/package*.json` 中的版本号。

# sub2api Helper

为 sub2api 提供账号定时调度、用户邮件维护、自动化规则和自定义菜单。Helper 的页面、资源和 API 统一挂载在 `/_helper/`。默认保留原站的流量路径，原生列表行按钮可在独立试点开启。项目适配 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)。

[交互演示](https://aaronyang0628.github.io/sub2api-helper/) · [Docker 部署](docs/deployment.md) · [镜像与版本](https://github.com/AaronYang0628/sub2api-helper/releases) · [兼容性](docs/upstream-compatibility.md)

![账号列表中的定时按钮和 Pages 示例数据](site/preview.png)

Pages 用不同颜色标出新增的定时和邮件按钮，方便在示例列表中找到它们。演示操作使用内存中的示例数据，刷新即可重置。[演示说明](docs/pages.md)

## 功能

| 入口 | 可执行的操作 |
| --- | --- |
| Helper → 定时调度 | 设置参与调度的时段和星期，支持北京时间 `23:00 → 次日 09:00`。原生行按钮也可在试点开启。 |
| Helper → 用户维护 | 给当前用户发送自定义邮件。需要配置 Helper 的 SMTP。原生行按钮另支持提醒模板。 |
| 高级功能 → 闲置提醒 | 按实际模型使用时间筛选长期闲置的普通用户，发信提醒，并可在邮件被 SMTP 接受后禁用用户。 |
| 高级功能 → 自动化规则 | 按到期时间和剩余订阅额度调整倍率，或按上个完整自然月的实际扣费排名为前三名发放余额奖励。 |
| 高级功能 → 功能菜单 | 将智商检测、模型用量与费用统计、用户用量排名同步到 sub2api 自定义菜单。普通用户看到匿名榜单，管理员可查看完整数据。 |

## 智商检测作品画廊

在「统计与检测 → 已配置分组」查看模型、API Key 名称、思考强度与任务状态。点击「新增分组」，从 sub2api 的分组目录选一组，再保存模型与该组已有的管理员模型 Key。没有匹配 Key 的分组会显示原因。在「同题对比」选多个已配置分组、保存一份提示词，再点击「生成一轮」。每组使用自己的模型与 Key，收到完全相同的提示词。[作品画廊示例](https://aaronyang0628.github.io/sub2api-helper/gallery.html)

从「功能与菜单」上线「智商检测」后，用户在一个总览里看到有权访问的参测结果。每个参测项的最新结果排在前面，近期作品随后展示。点开作品可查看生成时的模型、思考强度、提示词与耗时。思考强度的实际支持范围由网关和模型决定，默认沿用模型配置。定时生成默认关闭，启用后按所选间隔执行，默认每 30 分钟。参与同题对比的分组由共享任务调度。手动和定时生成均消耗各自分组 Key 的额度，作品记录保存在 `/data`。

HTML 和 SVG 在禁止脚本及外部资源请求的预览框中展示，CSS 和 SVG 自身的动画仍可播放。管理端可以展开「固定题自检」运行文字题集。

## Docker 部署

下列命令从当前源码构建路由隔离版，版本号以 [helper/VERSION](helper/VERSION) 为准。创建 `.env` 后，把 `SUB2API_URL` 指向 Helper 容器能访问的原服务地址，并把 `SUB2API_PUBLIC_URL` 与 `HELPER_PUBLIC_URL` 设置为原有公网 HTTPS 地址。

```sh
cp .env.example .env
# 设置上述地址后再运行这一步
docker compose up -d --build
```

1. 保留原站的网页、管理 API、模型网关和流式转发。只把 `/_helper/` 转给 Helper。Compose 默认监听宿主机回环地址的 `18080`。
2. 打开原公网域名下的 `/_helper/setup`，绑定管理员 API Key。到「功能与菜单」确认浏览器访问地址，再同步菜单。已有部署也可用此操作修正旧地址和补齐图标。
3. 需要开启页内增强（账号列表「定时」、用户列表「邮件」按钮）时，设置 `HELPER_NATIVE_UI=true` 并启用部署指南中的网页入口分流。该配置只选择 SPA 文档请求，Helper 不可用时由入口回到 sub2api。

默认接入与试点回切、SMTP、备份和升级步骤见[部署文档](docs/deployment.md)。已发布的 `v0.2.23` Release 镜像对应当时的发布基线；若包含当前工作树中的未发布改动，请直接从当前源码 checkout 构建镜像，或在对应提交的 CI 制品中下载通过测试的 `linux/amd64` 镜像归档。[镜像构建记录](https://github.com/AaronYang0628/sub2api-helper/actions/workflows/ci.yaml)

## 页内增强及回切

Helper 在浏览器里给原站账号和用户表格的操作列追加「定时」「邮件」按钮，沿用原表格的数据和现有操作。按钮仅以可确认的行 ID 为准（桌面端 `tr[data-row-id]`、移动端可见 ID 单元格），随切页、筛选和表格重绘自动更新。页内增强保留独立开关（Helper 的 `HELPER_NATIVE_UI` 与 Nginx 的 `$helper_injection_enabled`）。

- **请求路径与故障回退**：开启增强后，仅首次打开页面的 HTML 文档请求会多经过一次 Helper；原站登录 API、管理 API、模型请求和 SSE/WebSocket 流式连接仍直达 sub2api。Helper 不可用时，入口会自动回到原站 HTML，首次加载页面可能多等一个连接或读取回退超时。
- **上游表格结构变化**：若上游版本调整了表格 DOM 结构或隐藏/修改了行 ID 标记，新增按钮会安全降级为暂时不显示（不猜测目标行），需要重新适配和验证。
- **关闭回切与后台状态**：关闭增强开关并刷新页面即可移除按钮。后台定时和自动化规则会继续运行，已发送的邮件、已执行的禁用、倍率调整和余额充值也会保留。

## 自动化规则

- **账号时段**。跨午夜窗口按开始日计算，例如周五 23:00 到周六 09:00 属于周五。Helper 约每 30 秒核对参与调度状态，重启后按当前时间恢复。显式保存「全天运行」会开启参与调度，并移除该账号由行按钮创建的计划。
- **闲置提醒**。例如超过 30 天没有模型用量时发信。规则只处理最后使用时间明确的启用普通用户。同一规则和使用周期的发送记录保存在数据卷。邮件结果不确定时标记人工核对，禁用操作需要确认 SMTP 已接受邮件并重新检查实际使用情况。
- **订阅倍率**。例如订阅剩余不足 2 天且月额度剩余超过 50% 时，为用户在指定分组设置 0.5 倍率。可选择日、周或月的额度。个人倍率为绝对值，也可显式选择修改整个分组的默认倍率。规则默认在条件失效后恢复原值，并核对当前值是否仍由该规则写入。
- **月度奖励**。按规则时区结算上个完整自然月，为前三名分别配置余额奖励。支持自动结算或管理员确认发放。首次发放前冻结名单与金额，到账结果不确定时保留待核对记录。数据卷保存结算和发放记录，用于防止重复入账。

新增自动化规则默认停用，启用前可预览命中对象和动作，执行后在「执行记录」查看结果。接口细节和边界见[上游兼容性](docs/upstream-compatibility.md)。

## 版本与发布

每小时检查 sub2api 的正式 Release。每个新版本都创建同步 PR，并核对接口与界面变化。PR 合并并通过 CI 后发布同版本的镜像和 GitHub Release。版本号以 [helper/VERSION](helper/VERSION) 为准，审阅基线见 [upstream-baseline.json](docs/upstream-baseline.json)，发布流程见[自动跟版与发布](docs/releases.md)。

## 本地开发与验证

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node site/build.mjs
python3 -m http.server 8090 --directory _site
```

修改共享样式后使用 `npm --prefix ui ci && npm --prefix ui run build` 重新生成 CSS。原生行按钮的浏览器回归见 [测试说明](tests/native_ui/README.md)。构建镜像后可运行 `python3 tests/container_smoke.py sub2api-helper:0.2.24` 验证容器流程。上游样式与图标的许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

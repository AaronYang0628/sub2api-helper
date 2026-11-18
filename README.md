# sub2api Helper

为 sub2api 的账号管理和用户管理列表增加定时与邮件操作。管理员在原页面点击行按钮即可操作，按钮沿用 sub2api 的灰白配色和明暗主题。高级功能集中在行弹窗里，可配置闲置提醒、订阅倍率、月度奖励和自定义菜单。项目适配 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)。

[交互演示](https://aaronyang0628.github.io/sub2api-helper/) · [Docker 部署](docs/deployment.md) · [镜像与版本](https://github.com/AaronYang0628/sub2api-helper/releases) · [兼容性](docs/upstream-compatibility.md)

![账号列表中的定时按钮和 Pages 示例数据](site/preview.png)

Pages 用不同颜色标出新增的定时和邮件按钮，方便在示例列表中找到它们。演示操作使用内存中的示例数据，刷新即可重置。[演示说明](docs/pages.md)

## 功能

| 入口 | 可执行的操作 |
| --- | --- |
| 账号管理 → 定时 | 设置参与调度的时段和星期，支持北京时间 `23:00 → 次日 09:00`。Helper 默认只接管显式设置了时段的账号。 |
| 用户管理 → 邮件 | 给当前用户发送自定义邮件或选用提醒模板。需要配置 Helper 的 SMTP。 |
| 高级功能 → 闲置提醒 | 按实际模型使用时间筛选长期闲置的普通用户，发信提醒，并可在邮件被 SMTP 接受后禁用用户。 |
| 高级功能 → 自动化规则 | 按到期时间和剩余订阅额度调整倍率，或按上个完整自然月的实际扣费排名为前三名发放余额奖励。 |
| 高级功能 → 功能菜单 | 将模型能力自检、模型用量与费用统计、用户用量排名同步到 sub2api 自定义菜单。普通用户看到匿名榜单，管理员可查看完整数据。 |

模型能力自检需要绑定目标分组的普通模型 API Key。用户使用本人已有的 Key，管理端使用管理员 API Key 所属账号已有的模型 Key。测试调用消耗所选 Key 的额度。

## Docker 部署

镜像以 sub2api 官方正式版号发布，支持 `linux/amd64` 和 `linux/arm64`。

```sh
docker pull ghcr.io/aaronyang0628/sub2api-helper:0.2.22
```

1. 将 Helper 接入能访问原 sub2api 的网络，将 `SUB2API_URL` 指向 sub2api 内网服务，将 `SUB2API_PUBLIC_URL` 设置为原公网 HTTPS 地址。
2. 挂载 `/data` 后启动 Helper，将原公网域名的网页入口代理到 Helper。首次在 `/_helper/setup` 绑定管理员 API Key，也可通过环境变量预配置。
3. 登录原域名的 sub2api 管理员账号。在账号行点「定时」，或在用户行点「邮件」。需要自动化规则时，从弹窗展开「高级功能」。

Docker 启动命令、Nginx 配置、SMTP、备份和升级步骤见[部署文档](docs/deployment.md)。原生行操作需要上游明确关闭 IP/UA 会话绑定。若部署需要保留会话绑定，可使用 `HELPER_NATIVE_UI=false` 的独立内嵌管理模式。

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

修改共享样式后使用 `npm --prefix ui ci && npm --prefix ui run build` 重新生成 CSS。原生行按钮的浏览器回归见 [测试说明](tests/native_ui/README.md)。构建镜像后可运行 `python3 tests/container_smoke.py sub2api-helper:0.2.22` 验证容器流程。上游样式与图标的许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

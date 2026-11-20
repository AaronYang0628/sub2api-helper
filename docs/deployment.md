# Docker 部署与可回退接入

默认部署不代理原站。原 sub2api 的页面、登录、管理 API、静态文件、模型请求、SSE 和 WebSocket 继续直达原服务；只有 `/_helper/` 交给 Helper。Helper 停止时，原站继续工作。定时、邮件、倍率规则和画廊先通过 `/_helper/setup`、`/_helper/console` 管理，原生行按钮属于可选 Beta。

```text
浏览器 → 原有 HTTPS / Nginx ┬─ /_helper/* → Helper:8080 → 原管理 API
                          └─ 其他路径 → 原 sub2api
                                           Helper /data 持久化规则与凭据
```

以下说明适用于当前 `v0.2.23` 基线源码与镜像。历史 `v0.2.22` 早期镜像是此前的完整代理实现；已发布的 `v0.2.23` Release 镜像仅包含该标签发布时的内容，若需验证或部署当前工作树中的未发布改动，请从当前审阅 checkout 本地构建镜像，并记录源码提交与镜像 ID。

## 1. 构建与配置

准备 Docker、现有 sub2api 和原有 HTTPS 代理。首次取得源码：

```sh
git clone https://github.com/AaronYang0628/sub2api-helper.git
cd sub2api-helper
git rev-parse HEAD
HELPER_VERSION=$(cat helper/VERSION)
HELPER_IMAGE="sub2api-helper:${HELPER_VERSION}"
docker build -t "$HELPER_IMAGE" .
docker image inspect "$HELPER_IMAGE" --format '{{.Id}}'
```

已有审阅 checkout 时直接在该目录构建，先核对其提交。也可在对应 `main` 提交的 CI 制品下载 `linux/amd64` 归档。解包后用 `sha256sum -c` 核对随附校验文件，再执行 `docker load -i` 加载 `.tar.gz`，镜像标签中的版本与归档文件名一致。[CI 构建记录](https://github.com/AaronYang0628/sub2api-helper/actions/workflows/ci.yaml)

将 Helper 加入原 sub2api 的用户自定义 Docker 网络：

```sh
docker inspect sub2api \
  --format '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}'
SUB2API_DOCKER_NETWORK="替换为上一步的网络名"
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，以下均为示例：

```dotenv
SUB2API_URL=http://sub2api:8080
SUB2API_PUBLIC_URL=https://sub2api.example.com
HELPER_PUBLIC_URL=https://sub2api.example.com
SUB2API_ADMIN_KEY=
HELPER_NATIVE_UI=false
HELPER_LEGACY_ROUTES=false
HELPER_DB_PATH=/data/helper.db
TZ=Asia/Shanghai
```

`SUB2API_URL` 是直达原后端的内网根地址，不带 `/api/v1`，不能指向经 Helper 的入口。容器中的 `localhost` 指容器自己。公网配置填写浏览器实际协议、主机和端口；保留原域名有助于维持登录来源。

管理 Key 可通过受保护环境变量提供，也可留空后在 `/_helper/setup` 绑定。推荐预设可信的 `SUB2API_URL`；服务地址也为空时，首次绑定前仅在受控网络开放 Helper。管理 Key 不加入统一 Nginx 请求头、网页链接或仓库。`HELPER_ADMIN_TOKEN` 仅用于可选自动化管理凭据，正常绑定不需要另设密码。

## 2. 启动 Helper

宿主机使用 `18080`，避免占用原站的 `8080`：

```sh
docker volume create sub2api-helper-data
docker run -d \
  --name sub2api-helper \
  --restart unless-stopped \
  --network "$SUB2API_DOCKER_NETWORK" \
  --env-file .env \
  --publish 127.0.0.1:18080:8080 \
  --volume sub2api-helper-data:/data \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  "$HELPER_IMAGE"

curl --fail http://127.0.0.1:18080/_helper/healthz
docker logs --tail 100 sub2api-helper
```

健康检查仅确认进程与版本，不证明上游写权限、SMTP 或规则正确。SQLite 与后台任务仅支持一个实例、一个 worker。

## 3. 默认入口：只分流 `/_helper/`

完整样例见 [deploy/nginx/sub2api-helper.conf](../deploy/nginx/sub2api-helper.conf)。默认 `set $helper_injection_enabled 0;`，原站请求不经过 Helper；只新增 `/_helper/` 路由。将两个内网 origin、域名及监听与现有站点合并，保留 TLS、证书和原站访问控制。样例的 `map` 位于 `http` 层，不能放进 `server`。

样例使用宿主机回环地址，Nginx 可在 Helper 容器不存在时启动或重载。Nginx 在容器中时，`127.0.0.1` 指向自己；改用可达内网地址。使用容器 DNS 时，应配置实际 resolver 和变量 `proxy_pass`，避免加载配置时依赖 Helper DNS。不要把原站上游地址改为 Helper。

```sh
sudo nginx -t
sudo systemctl reload nginx
```

以上由部署者确认实际配置后执行；仓库测试不会修改现有 Nginx。

访问 `https://sub2api.example.com/_helper/setup` 绑定 Key，再打开 `/_helper/console`。先对测试账号保存计划、向受控邮箱发测试邮件，再启用规则。管理 Key 权限决定可用操作，当前适配推荐 FullAccess 或原有全权限 Key；上游运营确认在原站完成。

在「功能与菜单」确认或填写浏览器访问原 sub2api 的地址，例如 `https://sub2api.example.com`。先保存地址，再同步已启用菜单，旧地址与图标会一并更新。宿主机的 `127.0.0.1:18080` 用于 Nginx 转发，浏览器应通过原站的 `/_helper/` 路由访问。同步后刷新 sub2api，分别用普通用户和管理员打开菜单，确认登录与分组权限。

已保存的菜单地址优先于 `HELPER_PUBLIC_URL`。两者都为空时，使用配置的 `SUB2API_PUBLIC_URL` 或可公开访问的 `SUB2API_URL`。启用 IP/UA 会话绑定时，菜单的登录验证暂不可用。

## 4. 可选 Beta：网页注入

原生邮件与定时按钮需要两个开关：Helper 的 `HELPER_NATIVE_UI=true`，以及 Nginx 的 `set $helper_injection_enabled 1;`。更改环境变量后重建 Helper 并保留数据卷。默认两者均关闭，原站不依赖 Helper。

sub2api 使用 History SPA。从 `/`、`/login` 或其他页面首次加载后，切到 `/admin/accounts` 通常不会再请求 HTML。Beta 因此覆盖所有 SPA 首次文档入口，不能只代理两个管理路径。Helper 仅对含 `id="app"` 容器及 `/assets/` 模块脚本标记的原站 shell 注入，任意 HTML、错误页或文件不作为 SPA 增强。

样例仅分流 GET、Accept 含 `text/html` 或 `application/xhtml+xml`、且不属于 API/网关/静态文件的请求。`Sec-Fetch-Dest` 必须为空、`document` 或 `iframe`；有 Upgrade 或 WebSocket Key 的请求直连原站。HTML 的 `q=0` 保守地禁用本次注入，即使另一个 HTML 类型权重非零。只有符合条件的文档 GET 的 `404/502/503/504` 会回原站；关闭 Helper 注入的 404 也回退。连接超时 1 秒、读取超时 10 秒。没有 HTML Accept 的老客户端直接获得原页面，不保证显示按钮。

`/api/v1/`、模型 POST、SSE、WebSocket、上传与静态文件仍直连原站，不因 Helper 失败自动重试。原站认证和登出保持原路由。网关不只有 `/v1/`：样例也排除 `/v1beta/`、`/backend-api/`、`/antigravity/`、`/responses`、`/chat/completions`、`/images/*`、`/videos/*`、语音、搜索和视频任务根别名，按已审阅 v0.2.23 维护。上游增加路由时重新核对；保留流式、Upgrade 和 `proxy_next_upstream off`。

原生按钮在浏览器里给原站账号和用户表格操作列追加「定时」「邮件」按钮，沿用原表格的数据和现有操作。按钮以可确认的行 ID 为准，随切页和表格重绘更新，并验证当前原站管理员。当前要求 `session_binding_enabled` 明确关闭；启用或读不到时停止增强操作，不修改此设置、不强行验证可能撤销会话的 JWT。可继续用独立管理页面。注入保留上游 CSP 和 nonce；没有可靠行 ID 时不猜测目标。上游调整表格结构后，新增按钮可能暂时不显示，需要重新适配和验证。

开启增强后，首次打开页面的 HTML 会多经过一次 Helper；原站登录 API、管理 API、模型请求和流式连接仍直达 sub2api。Helper 不可用时，入口会回到原站，首次加载 HTML（例如首次打开登录页或后台页）可能多等一个连接或读取超时，但登录提交和业务 API 不经过 Helper。

隔离验收应覆盖：首页/登录页再进账号列表、直接打开列表、前进后退与刷新、静态资源；停止 Helper 后登录、用户管理、模型 POST、SSE 和 WebSocket 仍可用。仓库测试使用真实 Nginx、当前镜像与假上游，无真实 Key、无付费模型调用：

```sh
python3 tests/ingress_smoke.py "$HELPER_IMAGE"
```

这不是 ZJLAB 现场验收。长期若上游提供稳定的脚本加载和行扩展接口，可去掉 HTML 层，仅保留 `/_helper/`。当前插件配置 iframe 不提供原列表行扩展。

## 5. 回切与旧菜单

将 Nginx 的 `set $helper_injection_enabled 0;` 后检查并重载，新文档立即走原站，无需先重启或停 Helper。已打开的 SPA 标签页需完整刷新才能移除脚本。彻底撤掉接入时可移除 `/_helper/` location，保留其他原站路由。

入口回切不等于业务回滚。规则仍可运行；停止容器只停止未来执行，不恢复已修改的可调度状态、倍率、邮件或余额，也不撤销已发出的模型请求。停规则、恢复倍率应按业务语义操作，不能因页面回切重复奖励。保留 `/data`，不要删除数据卷来重置结算记录。

新菜单使用 `/_helper/features/...`。升级后重新同步功能菜单，更新历史 `/features/benchmark`、`/features/pricing`、`/features/ranking` 链接。迁移时可显式开启 `HELPER_LEGACY_ROUTES=true`，并在 Nginx server 中临时增加三个**精确**别名：

```nginx
location = /features/benchmark { proxy_pass $helper_origin$request_uri; }
location = /features/pricing   { proxy_pass $helper_origin$request_uri; }
location = /features/ranking   { proxy_pass $helper_origin$request_uri; }
```

确认菜单同步后删除别名并关闭 legacy。不要扩大为整个 `/features/`、`/api/` 或根路径代理。旧书签改为新 `/_helper/` 入口。

## SMTP、数据与升级

邮件使用独立 SMTP，管理 Key 不能替代凭据：

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=starttls
```

仅支持 `starttls` 或 `ssl` 并验证证书；配置 host 必须提供 from。先向测试邮箱验证，SMTP 接受不等于最终送达。

`/data` 保存计划、规则、审计、结算去重、画廊与加密凭据。主密钥在数据库旁 `.key` 文件中，备份整个卷和权限，并保护 `.env`。停 Helper 后可复制卷再启动，原站不受影响。恢复数据库与对应密钥，确保 UID/GID `10001:10001` 可读写、密钥 `0600`。密钥丢失不能通过重新生成恢复。

升级前记录源码 SHA、镜像 ID 并备份数据，使用同一网络、环境和卷重建单实例。切旧镜像前也备份，不假定旧版理解新版数据。发布与上游审阅见 [发布说明](releases.md)。

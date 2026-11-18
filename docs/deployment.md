# Docker 部署指南

将现有 sub2api 的网页入口接到 Helper，继续使用原来的公网域名。Helper 再访问原 sub2api 内网服务，浏览器中的管理员登录、用户列表和账号列表保持在同一个来源。

```text
浏览器 → 原有 HTTPS 域名 → Nginx → Helper:8080 → sub2api:8080
                                  │
                                  └─ /data 持久化数据卷
```

镜像提供 `linux/amd64` 和 `linux/arm64`，按正式 Release 固定版本部署。仓库不维护浮动 `latest` 标签。静态 [Pages 演示](pages.md) 只用于查看界面，实际定时任务和发信需要本指南中的后端服务。

## 1. 准备版本与网络

准备 Docker、可用的 sub2api 服务，以及现有 HTTPS 反向代理。Helper 使用 sub2api 管理员 API Key 执行后台任务，邮件另需 SMTP 服务。

从 [已发布版本](https://github.com/AaronYang0628/sub2api-helper/releases) 选择一个 Release，用其实际 tag 替换下面的 `vX.Y.Z`。源码和镜像使用同一个版本。

```sh
HELPER_RELEASE_TAG=vX.Y.Z
git clone --depth 1 --branch "$HELPER_RELEASE_TAG" \
  https://github.com/AaronYang0628/sub2api-helper.git
cd sub2api-helper
HELPER_VERSION=$(cat helper/VERSION)
HELPER_IMAGE="ghcr.io/aaronyang0628/sub2api-helper:${HELPER_VERSION}"
docker pull "$HELPER_IMAGE"
```

查询原 sub2api 容器所在的用户自定义网络。容器名不同时，将命令中的 `sub2api` 替换为实际名称。

```sh
docker inspect sub2api \
  --format '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}'
SUB2API_DOCKER_NETWORK="替换为上一步的网络名"
```

Helper 需要加入这个网络，才能通过容器名访问上游。上游官方 Docker 配置的容器内部端口默认为 `8080`，宿主机映射端口可以不同。重建原 sub2api 容器时，仍需保留所选网络和对应 DNS 名称。

## 2. 配置连接

```sh
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，按实际环境填写以下值。这里的域名和容器名都是示例。

```dotenv
SUB2API_URL=http://sub2api:8080
SUB2API_PUBLIC_URL=https://sub2api.example.com
HELPER_PUBLIC_URL=https://sub2api.example.com
HELPER_NATIVE_UI=true
SUB2API_ADMIN_KEY=
HELPER_DB_PATH=/data/helper.db
TZ=Asia/Shanghai
```

| 配置 | 应填写的地址 |
| --- | --- |
| `SUB2API_URL` | Helper 能访问的原 sub2api 内网根地址，不带 `/api/v1` |
| `SUB2API_PUBLIC_URL` | 用户原来打开 sub2api 的公网 HTTPS 根地址，非标准端口也要保留 |
| `HELPER_PUBLIC_URL` | 用户浏览器能访问 Helper 的根地址，原生模式下通常与上一项相同 |

**`SUB2API_URL` 必须直达原后端，不能指回已经经过 Helper 的公网入口，否则会形成代理循环。** 容器内的 `localhost` 指向 Helper 自己。

`SUB2API_ADMIN_KEY` 可以预先填入受保护的 `.env`，也可以留空，启动后在 `/_helper/setup` 完成一次绑定。推荐始终预设可信的 `SUB2API_URL`。如果连服务地址也留空，首次绑定前只在受控网络开放 Helper。

原生行操作要求管理 Key 能读 `/api/v1/admin/settings`，用于核对会话绑定设置。当前适配版本推荐使用 FullAccess 或原有全权限管理 Key。所用功能还需要对应的账号、用户或设置权限。上游要求运营确认时，由管理员在 sub2api 中完成确认。

保护 `.env`、数据卷和 Docker 管理权限，不将 Key 写入仓库、页面链接或统一的 Nginx 请求头。日常原生行操作验证当前已登录管理员，无需另外填写 Helper 密码。`HELPER_ADMIN_TOKEN` 仅在需要独立的自动化管理凭据时配置。

## 3. 启动容器

下面使用宿主机 `18080`，为原 sub2api 已使用的端口留出空间。

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
```

```sh
curl --fail http://127.0.0.1:18080/healthz
docker logs --tail 100 sub2api-helper
```

健康接口应返回 `status=ok` 和镜像版本。它只验证进程存活，上游权限、SMTP 和定时任务仍需分别确认。

仅运行一个 Helper 副本和一个 worker。镜像已固定一个 worker，SQLite 和后台任务不支持多个实例同时接管同一数据卷。

## 4. 接入原有 HTTPS 入口

下面适用于安装在宿主机上的 Nginx。保留原来的公网域名和证书，将网页入口转到 `127.0.0.1:18080`。`map` 放在 Nginx 的 `http` 配置层，不能放进 `server`。

```nginx
map $http_upgrade $helper_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name sub2api.example.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    # 可沿用原站点的请求体限制，示例不额外限制模型上传大小
    client_max_body_size 0;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $helper_connection_upgrade;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # 内嵌功能首次访问可能携带用户 JWT，避免记录完整请求 URL
        access_log off;
    }
}
```

合并到已有站点配置后检查并重新加载 Nginx。

```sh
sudo nginx -t
sudo systemctl reload nginx
```

若 Nginx 也运行在容器中，让它加入相同 Docker 网络，并将示例的 `proxy_pass` 改为 `http://sub2api-helper:8080`。Nginx 容器中的 `127.0.0.1` 不是宿主机。

必须填写真实的 `SUB2API_PUBLIC_URL`，尤其是在外层终止 TLS 的环境中。Helper 默认不信任传入的转发 IP 头，并使用受信任的公网配置处理 HTTPS 重定向和同源 WebSocket。上游通常看到 Helper 的网络来源，切换入口后可重新登录管理员。

如需让模型网关继续直达原 sub2api，可沿用原入口对 `/v1/`、`/v1beta/` 等网关路径的转发。原生网页、管理 API `/api/v1/` 和 `/_helper/` 仍应按同源入口配置，注意保留各自的流式及 WebSocket 转发。

## 5. 首次验证

1. 打开原公网域名下的 `/_helper/setup`。未通过环境预配置 Key 时，在这里验证并绑定管理员 Key，填写的服务地址仍为内网 `SUB2API_URL`。
2. 使用原 sub2api 管理员账号登录，检查用户行的邮件按钮和账号行的闹钟按钮。普通用户没有这些操作权限。
3. 只对测试账号保存一个时间窗口，确认上游当前的可调度状态与窗口一致，再测试显式保存全天。首次访问和默认显示全天都不会批量开启账号。
4. 配置 SMTP 后向自己管理的测试用户发一封邮件，再启用需要的自动规则。

原生行操作要求上游的 IP/UA 会话绑定设置明确为关闭。绑定启用或无法读取其状态时，Helper 会停止该操作，原页面继续代理。若业务需要保留会话绑定，可使用独立管理模式通过管理 Key 操作。部署前后都不要把一次健康检查当作所有写权限已经验证。

## SMTP

自定义发信使用 Helper 自己的 SMTP 配置，上游管理员 Key 不能代替邮件服务凭据。将服务商提供的值写入 `.env`，然后重建 Helper 容器。

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=starttls
```

填写发件人地址以及服务商要求的用户名、密码或专用授权码。`starttls` 常用端口 `587`，`ssl` 常用端口 `465`。只配置 TLS 模式而未指定端口时，程序会选择相应默认端口。复制 `.env.example` 后若改用 `ssl`，也要把已有的 `SMTP_PORT=587` 改成服务商的 SSL 端口。

SMTP 始终验证证书。配置 `SMTP_HOST` 时必须同时配置 `SMTP_FROM`。SMTP 接受投递不代表邮件已进入收件箱，先用测试邮件确认发信域名和服务商状态。

## 自定义菜单与独立模式

原生行操作之外，可按需在高级管理中启用内置菜单。菜单链接由 `HELPER_PUBLIC_URL` 生成，这个地址必须能被用户浏览器访问。在原生模式中使用原 sub2api 公网地址即可，核对外层代理允许同源 iframe，并避免记录内嵌链接的查询参数。

需要独立管理页面时设置 `HELPER_NATIVE_UI=false`，将 Helper 放在单独的 HTTPS 地址，例如 `helper.example.com`，原 sub2api 网页入口继续指向原服务。此时 `HELPER_PUBLIC_URL` 填 Helper 地址，`SUB2API_PUBLIC_URL` 仍填 sub2api 地址。独立页面通过管理员 Key 建立会话，原列表不注入按钮。依赖用户 JWT 的内置功能页仍受上游会话绑定限制。

## 数据、备份与升级

`/data` 保存规则、计划、审计、去重记录和已绑定的加密凭据。浏览器绑定产生的主密钥位于数据库旁的 `.key` 文件中。备份时保留整个数据卷及其权限，另行保护 `.env`。数据库和主密钥一起泄漏仍等同于凭据泄漏。

可在短暂停止 Helper 后复制整个目录，原 sub2api 服务继续运行。

```sh
mkdir -p backups
chmod 700 backups
docker stop sub2api-helper
docker cp sub2api-helper:/data "./backups/helper-data-$(date +%Y%m%d-%H%M%S)"
docker start sub2api-helper
```

恢复时同时恢复数据库和对应主密钥。镜像使用 UID/GID `10001:10001`，宿主机绑定目录或恢复文件后需保证该用户有读写权限，主密钥保持 `0600`。已有加密绑定但主密钥丢失或损坏时，程序会报错，不能通过生成新密钥恢复原凭据。

升级前备份，将 `NEW_HELPER_RELEASE_TAG` 设为选定的已发布 tag，再从该版本的 `helper/VERSION` 取得镜像版本。

```sh
git fetch --tags origin
git checkout "$NEW_HELPER_RELEASE_TAG"
HELPER_VERSION=$(cat helper/VERSION)
HELPER_IMAGE="ghcr.io/aaronyang0628/sub2api-helper:${HELPER_VERSION}"
docker pull "$HELPER_IMAGE"
docker stop sub2api-helper
docker rm sub2api-helper
```

之后按第 3 步使用原网络、原 `.env` 和原数据卷重新创建容器，完成健康与行操作检查。删除容器会保留命名卷，升级时保留该卷。版本适配和发布流程见 [发布说明](releases.md)。

## 常见问题

| 现象 | 检查方向 |
| --- | --- |
| 镜像拉取失败 | 核对 Release 已发布、版本来自对应 tag 的 `helper/VERSION`，以及主机可访问 GHCR |
| 页面出现代理错误或循环跳转 | 核对 `SUB2API_URL` 是内网原服务，容器加入了正确网络，使用的是上游容器端口 |
| 页面正常但按钮不可用 | 确认打开的是 Helper 代理后的入口、当前为管理员、Key 已绑定，并检查上游会话绑定状态 |
| 提示权限不足或运营确认 | 核对管理 Key 对应权限，在上游完成所需确认 |
| HTTPS 重定向不正确或 WebSocket 被拒绝 | 核对公网 URL 的协议、主机和端口，以及 Nginx 的 Upgrade 转发 |
| 流式回答延迟成批出现 | 检查外层 Nginx、CDN 等是否仍缓冲响应，以及读取超时是否足够 |
| 发信不可用 | 检查 SMTP 发件人、端口与 TLS 组合，使用服务商要求的授权方式 |
| 数据库或主密钥无法读取 | 检查卷挂载、UID `10001` 的权限，并恢复同一份备份中的数据库与主密钥 |

先查看 `docker logs --tail 100 sub2api-helper`，再在高级管理中查看兼容性结果及执行记录。更详细的接口范围与限制见 [上游兼容性说明](upstream-compatibility.md)，功能概览见 [项目首页](../README.md)。

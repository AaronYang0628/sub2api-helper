# GitHub Pages 界面演示

Pages 地址：<https://aaronyang0628.github.io/sub2api-helper/>。

这是静态交互演示：展示 sub2api 原生账号、用户列表中的新增按钮，复用实际的列表增强脚本、弹窗和上游样式。
演示用青色标出「定时」，用靛蓝色标出「邮件」。实际部署沿用 sub2api 原生按钮的配色。
在账号行点击闹钟可设置 `23:00 → 09:00` 等运行时段；在用户行点击邮件按钮可预览发信。
默认未接管的账号保留原有调度状态。高级自动化、闲置提醒与菜单设置可从弹窗进入。
主页的「检测结果示例」打开作品总览。示例模型的最新结果和近期作品在同一页，点开作品可查看当时的提示词。作品为离线示例。
高级「统计与检测」可以查看已配置分组、编辑模型和思考强度、添加示例分组，以及模拟运行同题任务。已有作品保留当时的模型和提示词。
画廊复用实际组件，作品在没有脚本或同源权限的 sandbox iframe 中展示。场景不引用外部资源，统计与生成操作均为内存中的模拟数据。
原有文本、PNG 和 GIF 固定题集示例保留在“固定题集结果”，也可从高级功能的“统计检测”运行模拟检测；分数与回答均为固定示例，不读取外部图片地址。
不需要填写真实管理员 Key；操作只影响演示数据，不访问真实 sub2api，不发送邮件、不调用付费模型、不充值。
示例修改只保存在页面内存，刷新即重置。实际的定时任务和发信功能需要部署 Docker 镜像，并将入口的 `/_helper/` 分流给 Helper。原生行按钮使用可选的网页注入。[部署文档](deployment.md)

## 首次启用

1. 打开 [仓库 Settings → Pages](https://github.com/AaronYang0628/sub2api-helper/settings/pages)。
2. 将 **Build and deployment → Source** 设为 **GitHub Actions**。
3. 在 [Deploy GitHub Pages demo](https://github.com/AaronYang0628/sub2api-helper/actions/workflows/pages.yaml) 运行工作流，或重新运行先前因未启用 Pages 而失败的部署。

这一步是仓库管理设置。GitHub 官方 `actions/configure-pages@v5` 明确说明，自动开启站点需要
`GITHUB_TOKEN` 以外的管理凭证；正常部署只需工作流内置的 Pages 权限，不需要额外 PAT。
所以本项目不把额外管理凭证放进工作流，也不声称仅推送代码就已经打开了站点设置。

后续修改 `site/`、实际界面资源或 `helper/VERSION` 并推送 `main` 后自动更新。部署地址和结果可在
Actions 的 `deploy` 作业及 `github-pages` environment 查看。即使首次部署设置未完成，成功的 `build`
作业也会生成可下载的静态站点制品，保留 7 天。

## 本地检查

```sh
node site/build.mjs
python3 -m http.server 8090 --directory _site
```

构建只使用 Node 内置模块，不需要安装额外依赖；站点资源使用相对路径，支持仓库子路径。
账号与用户演示各有静态目录，切换后刷新仍可正常打开。页面以 CSP `connect-src 'none'` 阻止网络 API 请求；
所有接口由演示适配器在内存中处理。固定的演示身份只由适配器提供，不写入浏览器凭据存储，也不会进入部署镜像。
生成目录 `_site/` 不提交到源码仓库。GitHub Actions 构建后上传 Pages 制品并部署。

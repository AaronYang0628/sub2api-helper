# GitHub Pages 界面演示

Pages 地址：<https://aaronyang0628.github.io/sub2api-helper/>。

这是静态交互演示：复用 Helper 实际界面和 sub2api 样式，用示例账号、调度及规则帮助预览功能。
不需要填写真实管理员 Key；操作只影响演示数据，不访问真实 sub2api，不发送邮件、不调用付费模型、不充值。
后台定时任务等实际功能仍需部署 Docker 镜像。

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
生成目录 `_site/` 不提交到源码仓库。GitHub Actions 构建后上传 Pages 制品并部署。

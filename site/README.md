# GitHub Pages 交互演示

在仓库根目录执行 `node site/build.mjs`，将生成 `_site/`。构建只使用 Node.js 内置模块，复制当前 Helper 的 HTML、CSS、SVG 和界面脚本，并注入静态示例数据适配器。

GitHub Pages 发布 `_site/` 即可；所有资源使用相对路径，支持 `/sub2api-helper/` 等仓库子路径。这里不是可部署的后端：连接输入被禁用，`/api/*` 由页面内存里的示例数据处理，CSP `connect-src 'none'` 阻止外发请求。示例修改不保存，刷新即可重置。用户应通过源码仓库的 Docker 部署说明安装真实服务。

本地预览：

```sh
node site/build.mjs
python3 -m http.server 8080 --directory _site
```

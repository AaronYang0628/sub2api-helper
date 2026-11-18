# GitHub Pages 交互演示

在仓库根目录执行 `node site/build.mjs`，将生成 `_site/`。构建只使用 Node.js 内置模块，复用实际的原生列表增强脚本、弹窗、CSS 和 SVG，并注入静态示例数据适配器。主页展示账号闹钟与用户邮件按钮；高级规则通过行内弹窗进入。

主页的“检测结果示例”打开 `results.html`，复用实际的检测结果渲染器，展示固定文本、PNG 与两帧 GIF。也可在行内高级功能中打开“统计检测”并运行模拟检测。图片均内嵌在示例数据中，不下载外部资源；展示分数只来自固定示例，不代表真实模型表现。

GitHub Pages 发布 `_site/` 即可；所有资源使用相对路径，支持 `/sub2api-helper/` 等仓库子路径，并生成账号、用户页面的静态目录以支持刷新。这里不是可部署的后端：`/_helper/api/*` 与 `/api/*` 由页面内存里的示例数据处理，CSP `connect-src 'none'` 阻止外发请求。演示身份不读取、保存真实凭据，示例修改不保存，刷新即可重置。用户应通过源码仓库的 Docker 部署说明安装真实服务。

本地预览：

```sh
node site/build.mjs
python3 -m http.server 8080 --directory _site
```

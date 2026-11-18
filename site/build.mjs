#!/usr/bin/env node
// Static Pages demo: native row extensions use only in-memory example data.
import { readFile, writeFile, mkdir, cp, rm } from 'node:fs/promises';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const source = dirname(fileURLToPath(import.meta.url));
const root = resolve(source, '..');
const target = join(root, '_site');
const assets = join(target, 'assets');
await rm(target, { recursive: true, force: true });
await mkdir(assets, { recursive: true });
let html = await readFile(join(root, 'helper/static/index.html'), 'utf8');
const appSource = await readFile(join(root, 'helper/static/app.js'), 'utf8');
if (!appSource.includes('let token = readSession();') || !html.includes('<div class="embed-shell">')) throw new Error('Helper UI structure changed; update the static demo adapter.');
html = html.replace('<head>', '<head>\n  <link rel="icon" href="data:,">').replace('<title>sub2api Helper · 管理助手</title>', '<title>sub2api Helper · 交互演示</title>')
  .replace('<meta name="referrer" content="no-referrer">', '<meta name="referrer" content="no-referrer">\n  <meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">')
  .replace('<script src="/static/icons.js"', '<script src="./assets/demo-adapter.js" defer></script>\n  <script src="/static/icons.js"')
  .replaceAll('"/static/', '"./assets/')
  .replace('<link rel="stylesheet" href="./assets/style.css">', '<link rel="stylesheet" href="./assets/style.css">\n  <link rel="stylesheet" href="./assets/demo.css">\n  <link rel="stylesheet" href="./assets/native-console.css">')
  .replace('<span>管理员 API Key</span>', '<span>演示凭据（固定示例）</span>')
  .replace('id="connection-url" name="url"', 'id="connection-url" readonly name="url"')
  .replace('id="connection-key" name="admin_key"', 'id="connection-key" disabled name="admin_key"')
  .replace('id="connect" class=', 'id="connect" disabled class=');
let app = appSource.replace('let token = readSession();', "let token = 'demo-session';")
  .replace('function toast(message, error = false) {', "function toast(message, error = false) {\n  message = '演示：' + message;")
  .replace('检测将使用所选分组的 API Key 调用模型并消耗额度，确认开始？', '演示将展示固定示例结果，不会调用模型或消耗额度，确认开始？')
  .replace('该检测会实际调用模型并消耗所选 Key 的额度。固定题集只反映本次回答表现，不代表全面的模型能力。', '演示检测使用固定示例结果，不会调用模型或消耗额度。实际部署后会通过用户自己的 Key 调用模型。');
await writeFile(join(target, 'advanced.html'), html);
await writeFile(join(assets, 'app.js'), app);
for (const name of ['icons.js', 'style.css', 'native.css', 'native-console.css', 'benchmark-results.js', 'benchmark-results.css', 'gallery-view.js', 'gallery-view.css', 'gallery-admin.css']) await cp(join(root, 'helper/static', name), join(assets, name));
const galleryAdmin = await readFile(join(root, 'helper/static/gallery-admin.js'), 'utf8');
await writeFile(join(assets, 'gallery-admin.js'), galleryAdmin.replace(
  '生成会实际调用所选模型并消耗该分组 Key 的额度。手动生成使用已保存的配置；暂停只停止后续定时生成。',
  '离线演示只修改本页示例，不调用模型、不消耗额度。刷新后恢复初始作品与配置。'));
const nativeSource = await readFile(join(root, 'helper/static/native.js'), 'utf8');
const consoleSource = "new URL('/_helper/console', location.origin)";
if (!nativeSource.includes(consoleSource)) throw new Error('Native advanced-console path changed; update the Pages-only URL rewrite.');
await writeFile(join(assets, 'native.js'), nativeSource.replace(consoleSource, "new URL('../../advanced.html', location.href)"));
for (const name of ['demo-adapter.js', 'demo.css']) await cp(join(source, name), join(assets, name));
const nativeHtml = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'self'">
  <title>sub2api Helper · 原生列表演示</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/style.css">
  <link rel="stylesheet" href="./assets/demo.css">
  <link rel="stylesheet" href="./assets/native.css">
  <script src="./assets/icons.js" defer></script>
  <script src="./assets/demo-adapter.js" defer></script>
  <script src="./assets/native.js" defer></script>
</head>
<body class="demo-native">
  <main class="demo-native-shell">
    <div class="demo-native-toolbar">
      <div class="demo-native-tabs" role="tablist" aria-label="原生列表演示">
        <button type="button" class="btn btn-secondary" id="demo-accounts" role="tab" aria-selected="true">账号管理</button>
        <button type="button" class="btn btn-ghost" id="demo-users" role="tab" aria-selected="false">用户管理</button>
      </div>
      <div class="demo-links"><a id="demo-benchmark-link" class="btn btn-secondary btn-sm" href="./gallery.html">检测结果示例</a><button type="button" class="btn btn-ghost btn-sm" id="demo-theme">深色</button><button type="button" class="btn btn-ghost btn-sm" id="demo-reset">重置演示</button><a class="btn btn-secondary btn-sm" href="https://github.com/AaronYang0628/sub2api-helper/blob/main/docs/deployment.md" target="_blank" rel="noopener noreferrer">部署说明</a></div>
    </div>
    <p class="demo-native-note">静态示例 · 点击账号行的闹钟设置运行时段，或点击用户行的邮件按钮。操作仅在本页模拟。</p>
    <section id="demo-native-list" aria-label="账号列表"></section>
    <p class="demo-native-footnote">实际使用时，按钮直接出现在 sub2api 原生列表中。未设置时段的账号保持原有调度状态。</p>
  </main>
</body>
</html>`;
await writeFile(join(target, 'index.html'), nativeHtml);
for (const page of ['accounts', 'users']) {
  const path = join(target, 'admin', page);
  await mkdir(path, { recursive: true });
  await writeFile(join(path, 'index.html'), nativeHtml.replaceAll('./assets/', '../../assets/').replace('href="./gallery.html"', 'href="../../gallery.html"'));
}
await writeFile(join(target, 'gallery.html'), `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>模型展示画廊 · sub2api Helper</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/style.css">
  <link rel="stylesheet" href="./assets/demo.css">
  <link rel="stylesheet" href="./assets/gallery-view.css">
  <script src="./assets/gallery-view.js" defer></script>
  <script src="./assets/demo-adapter.js" defer></script>
</head>
<body class="feature-body demo-gallery">
  <div class="feature-shell">
    <main class="feature-main">
      <div class="demo-native-toolbar"><a class="btn btn-secondary btn-sm" href="./admin/accounts/">返回账号列表</a><div class="demo-links"><a class="btn btn-secondary btn-sm" href="./results.html">固定题集结果</a><button id="demo-theme" type="button" class="btn btn-ghost btn-sm">深色</button></div></div>
      <p class="demo-native-note">离线画廊示例 · 两个示例分组展示原创 HTML/CSS 与 SVG 场景。统计、历史及倒计时均为示例数据，不调用模型、不消耗额度。</p>
      <section id="demo-gallery-main" aria-label="模型展示画廊"></section>
    </main>
  </div>
</body>
</html>`);
await writeFile(join(target, 'results.html'), `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>检测结果示例 · sub2api Helper</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/style.css">
  <link rel="stylesheet" href="./assets/demo.css">
  <link rel="stylesheet" href="./assets/benchmark-results.css">
  <script src="./assets/benchmark-results.js" defer></script>
  <script src="./assets/demo-adapter.js" defer></script>
</head>
<body class="feature-body demo-benchmark">
  <div class="feature-shell">
    <main id="demo-result-main" class="feature-main">
      <div class="demo-native-toolbar"><a class="btn btn-secondary btn-sm" href="./admin/accounts/">返回账号列表</a><button id="demo-theme" type="button" class="btn btn-ghost btn-sm">深色</button></div>
      <p class="demo-native-note">离线检测结果示例 · 使用固定文本回答与内嵌 PNG、GIF，不调用模型、不消耗额度。</p>
    </main>
  </div>
</body>
</html>`);
await writeFile(join(target, '.nojekyll'), '');
console.log('Built _site/: native account/user row actions with local-only sample data.');

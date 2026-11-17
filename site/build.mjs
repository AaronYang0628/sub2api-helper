#!/usr/bin/env node
// Static Pages demo: reuse the reviewed app UI, never its backend or credentials.
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
  .replace('<meta name="referrer" content="no-referrer">', '<meta name="referrer" content="no-referrer">\n  <meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\'; img-src \'self\' data:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">')
  .replace('<script src="/static/icons.js"', '<script src="./assets/demo-adapter.js" defer></script>\n  <script src="/static/icons.js"')
  .replaceAll('"/static/', '"./assets/')
  .replace('<link rel="stylesheet" href="./assets/style.css">', '<link rel="stylesheet" href="./assets/style.css">\n  <link rel="stylesheet" href="./assets/demo.css">')
  .replace('<div class="embed-shell">', '<div class="embed-shell">\n' + await readFile(join(source, 'notice.html'), 'utf8'))
  .replace('<span>管理员 API Key</span>', '<span>演示凭据（固定示例）</span>')
  .replace('id="connection-url" name="url"', 'id="connection-url" readonly name="url"')
  .replace('id="connection-key" name="admin_key"', 'id="connection-key" disabled name="admin_key"')
  .replace('id="connect" class=', 'id="connect" disabled class=');
let app = appSource.replace('let token = readSession();', "let token = 'demo-session';")
  .replace('function toast(message, error = false) {', "function toast(message, error = false) {\n  message = '演示：' + message;")
  .replace('检测将使用所选分组的 API Key 调用模型并消耗额度，确认开始？', '演示将展示固定示例结果，不会调用模型或消耗额度，确认开始？')
  .replace('该检测会实际调用模型并消耗所选 Key 的额度。固定题集只反映本次回答表现，不代表全面的模型能力。', '演示检测使用固定示例结果，不会调用模型或消耗额度。实际部署后会通过用户自己的 Key 调用模型。');
await writeFile(join(target, 'index.html'), html);
await writeFile(join(assets, 'app.js'), app);
for (const name of ['icons.js', 'style.css']) await cp(join(root, 'helper/static', name), join(assets, name));
for (const name of ['demo-adapter.js', 'demo.css']) await cp(join(source, name), join(assets, name));
await writeFile(join(target, '.nojekyll'), '');
console.log('Built _site/: native helper UI + local-only interactive sample data.');

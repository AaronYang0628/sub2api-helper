/* Offline regression for the embedded console, native sizing and menu readback. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');
(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-console-ui-'));
  let publicOrigin = 'https://api.example.test', syncedOrigin = publicOrigin;
  let modules = { benchmark: true, pricing: false, ranking: true };
  let rejectSave = false, rejectSync = false;
  const writes = [], errors = [], resources = [], pendingMetrics = [];
  const labels = { benchmark: '智商检测', pricing: '模型价格统计', ranking: '用户用量排名' };
  const settings = () => ({ public_origin: publicOrigin, origin_source: 'stored', sub2api_public_origin: 'https://api.example.test', modules, menus: Object.entries(labels).map(([key, label]) => ({ key, id: `sub2api-helper-${key}`, label, url: `${publicOrigin}/_helper/features/${key}`, enabled: modules[key], icon_svg: '' })), ready: Boolean(publicOrigin), problems: [], in_sync: publicOrigin === syncedOrigin });
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/_helper/api/admin/')) {
      let raw = ''; for await (const part of req) raw += part;
      const body = raw ? JSON.parse(raw) : undefined;
      if (req.method !== 'GET') writes.push({ path: url.pathname, method: req.method, body });
      let result = {}, code = 200;
      const route = url.pathname.slice('/_helper/api/admin'.length);
      if (route.startsWith('/analytics/')) { pendingMetrics.push(res); return; }
      if (route === '/connection') result = { upstream_url: 'https://api.example.test', public_url: publicOrigin };
      else if (route === '/status') result = { upstream_url: 'https://api.example.test', public_url: publicOrigin, timezone: 'Asia/Shanghai', mail_configured: true, version: 'fixture', engine: { running: true } };
      else if (route === '/modules') { if (req.method === 'PUT') { modules = body; syncedOrigin = publicOrigin; } result = modules; }
      else if (route === '/menu-settings') { if (req.method === 'PUT') { if (rejectSave) { code = 422; result = { detail: '此地址不能供用户访问' }; } else publicOrigin = body.public_origin; } if (code === 200) result = settings(); }
      else if (route === '/menu-settings/sync') { if (rejectSync) { code = 502; result = { detail: '上游菜单回读不一致' }; } else { const changed = syncedOrigin !== publicOrigin; syncedOrigin = publicOrigin; result = { ...settings(), sync_status: 'verified', changed }; } }
      else if (route === '/accounts') result = [{ id: 1, name: '跨夜免费额度账号', platform: 'anthropic', status: 'active', schedulable: true, group_ids: [1] }];
      else if (route === '/gallery') result = { groups: [], timezone: 'Asia/Shanghai' };
      else if (route === '/benchmark/options') result = { keys: [] };
      else if (['/schedules', '/rules', '/automations', '/settlements', '/users', '/groups', '/audit'].includes(route)) result = [];
      res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(result)); return;
    }
    if (url.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    if (url.pathname === '/host.css') { res.writeHead(200, { 'Content-Type': 'text/css' }); res.end('body{margin:0}.host{padding:16px}.native-reference{display:flex;align-items:center;gap:16px;margin-bottom:16px}.native-reference input{width:220px}.host iframe{display:block;width:100%;height:1100px;border:0}'); return; }
    if (url.pathname === '/host') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/_helper/static/style.css"><link rel="stylesheet" href="/host.css"></head><body><main class="host"><div class="native-reference"><button class="btn btn-primary">原生按钮</button><input class="input" value="原生输入框"></div><iframe title="助手嵌入界面" src="/_helper/console#modules"></iframe></main></body></html>'); return; }
    const asset = url.pathname.startsWith('/_helper/static/');
    const file = path.join(root, 'helper/static', asset ? path.basename(url.pathname) : 'index.html');
    let content = await fs.readFile(file, 'utf8');
    if (url.pathname === '/_helper/console') content = content.replace('</head>', '<link rel="stylesheet" href="/_helper/static/native-console.css"></head>');
    res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'none'" }); res.end(content);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, locale: 'zh-CN' });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', item => { if (item.type() === 'error' && !/Failed to load resource/.test(item.text())) errors.push(item.text()); });
    page.on('response', response => { if (response.url().includes('/_helper/static/')) resources.push({ status: response.status(), url: response.url() }); });
    page.on('request', request => assert.equal(new URL(request.url()).origin, origin, 'console assets never request placeholder/external images'));
    page.on('dialog', dialog => dialog.accept());
    await page.addInitScript(() => sessionStorage.setItem('helper_session', 'fixture-only'));
    await page.goto(origin + '/_helper/setup#modules');
    await page.getByText('上游菜单与已保存的配置一致。', { exact: true }).waitFor();
    const switches = page.locator('.module-bottom .helper-switch');
    assert.equal(await switches.count(), 3);
    const measureSwitches = async () => {
      const measured = await switches.evaluateAll(nodes => nodes.map(node => {
        const caption = node.lastElementChild;
        const style = getComputedStyle(caption);
        return { box: node.getBoundingClientRect().toJSON(), caption: caption.getBoundingClientRect().toJSON(),
          track: node.querySelector('.switch').getBoundingClientRect().toJSON(), font: style.fontSize,
          lineHeight: parseFloat(style.lineHeight), writingMode: style.writingMode, whiteSpace: style.whiteSpace };
      }));
      for (const item of measured) {
        assert.equal(item.font, '14px');
        assert.equal(item.writingMode, 'horizontal-tb');
        assert.equal(item.whiteSpace, 'nowrap');
        assert(item.caption.width > 0 && item.caption.height <= item.lineHeight + 1, 'switch caption stays on one horizontal line');
        assert(item.caption.left > item.track.right, 'switch caption is next to the track');
        assert(Math.abs(item.track.width - 44) < 1 && Math.abs(item.track.height - 24) < 1);
        assert(item.box.width > item.track.width + item.caption.width);
      }
      return measured;
    };
    await measureSwitches();
    assert.equal(await page.locator('img').count(), 0, 'console icons use real local inline SVG, never fake raster assets');
    assert(await page.locator('.module-icon svg path').evaluateAll(nodes => nodes.every(node => node.getAttribute('d') && node.getBBox().width > 0)));
    const originInput = page.locator('[name=public_origin]');
    await originInput.fill('https://new.example.test');
    await page.getByText('https://new.example.test/_helper/features/benchmark', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '同步菜单', exact: true }).isDisabled(), true);
    rejectSave = true; await page.getByRole('button', { name: '保存域名', exact: true }).click(); await page.getByText('访问域名保存失败：此地址不能供用户访问', { exact: true }).waitFor(); assert.equal(await originInput.inputValue(), 'https://new.example.test');
    rejectSave = false; await page.getByRole('button', { name: '保存域名', exact: true }).click(); await page.getByText('访问域名已保存。点击“同步菜单”后才会更新上游入口。', { exact: true }).waitFor(); assert.equal(syncedOrigin, 'https://api.example.test', 'saving origin never silently syncs');
    rejectSync = true; await page.getByRole('button', { name: '同步菜单', exact: true }).click(); await page.getByText('上次同步未得到确认。当前菜单状态未知，请先刷新核对。', { exact: true }).waitFor(); assert.equal(await page.getByText('上游菜单与已保存的配置一致。', { exact: true }).count(), 0);
    rejectSync = false; await page.getByRole('button', { name: '同步菜单', exact: true }).click(); await page.getByText('菜单已同步，并通过上游回读确认。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '使用 sub2api 地址', exact: true }).click(); assert.equal(await originInput.inputValue(), 'https://api.example.test', 'known sub2api public origin wins over the local Helper host'); assert.equal(publicOrigin, 'https://new.example.test', 'autodetect is a draft, not a write');
    await originInput.fill('https://new.example.test');
    await switches.nth(1).locator('input').check(); await page.getByText('功能状态与上游菜单已确认同步', { exact: true }).waitFor(); assert.equal(modules.pricing, true);
    await page.getByRole('button', { name: '关闭通知', exact: true }).click();
    for (const width of [390, 320]) { await page.setViewportSize({ width, height: 844 }); await measureSwitches(); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page-wide overflow at narrow widths'); await page.locator('.module-card').last().scrollIntoViewIfNeeded(); await page.getByRole('link', { name: '功能与菜单', exact: true }).scrollIntoViewIfNeeded(); assert.equal(await page.getByRole('link', { name: '功能与菜单', exact: true }).getAttribute('aria-current'), 'page'); }
    await page.evaluate(() => document.documentElement.classList.add('dark')); await page.waitForTimeout(350); assert.equal(await page.locator('.module-card').first().evaluate(node => getComputedStyle(node).backgroundColor), 'rgba(30, 41, 59, 0.5)'); await page.screenshot({ path: path.join(directory, 'modules-mobile-dark.png'), fullPage: true });
    await page.goto(origin + '/_helper/setup#accounts'); await page.getByRole('heading', { name: '账号管理', exact: true }).waitFor();
    const tableStyle = await page.locator('th').first().evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, x: s.paddingLeft, y: s.paddingTop }; });
    assert.deepEqual(tableStyle, { font: '14px', line: '20px', x: '20px', y: '16px' }, 'native TablePageLayout.vue admin cell typography and spacing');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.goto(origin + '/_helper/setup#schedules'); await page.getByRole('button', { name: '新建调度', exact: true }).click();
    const dialog = page.locator('#editor'); await dialog.waitFor(); const modalPadding = await dialog.locator('.dialog-header').evaluate(node => getComputedStyle(node).paddingLeft); assert.equal(modalPadding, '16px'); assert.equal(await dialog.locator('h2').evaluate(node => getComputedStyle(node).fontSize), '18px'); await dialog.getByRole('button', { name: '关闭', exact: true }).click();
    await page.goto(origin + '/_helper/setup#analytics'); await page.getByRole('heading', { name: '统计与检测', exact: true }).waitFor(); await page.locator('.helper-gallery-admin-catalog').waitFor(); assert.equal(await page.getByText('正在读取统计数据…', { exact: true }).count(), 2, 'slow optional metrics do not block gallery mount');
    await page.waitForFunction(() => document.querySelectorAll('.helper-gallery-admin-catalog').length === 1);
    for (const response of pendingMetrics.splice(0)) { response.writeHead(403, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ detail: '此统计暂不可用' })); }
    await page.getByText('此统计暂不可用', { exact: true }).first().waitFor(); assert.equal(await page.locator('.helper-gallery-admin-catalog').count(), 1, 'denied metrics keep gallery available');
    await page.setViewportSize({ width: 1280, height: 1000 }); await page.goto(origin + '/host');
    const embedded = page.frameLocator('iframe'); await embedded.getByRole('heading', { name: '功能与菜单', exact: true }).waitFor();
    const reference = await page.locator('.native-reference button').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius }; });
    const actual = await embedded.getByRole('button', { name: '保存域名', exact: true }).evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius }; });
    assert.deepEqual(actual, reference, 'embedded buttons exactly match pinned native .btn typography and shape');
    const nativeInput = await page.locator('.native-reference input').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius, height: node.getBoundingClientRect().height }; });
    const embeddedInput = await embedded.locator('[name=public_origin]').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius, height: node.getBoundingClientRect().height }; });
    assert.deepEqual(embeddedInput, nativeInput, 'embedded form inputs exactly match pinned native .input');
    assert.equal(await embedded.locator('html').evaluate(node => getComputedStyle(node).fontSize), '16px');
    assert.equal(await embedded.locator('.embed-shell').evaluate(node => getComputedStyle(node).paddingLeft), '0px', 'embedded console avoids duplicated surrounding padding');
    assert.equal(await embedded.locator('.page-head h1').evaluate(node => getComputedStyle(node).fontSize), '18px');
    await page.screenshot({ path: path.join(directory, 'modules-iframe-native.png'), fullPage: true });
    await page.evaluate(() => document.body.style.zoom = '2');
    await page.waitForTimeout(250);
    const zoomed = await embedded.getByRole('button', { name: '保存域名', exact: true }).evaluate(node => node.getBoundingClientRect().height);
    const nativeZoomed = await page.locator('.native-reference button').evaluate(node => node.getBoundingClientRect().height);
    assert(Math.abs(zoomed - nativeZoomed / 2) < 1, `browser zoom keeps iframe CSS controls at native rem sizes: iframe=${zoomed}, native=${nativeZoomed}`);
    assert.equal(await embedded.locator('.helper-switch').first().locator('span').last().evaluate(node => getComputedStyle(node).whiteSpace), 'nowrap');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); await page.screenshot({ path: path.join(directory, 'modules-iframe-zoom.png'), fullPage: true });
    assert(resources.length >= 9 && resources.every(item => item.status === 200)); assert.deepEqual(errors, []);
    assert(writes.some(item => item.path.endsWith('/menu-settings') && item.method === 'PUT')); assert(writes.some(item => item.path.endsWith('/menu-settings/sync')));
    console.log(JSON.stringify({ passed: true, screenshots: directory, nativeButton: reference, nativeInput, tableStyle, checks: ['horizontal switches at320/390', 'native iframe typography and zoom', 'table and modal metrics', 'local SVG assets', 'origin draft/save/autodetect', 'explicit verified sync/errors', 'slow or denied metrics never block gallery', 'native controls remain interactive'] }, null, 2));
  } finally { for (const response of pendingMetrics) response.end('{}'); await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

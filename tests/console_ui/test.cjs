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
  let schedules = [];
  let rules = [{ id: '1', name: '旧闲置提醒规则', days: 30, subject: '账户长期未使用提醒', body: '您好 {username}', disable_after_email: false, enabled: false }];
  let automations = [
    { id: '1', name: '旧订阅倍率规则', type: 'subscription_multiplier', timezone: 'Europe/London', enabled: false, group_id: 88, expires_within_days: 2, remaining_fraction_gt: 0.5, quota_period: 'month', multiplier: 0.5, scope: 'user_group', restore_on_exit: false },
    { id: '2', name: '旧月度奖励规则', type: 'monthly_rewards', timezone: 'Europe/London', enabled: false, mode: 'approval', rewards: [{ rank: 1, amount: 50 }, { rank: 2, amount: 30 }, { rank: 3, amount: 10 }] }
  ];
  let delayPreviewMs = 0;
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
      else if (route === '/status') result = { upstream_url: 'https://api.example.test', public_url: publicOrigin, timezone: 'America/New_York', mail_configured: true, version: 'fixture', engine: { running: true } };
      else if (route === '/modules') { if (req.method === 'PUT') { modules = body; syncedOrigin = publicOrigin; } result = modules; }
      else if (route === '/menu-settings') { if (req.method === 'PUT') { if (rejectSave) { code = 422; result = { detail: '此地址不能供用户访问' }; } else publicOrigin = body.public_origin; } if (code === 200) result = settings(); }
      else if (route === '/menu-settings/sync') { if (rejectSync) { code = 502; result = { detail: '上游菜单回读不一致' }; } else { const changed = syncedOrigin !== publicOrigin; syncedOrigin = publicOrigin; result = { ...settings(), sync_status: 'verified', changed }; } }
      else if (route === '/accounts') result = [
        { id: 1, name: '跨夜免费额度账号', platform: 'anthropic', status: 'active', schedulable: true, group_ids: [1] },
        { id: 2, name: '超长名称测试账号_' + 'A'.repeat(95), platform: 'openai', status: 'active', schedulable: true, group_ids: [1] }
      ];
      else if (route === '/groups') result = [
        { id: 1, name: '主力订阅分组', status: 'active', subscription_type: 'subscription', rate_multiplier: 1 },
        { id: 2, name: '超长名称测试订阅分组_' + 'G'.repeat(95), status: 'active', subscription_type: 'subscription', rate_multiplier: 1 }
      ];
      else if (route === '/gallery') result = { groups: [], timezone: 'Asia/Shanghai' };
      else if (route === '/benchmark/options') result = { keys: [] };
      else if (route === '/schedules' || route.startsWith('/schedules/')) {
        if (req.method === 'GET') result = schedules;
        else if (req.method === 'POST') { const created = { id: String(schedules.length + 1), ...body }; schedules.push(created); result = created; }
        else if (req.method === 'PUT') { const id = route.split('/')[2]; const idx = schedules.findIndex(s => String(s.id) === id); schedules[idx] = { ...schedules[idx], ...body, id }; result = schedules[idx]; }
      }
      else if (route === '/rules' || route.startsWith('/rules/')) {
        if (req.method === 'GET') result = rules;
        else if (req.method === 'POST') { const created = { id: String(rules.length + 1), ...body }; rules.push(created); result = created; }
        else if (req.method === 'PUT') { const id = route.split('/')[2]; const idx = rules.findIndex(r => String(r.id) === id); rules[idx] = { ...rules[idx], ...body, id }; result = rules[idx]; }
      }
      else if (route === '/automations' || route.startsWith('/automations/')) {
        const parts = route.split('/');
        if (req.method === 'GET') result = automations;
        else if (req.method === 'POST' && parts[3] === 'preview') {
          if (delayPreviewMs > 0) await new Promise(resolve => setTimeout(resolve, delayPreviewMs));
          const item = automations.find(a => String(a.id) === parts[2]);
          result = item?.type === 'monthly_rewards'
            ? { type: 'monthly_rewards', month: '2026-10', actions: [{ action: 'credit_balance', rank: 1, user_id: 101, amount: item.rewards[0].amount, status: 'ready' }], skipped: [], errors: [] }
            : { type: 'subscription_multiplier', actions: [{ action: 'set_user_group_rate', user_id: 101, group_id: 1, multiplier: item?.multiplier || 0.5, status: 'ready' }], skipped: [], errors: [] };
        }
        else if (req.method === 'POST' && parts[3] === 'run') result = { executed: 1, actions: [{ action: 'credit_balance', user_id: 101, status: 'done' }], errors: [] };
        else if (req.method === 'POST') { const created = { id: String(automations.length + 1), ...body }; automations.push(created); result = created; }
        else if (req.method === 'PUT') { const id = parts[2]; const idx = automations.findIndex(a => String(a.id) === id); automations[idx] = { ...automations[idx], ...body, id }; result = automations[idx]; }
      }
      else if (['/settlements', '/users', '/audit'].includes(route)) result = [];
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
    const dialog = page.locator('#editor'); await dialog.waitFor(); const modalPadding = await dialog.locator('.dialog-header').evaluate(node => getComputedStyle(node).paddingLeft); assert.equal(modalPadding, '16px'); assert.equal(await dialog.locator('h2').evaluate(node => getComputedStyle(node).fontSize), '18px');
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false, 'schedule more-settings collapsed by default');
    await dialog.locator('input[name="account_ids"][value="1"]').check();
    assert.equal(await dialog.locator('[data-schedule-summary]').textContent(), '跨夜免费额度账号 每天 23:00 开启、次日 09:00 关闭（America/New_York）');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('跨夜免费额度账号 23:00-09:00', { exact: true }).waitFor();
    assert.deepEqual(schedules[0], { id: '1', name: '跨夜免费额度账号 23:00-09:00', start: '23:00', end: '09:00', timezone: 'America/New_York', account_ids: [1], weekdays: [0, 1, 2, 3, 4, 5, 6], enabled: true }, 'new schedule uses configured default timezone');
    schedules[0] = { id: '1', name: '旧自定义跨夜计划', start: '23:00', end: '09:00', timezone: 'Europe/London', account_ids: [1, 99], weekdays: [0, 2, 4], enabled: true };
    await page.reload();
    await page.getByText('旧自定义跨夜计划', { exact: true }).waitFor();
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    await dialog.locator('[data-schedule-summary]').waitFor();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false, 'legacy schedule edit keeps more-settings collapsed');
    assert.equal(await dialog.locator('[data-schedule-summary]').textContent(), '跨夜免费额度账号 等 2 个账号 周一、周三、周五 23:00 开启、次日 09:00 关闭（Europe/London）');
    await dialog.locator('input[name="end"]').fill('08:30');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('旧自定义跨夜计划', { exact: true }).waitFor();
    assert.deepEqual(schedules[0], { id: '1', name: '旧自定义跨夜计划', start: '23:00', end: '08:30', timezone: 'Europe/London', account_ids: [1, 99], weekdays: [0, 2, 4], enabled: true }, 'collapsed save preserves legacy name, weekdays, third timezone, and unreturned account_id');
    await page.locator('table .helper-switch input').first().uncheck();
    await page.getByText('规则已暂停', { exact: true }).waitFor();
    assert.equal(schedules[0].enabled, false);
    await page.locator('table .helper-switch input').first().check();
    await page.getByText('规则已启用', { exact: true }).waitFor();
    assert.equal(schedules[0].enabled, true);

    await page.goto(origin + '/_helper/setup#rules'); await page.getByText('旧闲置提醒规则', { exact: true }).waitFor();
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false);
    await dialog.locator('input[name="days"]').fill('45');
    await dialog.locator('input[name="disable_after_email"]').check();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('超过 45 天未使用', { exact: true }).waitFor();
    assert.equal(rules[0].name, '旧闲置提醒规则');
    assert.equal(rules[0].days, 45);
    assert.equal(rules[0].disable_after_email, true);

    await page.goto(origin + '/_helper/setup#automations'); await page.getByText('旧订阅倍率规则', { exact: true }).waitFor();
    await page.getByRole('button', { name: '编辑', exact: true }).first().click();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false);
    await dialog.locator('input[name="multiplier"]').fill('0.4');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('自动化规则已保存', { exact: true }).waitFor();
    assert.equal(automations[0].name, '旧订阅倍率规则');
    assert.equal(automations[0].timezone, 'Europe/London', 'editing legacy automation preserves its own timezone');
    assert.equal(automations[0].group_id, 88, 'editing legacy automation preserves unreturned group_id instead of switching to first group');
    assert.equal(automations[0].restore_on_exit, false);
    assert.equal(automations[0].multiplier, 0.4);
    const previewCallsBefore = writes.filter(w => w.path.endsWith('/preview')).length;
    await page.getByRole('button', { name: '编辑', exact: true }).nth(1).click();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false);
    assert.equal(writes.filter(w => w.path.endsWith('/preview')).length, previewCallsBefore, 'opening monthly rewards editor does not auto-request preview');
    await dialog.getByRole('button', { name: '加载预览', exact: true }).click();
    await dialog.locator('[data-automation-inline-preview] table').waitFor();
    assert.equal(await dialog.locator('[data-automation-inline-preview] table').textContent(), '操作名次用户 ID奖励（USD）状态发放余额奖励110150待执行');
    await dialog.locator('input[name="reward_amount"]').first().fill('80');
    assert.equal(await dialog.locator('[data-automation-inline-preview] table').count(), 0, 'changing reward amount immediately clears stale preview table');
    await dialog.locator('[data-preview-stale]').waitFor();
    assert.equal(await dialog.locator('[data-preview-stale]').textContent(), '规则参数已修改，请保存后重新预览。');
    assert.equal(writes.filter(w => w.path.endsWith('/run')).length, 0, 'preview and edit never call execute endpoint');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText(/第 1 名 \$80/).waitFor();
    assert.equal(automations[1].name, '旧月度奖励规则');
    assert.equal(automations[1].timezone, 'Europe/London');
    assert.equal(automations[1].rewards[0].amount, 80);
    // Verify in-flight preview response is ignored if parameters are edited before it returns
    await page.getByRole('button', { name: '编辑', exact: true }).nth(1).click();
    delayPreviewMs = 200;
    await dialog.getByRole('button', { name: '加载预览', exact: true }).click();
    await dialog.getByText('正在加载已保存规则的执行预览…', { exact: true }).waitFor();
    await dialog.locator('input[name="reward_amount"]').first().fill('90');
    await dialog.locator('[data-preview-stale]').waitFor();
    await page.waitForTimeout(260);
    delayPreviewMs = 0;
    assert.equal(await dialog.locator('[data-automation-inline-preview] table').count(), 0, 'late in-flight preview response is ignored after parameter edit');
    assert.equal(writes.filter(w => w.path.endsWith('/run')).length, 0, 'no execute endpoint called during in-flight preview cancellation');
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    await page.getByRole('button', { name: '新建规则', exact: true }).click();
    await dialog.locator('select[name="group_id"]').selectOption('2');
    await dialog.locator('input[name="multiplier"]').fill('0.75');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('自动化规则已保存', { exact: true }).waitFor();
    assert.equal(automations[2].timezone, 'America/New_York', 'new automation uses configured default timezone');
    assert(automations[2].name.length <= 100 && automations[2].name.endsWith('到期倍率 0.75x'), `auto-generated name respects 100-char limit: len=${automations[2].name.length}`);
    await page.getByRole('button', { name: '预览 / 执行', exact: true }).nth(1).click();
    await dialog.getByText('规则预览 · 旧月度奖励规则', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '关闭', exact: true }).first().click();
    await page.goto(origin + '/_helper/setup#analytics'); await page.getByRole('heading', { name: '统计与检测', exact: true }).waitFor(); await page.locator('.helper-gallery-admin-catalog').waitFor(); assert.equal(await page.getByText('正在读取统计数据…', { exact: true }).count(), 2, 'slow optional metrics do not block gallery mount');
    await page.waitForFunction(() => document.querySelectorAll('.helper-gallery-admin-catalog').length === 1);
    for (const response of pendingMetrics.splice(0)) { response.writeHead(403, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ detail: '此统计暂不可用' })); }
    await page.getByText('此统计暂不可用', { exact: true }).first().waitFor(); assert.equal(await page.locator('.helper-gallery-admin-catalog').count(), 1, 'denied metrics keep gallery available');
    assert.equal(await page.locator('.page-head h1').evaluate(node => getComputedStyle(node).fontSize), '18px', 'standalone setup keeps page-head h1');
    await page.setViewportSize({ width: 1280, height: 1000 }); await page.goto(origin + '/host');
    const embedded = page.frameLocator('iframe'); await embedded.getByRole('button', { name: '刷新核对', exact: true }).waitFor();
    assert.equal(await embedded.locator('.page-head h1').count(), 0, 'embedded console removes duplicate page-head h1');
    assert.equal(await embedded.locator('.page-head p').count(), 0, 'embedded console removes duplicate page-head description');
    assert.equal(await embedded.locator('main').getAttribute('aria-label'), '功能与菜单', 'embedded console preserves accessible landmark name');
    const reference = await page.locator('.native-reference button').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius }; });
    const actual = await embedded.getByRole('button', { name: '保存域名', exact: true }).evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius }; });
    assert.deepEqual(actual, reference, 'embedded buttons exactly match pinned native .btn typography and shape');
    const nativeInput = await page.locator('.native-reference input').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius, height: node.getBoundingClientRect().height }; });
    const embeddedInput = await embedded.locator('[name=public_origin]').evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, family: s.fontFamily, padX: s.paddingLeft, padY: s.paddingTop, radius: s.borderRadius, height: node.getBoundingClientRect().height }; });
    assert.deepEqual(embeddedInput, nativeInput, 'embedded form inputs exactly match pinned native .input');
    assert.equal(await embedded.locator('html').evaluate(node => getComputedStyle(node).fontSize), '16px');
    assert.equal(await embedded.locator('.embed-shell').evaluate(node => getComputedStyle(node).paddingLeft), '0px', 'embedded console avoids duplicated surrounding padding');
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

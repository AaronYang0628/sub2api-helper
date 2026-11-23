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
  let rejectSave = false, rejectSync = false, unverified = false;
  let schedules = [];
  let rules = [{ id: '1', name: '旧闲置提醒规则', days: 30, subject: '账户长期未使用提醒', body: '您好 {username}', disable_after_email: false, enabled: false }];
  let emailTemplates = [];
  let automations = [
    { id: '1', name: '旧订阅倍率规则', type: 'subscription_multiplier', timezone: 'Europe/London', enabled: false, group_id: 88, expires_within_days: 2, remaining_fraction_gt: 0.5, quota_period: 'month', multiplier: 0.5, scope: 'user_group', restore_on_exit: false },
    { id: '2', name: '旧月度奖励规则', type: 'monthly_rewards', timezone: 'Europe/London', enabled: false, mode: 'approval', rewards: [{ rank: 1, amount: 50 }, { rank: 2, amount: 30 }, { rank: 3, amount: 10 }] }
  ];
  let delayPreviewMs = 0, holdMetrics = false, failSettlements = false;
  const metricFailure = {};
  const metricReply = route => route === '/analytics/models'
    ? { models: [{ model: 'fixture-model', requests: 12, input_tokens: 30, output_tokens: 10, total_tokens: 40, cost: 0.5, actual_cost: 0.4 }] }
    : { ranking: [{ rank: 1, user_id: 7, username: 'fixture-user', requests: 9, tokens: 100, actual_cost: 1.25 }] };
  const settlements = [
    { month: '2026-10', start_date: '2026-10-01', end_date: '2026-10-31', timezone: 'Europe/London', rule_id: '2', actions: [
      { user_id: 101, rank: 1, amount: '80', actual_cost: '245.80', month: '2026-10', status: 'done', completed_at: '2026-11-01T00:02:00Z' },
      { user_id: 102, rank: 2, amount: '30', actual_cost: '180.24', month: '2026-10', status: 'done', completed_at: '2026-11-01T00:02:00Z' },
      { user_id: 103, rank: 3, amount: '10', actual_cost: '128.60', month: '2026-10', status: 'needs_review', attempted_at: '2026-11-01T00:01:00Z', reason: 'fixture：发放结果未知，等待人工核对' }] },
    { month: '2026-09', start_date: '2026-09-01', end_date: '2026-09-30', timezone: 'Europe/London', rule_id: 'removed-rule', actions: [
      { user_id: 104, rank: 1, amount: '50', month: '2026-09', status: 'skipped' }] }
  ];
  const writes = [], errors = [], resources = [], pendingMetrics = [], requests = [];
  const labels = { benchmark: '智商检测', pricing: '模型价格统计', ranking: '用户用量排名' };
  const settings = () => ({ public_origin: publicOrigin, origin_source: 'stored', sub2api_public_origin: 'https://api.example.test', modules, menus: Object.entries(labels).map(([key, label]) => ({ key, id: `sub2api-helper-${key}`, label, url: `${publicOrigin}/_helper/features/${key}`, enabled: modules[key], icon_svg: '' })), ready: Boolean(publicOrigin), problems: [], in_sync: publicOrigin === syncedOrigin });
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/_helper/api/admin/')) {
      let raw = ''; for await (const part of req) raw += part;
      const body = raw ? JSON.parse(raw) : undefined;
      if (req.method !== 'GET') writes.push({ path: url.pathname, method: req.method, body });
      const route = url.pathname.slice('/_helper/api/admin'.length);
      requests.push({ method: req.method, path: route });
      let result = {}, code = 200;
      if (route.startsWith('/analytics/')) {
        if (holdMetrics) { pendingMetrics.push(res); return; }
        if (metricFailure[route]) { code = metricFailure[route]; result = { detail: '此统计暂不可用' }; } else result = metricReply(route);
      }
      if (route === '/connection') result = { configured: true, upstream_url: 'https://api.example.test', public_url: publicOrigin };
      else if (route === '/status') {
        if (unverified) { code = 401; result = { detail: '未授权' }; }
        else result = { upstream_url: 'https://api.example.test', public_url: publicOrigin, timezone: 'America/New_York', mail_configured: true, version: 'fixture', engine: { running: true } };
      }
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
      else if (route === '/settlements') { if (failSettlements) { code = 503; result = { detail: '结算记录暂不可用' }; } else result = settlements; }
      else if (route === '/email-templates' || route.startsWith('/email-templates/')) {
        if (req.method === 'GET') result = emailTemplates;
        else if (req.method === 'POST') { const created = { id: String(emailTemplates.length + 1), ...body }; emailTemplates.push(created); result = created; }
        else if (req.method === 'PUT') { const id = route.split('/')[2]; const idx = emailTemplates.findIndex(t => String(t.id) === id); emailTemplates[idx] = { ...emailTemplates[idx], ...body, id }; result = emailTemplates[idx]; }
        else if (req.method === 'DELETE') { const id = route.split('/')[2]; emailTemplates = emailTemplates.filter(t => String(t.id) !== id); result = { ok: true }; }
      }
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
      else if (['/users', '/audit'].includes(route)) result = [];
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
    await page.addInitScript(() => {
      sessionStorage.setItem('helper_setup_session', 'fixture-only');
      sessionStorage.setItem('helper_native_console_session', 'fixture-native-only');
    });
    await page.goto(origin + '/_helper/setup#modules');
    await page.getByText('上游菜单与已保存的配置一致。', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('#navigation .nav-item').evaluateAll(nodes => nodes.map(n => n.textContent.trim())), ['规则', '功能与菜单', '智商检测', '排名与奖励', '运行与系统'], 'setup navigation consolidates to 5 tabs');
    assert.equal(await page.locator('#connection-summary').isVisible(), true, 'connected setup shows compact connection summary bar');
    assert.equal(await page.locator('#connection-form').isVisible(), false, 'connected setup hides credential input form by default');
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
    await page.goto(origin + '/_helper/setup#accounts'); await page.getByRole('heading', { name: '账号管理（备用入口）', exact: true }).waitFor();
    const tableStyle = await page.locator('th').first().evaluate(node => { const s = getComputedStyle(node); return { font: s.fontSize, line: s.lineHeight, x: s.paddingLeft, y: s.paddingTop }; });
    assert.deepEqual(tableStyle, { font: '14px', line: '20px', x: '20px', y: '16px' }, 'native TablePageLayout.vue admin cell typography and spacing');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const schedulesSection = page.locator('#rules-schedules');
    const usersSection = page.locator('#rules-users');
    const automationsSection = page.locator('#rules-automations');
    await page.goto(origin + '/_helper/setup#schedules'); await schedulesSection.getByRole('button', { name: /新建调度/ }).click();
    const dialog = page.locator('#editor'); await dialog.waitFor(); const modalPadding = await dialog.locator('.dialog-header').evaluate(node => getComputedStyle(node).paddingLeft); assert.equal(modalPadding, '16px'); assert.equal(await dialog.locator('h2').evaluate(node => getComputedStyle(node).fontSize), '18px');
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false, 'schedule more-settings collapsed by default');
    await dialog.locator('input[name="account_ids"][value="1"]').check();
    assert.equal(await dialog.locator('[data-schedule-summary]').textContent(), '跨夜免费额度账号 每天 23:00 开启、次日 09:00 关闭（America/New_York）');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await schedulesSection.getByText('跨夜免费额度账号 23:00-09:00', { exact: true }).waitFor();
    assert.deepEqual(schedules[0], { id: '1', name: '跨夜免费额度账号 23:00-09:00', start: '23:00', end: '09:00', timezone: 'America/New_York', account_ids: [1], weekdays: [0, 1, 2, 3, 4, 5, 6], enabled: true }, 'new schedule uses configured default timezone');
    schedules[0] = { id: '1', name: '旧自定义跨夜计划', start: '23:00', end: '09:00', timezone: 'Europe/London', account_ids: [1, 99], weekdays: [0, 2, 4], enabled: true };
    await page.reload();
    await schedulesSection.getByText('旧自定义跨夜计划', { exact: true }).waitFor();
    await schedulesSection.getByRole('button', { name: '编辑', exact: true }).click();
    await dialog.locator('[data-schedule-summary]').waitFor();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false, 'legacy schedule edit keeps more-settings collapsed');
    assert.equal(await dialog.locator('[data-schedule-summary]').textContent(), '跨夜免费额度账号 等 2 个账号 周一、周三、周五 23:00 开启、次日 09:00 关闭（Europe/London）');
    await dialog.locator('input[name="end"]').fill('08:30');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await schedulesSection.getByText('旧自定义跨夜计划', { exact: true }).waitFor();
    assert.deepEqual(schedules[0], { id: '1', name: '旧自定义跨夜计划', start: '23:00', end: '08:30', timezone: 'Europe/London', account_ids: [1, 99], weekdays: [0, 2, 4], enabled: true }, 'collapsed save preserves legacy name, weekdays, third timezone, and unreturned account_id');
    await schedulesSection.locator('table .helper-switch input').first().uncheck();
    await page.getByText('规则已暂停', { exact: true }).waitFor();
    assert.equal(schedules[0].enabled, false);
    await schedulesSection.locator('table .helper-switch input').first().check();
    await page.getByText('规则已启用', { exact: true }).waitFor();
    assert.equal(schedules[0].enabled, true);

    await page.goto(origin + '/_helper/setup#rules'); await usersSection.getByText('旧闲置提醒规则', { exact: true }).waitFor();
    await usersSection.getByRole('button', { name: '编辑', exact: true }).first().click();
    assert.equal(await dialog.locator('details.more-settings').evaluate(node => node.open), false);
    await dialog.locator('input[name="days"]').fill('45');
    await dialog.locator('input[name="disable_after_email"]').check();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await usersSection.getByText('超过 45 天未使用', { exact: true }).waitFor();
    assert.equal(rules[0].name, '旧闲置提醒规则');
    assert.equal(rules[0].days, 45);
    assert.equal(rules[0].disable_after_email, true);
    await usersSection.getByRole('button', { name: '新建邮件模板', exact: true }).click();
    await dialog.locator('input[name="name"]').fill('节假日维护模板');
    await dialog.locator('input[name="subject"]').fill('维护通知 · {username}');
    await dialog.locator('textarea[name="body"]').fill('您好 {username}，系统将在晚间维护。');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await usersSection.getByText('节假日维护模板', { exact: true }).waitFor();
    assert.equal(emailTemplates.length, 1);

    await page.goto(origin + '/_helper/setup#automations'); await automationsSection.getByText('旧订阅倍率规则', { exact: true }).waitFor();
    await automationsSection.getByRole('button', { name: '编辑', exact: true }).first().click();
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
    await automationsSection.getByRole('button', { name: '编辑', exact: true }).nth(1).click();
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
    await automationsSection.getByText(/第 1 名 \$80/).waitFor();
    assert.equal(automations[1].name, '旧月度奖励规则');
    assert.equal(automations[1].timezone, 'Europe/London');
    assert.equal(automations[1].rewards[0].amount, 80);
    // Verify in-flight preview response is ignored if parameters are edited before it returns
    await automationsSection.getByRole('button', { name: '编辑', exact: true }).nth(1).click();
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
    await automationsSection.getByRole('button', { name: /新建自动化/ }).click();
    await dialog.locator('select[name="group_id"]').selectOption('2');
    await dialog.locator('input[name="multiplier"]').fill('0.75');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('自动化规则已保存', { exact: true }).waitFor();
    assert.equal(automations[2].timezone, 'America/New_York', 'new automation uses configured default timezone');
    assert(automations[2].name.length <= 100 && automations[2].name.endsWith('到期倍率 0.75x'), `auto-generated name respects 100-char limit: len=${automations[2].name.length}`);
    await automationsSection.getByRole('button', { name: '预览 / 执行', exact: true }).nth(1).click();
    await dialog.getByText('规则预览 · 旧月度奖励规则', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '关闭', exact: true }).first().click();
    // Rules keep rule configuration only: no settlement history request or card.
    const statisticsRequests = () => requests.map(r => r.path).filter(path => /^\/(analytics|settlements|benchmark)/.test(path));
    requests.length = 0; await page.goto(origin + '/_helper/setup#rules'); await usersSection.getByText('旧闲置提醒规则', { exact: true }).waitFor(); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#main').getByText('月度结算记录', { exact: true }).count(), 0, 'monthly settlement history no longer renders in rules');
    assert.deepEqual(statisticsRequests(), [], 'rules requests no settlement history or metrics');
    assert.equal(await automationsSection.getByRole('link', { name: '结算记录', exact: true }).getAttribute('href'), '#rewards', 'rules link to the relocated settlement history');

    // 智商检测 keeps the gallery only: no fixed-question self-test and no statistics.
    requests.length = 0; await page.goto(origin + '/_helper/setup#analytics'); await page.getByRole('heading', { name: '智商检测', exact: true }).waitFor(); await page.locator('.helper-gallery-admin-catalog').waitFor(); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#main .benchmark-form, #main .secondary-analytics').count(), 0, 'fixed-question self-test and statistics sections removed from 智商检测');
    assert.equal(await page.locator('#main').getByText('固定题', { exact: false }).count(), 0, 'no self-test wording remains');
    assert.deepEqual(statisticsRequests(), [], '智商检测 requests no statistics or self-test endpoints');
    assert.equal(await page.locator('.page-head h1').evaluate(node => getComputedStyle(node).fontSize), '18px', 'standalone setup keeps page-head h1');
    await page.screenshot({ path: path.join(directory, 'analytics-gallery-only.png'), fullPage: true });

    // 排名与奖励: settlement records, model costs and user ranking each load and fail on their own.
    const mainRegion = page.locator('#main'), records = page.locator('#rewards-settlements');
    const rowText = async index => (await records.locator('tbody tr').nth(index).textContent()).replace(/\s+/g, ' ');
    requests.length = 0; holdMetrics = true;
    await page.goto(origin + '/_helper/setup#rewards'); await page.getByRole('heading', { name: '排名与奖励', exact: true }).waitFor();
    await records.locator('table').waitFor();
    assert.equal(await mainRegion.getByText('正在读取统计数据…', { exact: true }).count(), 2, 'both metric cards show their own loading state');
    assert.equal(await records.getByText('正在读取结算记录…', { exact: true }).count(), 0, 'settlement records do not wait for metrics');
    assert.equal(await records.locator('tbody tr').count(), 4, 'settlement history moved out of rules');
    assert.deepEqual([...new Set(requests.map(r => r.path).filter(path => !['/status', '/gallery'].includes(path)))].sort(), ['/analytics/models', '/analytics/ranking', '/automations', '/settlements'], 'rewards loads records plus both metrics');
    assert((await rowText(0)).startsWith('2026-102026-10-01 — 2026-10-311#101$80$245.80已发放旧月度奖励规则'), 'latest month ranks first and resolves the originating rule');
    assert((await rowText(2)).includes('3#103$10$128.60待人工核对旧月度奖励规则') && (await rowText(2)).includes('fixture：发放结果未知，等待人工核对'), 'uncertain payout keeps its manual-review status and reason');
    assert((await rowText(3)).includes('已跳过') && (await rowText(3)).includes('规则 #removed-rule'), 'records of deleted rules keep the rule ID fallback');
    await records.getByText('存在待核对的发放记录，这些记录不会自动重试。请先在 sub2api 核查余额变更记录，确认结果后再处理，避免重复奖励。', { exact: true }).waitFor();
    assert.equal(pendingMetrics.length, 2);
    const [modelsResponse, rankingResponse] = pendingMetrics.splice(0);
    modelsResponse.writeHead(403, { 'Content-Type': 'application/json' }); modelsResponse.end(JSON.stringify({ detail: '此统计暂不可用' }));
    rankingResponse.writeHead(200, { 'Content-Type': 'application/json' }); rankingResponse.end(JSON.stringify(metricReply('/analytics/ranking')));
    holdMetrics = false;
    await mainRegion.getByText('此统计暂不可用', { exact: true }).waitFor(); await mainRegion.getByText('fixture-user', { exact: true }).waitFor();
    assert.equal(await mainRegion.getByText('此统计暂不可用', { exact: true }).count(), 1, 'a denied metric reports an error in its own card only');
    assert.equal(await mainRegion.getByText('正在读取统计数据…', { exact: true }).count(), 0);
    assert.equal(await records.locator('tbody tr').count(), 4, 'settlement records remain available when a metric fails');
    await page.screenshot({ path: path.join(directory, 'rewards-metric-denied.png'), fullPage: true });
    failSettlements = true;
    await page.reload(); await records.getByText('结算记录读取失败：结算记录暂不可用', { exact: true }).waitFor();
    await mainRegion.getByText('fixture-model', { exact: true }).waitFor(); await mainRegion.getByText('fixture-user', { exact: true }).waitFor();
    assert.equal(await records.locator('table').count(), 0, 'failed records show an error instead of an empty history');
    assert.equal(await mainRegion.locator('table').count(), 2, 'metrics still render when the records request fails');
    failSettlements = false;
    await page.reload(); await records.locator('table').waitFor(); await mainRegion.getByText('fixture-model', { exact: true }).waitFor(); await mainRegion.getByText('fixture-user', { exact: true }).waitFor();
    assert.equal(await mainRegion.locator('table').count(), 3); assert.equal(await mainRegion.locator('.notice.error').count(), 0);
    assert.equal(await page.getByRole('link', { name: '排名与奖励', exact: true }).getAttribute('aria-current'), 'page');
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.screenshot({ path: path.join(directory, 'rewards-desktop.png'), fullPage: true });
    const reviewAction = settlements[0].actions[2]; reviewAction.status = 'done';
    await page.reload(); await records.locator('table').waitFor();
    assert.equal(await records.locator('.notice.warning').count(), 0, 'completed-only history needs no uncertain-payout warning');
    assert(!(await records.textContent()).includes('null'), 'optional warning never renders a literal null');
    reviewAction.status = 'needs_review';
    await page.reload(); await records.locator('.notice.warning').waitFor();
    for (const width of [390, 320]) { await page.setViewportSize({ width, height: 844 }); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `rewards has no page-wide overflow at ${width}px`); }
    await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.classList.add('dark')); await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(directory, 'rewards-mobile-dark.png'), fullPage: true });
    await page.goto(origin + '/_helper/setup#analytics'); await page.locator('.helper-gallery-admin-catalog').waitFor(); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#main .benchmark-form, #main .secondary-analytics').count(), 0);
    await page.screenshot({ path: path.join(directory, 'analytics-mobile-dark.png'), fullPage: true });

    // Legacy hashes: automation configuration stays in rules; the relocated history keeps a deep link.
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.goto(origin + '/_helper/setup#settlements'); await records.locator('table').waitFor();
    assert.equal(await page.getByRole('link', { name: '排名与奖励', exact: true }).getAttribute('aria-current'), 'page', 'legacy #settlements opens 排名与奖励');
    requests.length = 0; await page.goto(origin + '/_helper/setup#automations'); await automationsSection.getByText('旧订阅倍率规则', { exact: true }).waitFor(); await page.waitForLoadState('networkidle');
    assert.equal(await automationsSection.getByRole('button', { name: '预览 / 执行', exact: true }).count(), automations.length, 'automation configuration and preview stay in rules');
    assert.deepEqual(statisticsRequests(), [], 'legacy #automations stays on rule configuration without requesting history or metrics');
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
    await page.evaluate(() => { document.querySelector('iframe').contentWindow.location.hash = 'rewards'; });
    await embedded.locator('#rewards-settlements table').waitFor(); await embedded.locator('.overview-grid tbody tr').first().waitFor();
    assert.equal(await embedded.locator('main').getAttribute('aria-label'), '排名与奖励', 'native advanced console opens #rewards as a named landmark without a duplicate heading');
    assert.equal(await embedded.locator('.page-head h1, .page-head p').count(), 0);
    assert.equal(await embedded.locator('#navigation .nav-item').count(), 5, 'embedded console offers the same five tabs');
    unverified = true;
    await page.evaluate(() => { document.body.style.zoom = ''; sessionStorage.removeItem('helper_setup_session'); });
    await page.goto(origin + '/_helper/setup#rules');
    await page.getByText('已绑定 · 待验证', { exact: true }).first().waitFor();
    assert.equal(await page.locator('#connection-url').evaluate(node => node.readOnly), true, 'bound-unverified locks URL input');
    assert.equal(await page.locator('#connect').textContent(), '验证管理员身份');
    unverified = false;
    assert(resources.length >= 9 && resources.every(item => item.status === 200)); assert.deepEqual(errors, []);
    assert(writes.some(item => item.path.endsWith('/menu-settings') && item.method === 'PUT')); assert(writes.some(item => item.path.endsWith('/menu-settings/sync')));
    console.log(JSON.stringify({ passed: true, screenshots: directory, nativeButton: reference, nativeInput, tableStyle, checks: ['horizontal switches at320/390', 'native iframe typography and zoom', 'table and modal metrics', 'local SVG assets', 'origin draft/save/autodetect', 'explicit verified sync/errors', 'slow, denied and failed cards stay independent', 'rules and 智商检测 request no history or metrics', 'legacy #settlements and #automations hashes', 'embedded #rewards console', 'native controls remain interactive'] }, null, 2));
  } finally { for (const response of pendingMetrics) response.end('{}'); await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

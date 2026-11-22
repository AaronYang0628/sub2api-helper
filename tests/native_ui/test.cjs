/* Offline browser regression against the production native bridge. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const requireDependency = createRequire(path.join(root, 'ui/package.json'));
const { chromium } = requireDependency(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');

(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-native-ui-'));
  const css = path.join(directory, 'fixture-native.css');
  const compile = spawnSync(process.execPath, [path.join(root, 'ui/node_modules/tailwindcss/lib/cli.js'), '-i', path.join(root, 'ui/upstream.css'), '-c', path.join(root, 'ui/upstream.tailwind.config.js'), '--content', [path.join(root, 'helper/static/native.js'), path.join(__dirname, 'index.html'), path.join(__dirname, 'fixture.js')].join(','), '-o', css, '--minify'], { encoding: 'utf8' });
  assert.equal(compile.status, 0, compile.stderr);
  const calls = [], schedules = new Map();
  let emailRateLimit = false, statusFailure = false, enhancementEnabled = true;
  const observed = new Map([[1, false], [2, true], [33, false]]);
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      const send = (status, body, headers = {}) => { response.writeHead(status, { 'Content-Type': 'application/json', ...headers }); response.end(JSON.stringify(body)); };
      if (url.pathname.startsWith('/_helper/api/')) {
        let raw = ''; for await (const chunk of request) raw += chunk;
        const body = raw ? JSON.parse(raw) : undefined;
        calls.push({ path: url.pathname, method: request.method, body, authorization: request.headers.authorization, ui: request.headers['x-admin-ui-request'] });
        assert.equal(request.headers['x-admin-ui-request'], '1');
        if (url.pathname === '/_helper/api/session/revoke') return send(200, { ok: true });
        if (!['Bearer fixture-admin-jwt', 'Bearer fixture-admin-two'].includes(request.headers.authorization)) return send(403, { detail: '需要管理员身份' });
        if (url.pathname.endsWith('/status')) { if (statusFailure) { statusFailure = false; return send(503, { detail: '模拟暂时不可用' }); } return send(200, { timezone: 'America/New_York', mail_configured: true }); }
        if (url.pathname.endsWith('/session')) return send(200, { session: 'fixture-opaque-helper-session' });
        const sharedRule = { id: 10, name: '夜间共享规则', start: '23:00', end: '09:00', timezone: 'America/New_York', weekdays: [0, 1, 2, 3, 4, 5, 6], enabled: true, account_count: 1, in_window_now: true };
        let match = url.pathname.match(/\/accounts\/(\d+)\/schedule$/);
        if (match) {
          const id = Number(match[1]);
          const existing = schedules.get(id);
          if (request.method === 'PUT') {
            if (id === 2) return send(409, { detail: '其他计划正在管理' });
            if (body?.mode === 'all_day' && body?.remove_from_schedule_id === 10) {
              schedules.delete(id); observed.set(id, true);
            } else {
              schedules.set(id, body); observed.set(id, true);
            }
          }
          const value = schedules.get(id) || existing;
          const inShared = value?.mode === 'rule' && value?.schedule_id === 10;
          return send(200, {
            account_id: id === 99 ? 100 : id,
            account_name: id === 33 ? '循环复用后的账号' : '示例账号 ' + id,
            schedulable: observed.get(id) ?? false,
            managed: !!value && (value.mode === 'window' || inShared),
            read_only: id === 2 || inShared,
            conflict: id === 2 ? '该账号已由其他定时计划管理' : (inShared ? '该账号已由时段规则「夜间共享规则」管理' : null),
            ...(inShared ? { shared_schedule: { id: 10, name: '夜间共享规则', start: '23:00', end: '09:00', timezone: 'America/New_York', weekdays: [0, 1, 2, 3, 4, 5, 6], other_account_count: 1 } } : {}),
            available_schedules: [sharedRule],
            ...(value?.name ? { name: value.name } : {}),
            mode: value?.mode || 'all_day',
            start: value?.start || '23:00',
            end: value?.end || '09:00',
            timezone: value?.timezone || 'America/New_York',
            weekdays: value?.weekdays || [0, 1, 2, 3, 4, 5, 6]
          });
        }
        match = url.pathname.match(/\/users\/(\d+)(\/email)?$/);
        if (match) {
          const id = Number(match[1]);
          if (match[2]) { if (emailRateLimit) { emailRateLimit = false; return send(429, { detail: 'rate limit' }, { 'Retry-After': '60' }); } return send(200, { ok: true }); }
          return send(200, {
            id: id === 9 ? 999 : id,
            username: '<img src=x onerror=alert(1)>',
            email: `fresh${id}@example.invalid`,
            status: 'active',
            role: 'user',
            last_used_at: '2026-10-01T12:00:00Z',
            templates: [
              { id: 'template:5', name: '续费提醒模板', subject: '续费提醒 · {username}', body: '您好 {username}（{email}），上次活跃：{last_used_at}', builtin: false },
              { id: 'builtin:inactive', name: '长期未使用提醒', subject: '账户长期未使用提醒', body: '您好 {username}，您的账户近期未使用，请及时登录查看。', builtin: true },
              { id: 'builtin:maintenance', name: '服务维护通知', subject: '服务维护通知', body: '您好 {username}，服务将进行维护，请留意管理员后续通知。', builtin: true },
            ]
          });
        }
        return send(404, { detail: 'Unknown fixture API' });
      }
      if (url.pathname === '/_helper/console') {
        response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><html><body><p id="session"></p><script src="/advanced.js"></script></body></html>'); return;
      }
      if (url.pathname === '/advanced.js') { response.writeHead(200, { 'Content-Type': 'text/javascript' }); response.end("document.getElementById('session').textContent=sessionStorage.getItem('helper_native_console_session') === 'fixture-opaque-helper-session' ? '高级功能会话已验证' : 'missing session';"); return; }
      const files = { '/native.js': path.join(root, 'helper/static/native.js'), '/native.css': path.join(root, 'helper/static/native.css'), '/fixture.js': path.join(__dirname, 'fixture.js'), '/fixture.css': path.join(__dirname, 'fixture.css'), '/fixture-native.css': css };
      const file = files[url.pathname] || path.join(__dirname, 'index.html');
      const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      let body = await fs.readFile(file);
      if (type === 'text/html' && !enhancementEnabled) {
        body = Buffer.from(body.toString('utf8').replace('<link rel="stylesheet" href="/native.css">', '').replace('<script src="/native.js" defer></script>', ''));
      }
      response.writeHead(200, { 'Content-Type': type, 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'" }); response.end(body);
    } catch (error) { response.writeHead(500, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ detail: error.message })); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
    await page.addInitScript(() => {
      window.fixtureOriginalFetch = window.fetch;
      window.fixtureOriginalXHROpen = XMLHttpRequest.prototype.open;
      window.fixtureOriginalXHRSend = XMLHttpRequest.prototype.send;
    });
    const errors = [], browserDialogs = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', item => { if (item.type() === 'error' && !/Failed to load resource/.test(item.text())) errors.push(item.text()); });
    page.on('dialog', item => { browserDialogs.push(item.message()); item.dismiss(); });
    const actions = kind => page.locator(`[data-helper-native-action="${kind}"]`);
    const dialog = page.locator('.helper-native-dialog');
    const close = async () => { await dialog.getByRole('button', { name: '关闭', exact: true }).first().click(); await dialog.waitFor({ state: 'detached' }); };
    await page.goto(origin + '/admin/accounts');
    await actions('accounts').first().waitFor();
    assert.equal(await page.evaluate(() => window.fetch === window.fixtureOriginalFetch && XMLHttpRequest.prototype.open === window.fixtureOriginalXHROpen && XMLHttpRequest.prototype.send === window.fixtureOriginalXHRSend), true, 'enhancement must not intercept business fetch/XHR');
    assert.equal(await actions('accounts').count(), 2);
    assert.equal(calls.filter(c => c.method === 'PUT').length, 0);
    await page.locator('tr[data-row-id="1"]').getByRole('button', { name: '编辑', exact: true }).click();
    assert.equal(await page.evaluate(() => fixture.nativeClicks), 1, 'existing native action unchanged');
    await page.locator('tr[data-row-id="1"] [data-helper-native-action]').click();
    await dialog.getByText('全天（未接管）', { exact: true }).waitFor();
    assert.equal(await dialog.locator('[name="mode"][value="all_day"]').isChecked(), true);
    await dialog.getByText(/当前参与调度：关闭/).waitFor();
    assert.equal(calls.filter(c => c.method === 'PUT').length, 0, 'default/opening performs no writes');
    await page.screenshot({ path: path.join(directory, 'accounts-default-all-day.png'), fullPage: true });
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
    await dialog.getByText('设置已保存。原生列表的调度状态将在下次刷新时同步。', { exact: true }).waitFor();
    assert.deepEqual(calls.find(c => c.method === 'PUT').body, { mode: 'all_day' }); await close();
    await page.locator('tr[data-row-id="1"] [data-helper-native-action]').click();
    await dialog.locator('[name="mode"][value="window"]').check();
    assert.equal(await dialog.locator('.modal-footer button[type="submit"]').count(), 1, 'primary submit button lives in .modal-footer');
    assert.equal(await dialog.locator('.modal-body button[type="submit"]').count(), 0, 'no duplicate submit button inside .modal-body');
    assert.equal(await dialog.locator('details.helper-native-more').evaluate(node => node.open), false, 'more settings collapsed by default');
    assert.equal(await dialog.locator('[data-helper-schedule-summary]').textContent(), '示例账号 1 每天 23:00 开启、次日 09:00 关闭（America/New_York）');
    assert.equal(await dialog.locator('[name="start"]').inputValue(), '23:00');
    assert.equal(await dialog.locator('[name="end"]').inputValue(), '09:00');
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click(); await dialog.getByText(/设置已保存/).waitFor(); await close();
    assert.equal(schedules.get(1).mode, 'window'); assert.equal(schedules.get(1).weekdays.length, 7); assert.equal(schedules.get(1).timezone, 'America/New_York', 'new native schedule uses configured default timezone');
    schedules.set(1, { mode: 'window', name: '自定义夜间计划', start: '23:00', end: '09:00', timezone: 'Europe/London', weekdays: [1, 3, 5] });
    await page.locator('tr[data-row-id="1"] [data-helper-native-action]').click();
    await dialog.locator('[data-helper-schedule-summary]').waitFor();
    assert.equal(await dialog.locator('details.helper-native-more').evaluate(node => node.open), false, 'more settings stays collapsed when editing legacy schedule');
    assert.equal(await dialog.locator('[data-helper-schedule-summary]').textContent(), '示例账号 1 周二、周四、周六 23:00 开启、次日 09:00 关闭（Europe/London）');
    await dialog.locator('[name="end"]').fill('08:30');
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click(); await dialog.getByText(/设置已保存/).waitFor(); await close();
    assert.deepEqual(schedules.get(1), { mode: 'window', name: '自定义夜间计划', start: '23:00', end: '08:30', timezone: 'Europe/London', weekdays: [1, 3, 5] }, 'collapsed fields preserve saved name, weekdays, and third timezone');
    await page.evaluate(() => { document.body.style.zoom = '2'; });
    const rowActionLayout = await page.locator('tr[data-row-id="1"]').evaluate(row => {
      const nativeBtn = row.querySelector('td:last-child button:not([data-helper-native-action])');
      const helperBtn = row.querySelector('td:last-child [data-helper-native-action]');
      const label = helperBtn.querySelector('span');
      const style = getComputedStyle(label);
      const nb = nativeBtn.getBoundingClientRect(), hb = helperBtn.getBoundingClientRect();
      return { overlap: nb.right > hb.left + 1 && nb.left < hb.right - 1 && nb.bottom > hb.top + 1 && nb.top < hb.bottom - 1, writingMode: style.writingMode, whiteSpace: style.whiteSpace };
    });
    assert.equal(rowActionLayout.overlap, false, 'injected button must not overlap native action button at 200% zoom');
    assert.equal(rowActionLayout.writingMode, 'horizontal-tb');
    assert.equal(rowActionLayout.whiteSpace, 'nowrap');
    await page.evaluate(() => { document.body.style.zoom = ''; });
    await page.locator('tr[data-row-id="2"] [data-helper-native-action]').click();
    await dialog.getByText('该账号已由其他定时计划管理', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存设置', exact: true }).isDisabled(), true); await close();
    const countBefore = calls.length;
    await page.evaluate(() => fixture.recycle(33));
    await page.locator('tr[data-row-id="33"] [data-helper-native-action]').click();
    await dialog.getByRole('heading', { name: '账号定时 · 循环复用后的账号' }).waitFor();
    assert.equal(calls[countBefore].path, '/_helper/api/accounts/33/schedule', 'reused row reads current ID at click');
    await dialog.locator('[name="mode"][value="rule"]').check();
    await dialog.locator('[data-helper-rule-preview]').waitFor();
    assert((await dialog.locator('[data-helper-rule-preview]').textContent()).includes('夜间共享规则'));
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
    await dialog.getByText(/设置已保存/).waitFor();
    assert.deepEqual(schedules.get(33), { mode: 'rule', schedule_id: 10 }, 'applying existing rule sends mode=rule and schedule_id');
    await close();
    await page.locator('tr[data-row-id="33"] [data-helper-native-action]').click();
    await dialog.locator('[data-helper-shared-schedule="10"]').waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存设置', exact: true }).isDisabled(), true, 'shared-rule account disables direct overwrite');
    await dialog.getByRole('button', { name: '仅将此账号移出规则并改为全天', exact: true }).click();
    await dialog.getByText(/已将 循环复用后的账号 从「夜间共享规则」移出并设为全天运行/).waitFor();
    assert.equal(schedules.has(33), false, 'explicit remove_from_schedule_id detaches only this account');
    await close();
    await page.evaluate(() => { for (let n = 0; n < 30; n++) { const span = document.createElement('span'); document.body.append(span); span.remove(); } });
    await page.waitForTimeout(100); assert.equal(await actions('accounts').count(), 2, 'observer dedupes buttons');
    const statuses = calls.filter(c => c.path.endsWith('/status')).length;
    await page.waitForTimeout(150); assert.equal(calls.filter(c => c.path.endsWith('/status')).length, statuses, 'no status polling');
    await page.evaluate(() => fixture.rows([99])); await actions('accounts').first().click();
    await dialog.getByText('账号身份或状态响应不完整，已停止操作。', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存设置' }).count(), 0); await close();
    await page.getByRole('button', { name: '原生用户', exact: true }).click(); await actions('users').first().waitFor();
    await page.locator('tr[data-row-id="7"] [data-helper-native-action]').click();
    await dialog.getByText('fresh7@example.invalid', { exact: true }).waitFor();
    assert.equal(await dialog.locator('img').count(), 0, 'untrusted API labels are text');
    await dialog.locator('[name="template"]').selectOption('template:5');
    assert((await dialog.locator('[data-helper-email-preview]').textContent()).includes('fresh7@example.invalid'), 'custom template renders placeholders into live preview');
    assert.equal(await dialog.locator('img').count(), 0, 'template preview keeps untrusted username text-only');
    await dialog.locator('[name="template"]').selectOption('builtin:maintenance');
    await dialog.getByRole('button', { name: '发送邮件', exact: true }).click(); await dialog.getByText('邮件已发送给用户 #7。', { exact: true }).waitFor();
    const sent = calls.filter(c => c.path.endsWith('/email')); assert.equal(sent.length, 1); assert.equal(sent[0].path, '/_helper/api/users/7/email'); assert.deepEqual(Object.keys(sent[0].body).sort(), ['body', 'subject']); await close();
    emailRateLimit = true;
    await page.locator('tr[data-row-id="8"] [data-helper-native-action]').click(); await dialog.getByText('fresh8@example.invalid', { exact: true }).waitFor();
    await dialog.locator('[name="template"]').selectOption('builtin:inactive'); await dialog.getByRole('button', { name: '发送邮件', exact: true }).click();
    await dialog.getByText('操作过于频繁，请 60 秒后重试。', { exact: true }).waitFor(); assert.equal(calls.filter(c => c.path.endsWith('/email')).length, 2); await close();
    await page.evaluate(() => sessionStorage.setItem('helper_setup_session', 'keep-standalone-setup'));
    await page.locator('tr[data-row-id="7"] [data-helper-native-action]').click(); await dialog.getByText('fresh7@example.invalid', { exact: true }).waitFor();
    await dialog.locator('summary').click(); await dialog.locator('[aria-label="高级功能"]').selectOption('rules'); await dialog.getByRole('button', { name: '打开', exact: true }).click();
    await page.frameLocator('.helper-native-console').getByText('高级功能会话已验证', { exact: true }).waitFor();
    const frameSource = await page.locator('.helper-native-console').getAttribute('src'); assert(!frameSource.includes('jwt')); assert(frameSource.endsWith('#rules'));
    assert.equal(calls.filter(c => c.path === '/_helper/api/session/revoke').length, 0, 'initial login and unchanged identity must not revoke sessions');
    const revokeFirst = page.waitForResponse(value => new URL(value.url()).pathname === '/_helper/api/session/revoke' && value.request().headers().authorization === 'Bearer fixture-admin-jwt');
    await page.evaluate(() => fixture.identity('fixture-admin-two'));
    assert.equal((await revokeFirst).status(), 200);
    const revoked = calls.find(c => c.path === '/_helper/api/session/revoke');
    assert.equal(revoked.method, 'POST'); assert.equal(revoked.body, undefined); assert.equal(revoked.ui, '1');
    await dialog.waitFor({ state: 'detached' });
    assert.equal(await page.evaluate(() => sessionStorage.getItem('helper_native_console_session')), null, 'identity change clears advanced console session');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('helper_setup_session')), 'keep-standalone-setup', 'native identity change never wipes standalone setup session');
    await actions('users').first().waitFor(); await actions('users').first().click(); await dialog.getByText('fresh7@example.invalid', { exact: true }).waitFor();
    assert.equal(calls.at(-1).authorization, 'Bearer fixture-admin-two', 'per-request current native JWT'); await close();
    const revokeSecond = page.waitForResponse(value => new URL(value.url()).pathname === '/_helper/api/session/revoke' && value.request().headers().authorization === 'Bearer fixture-admin-two');
    await page.evaluate(() => fixture.identity('fixture-user-jwt'));
    assert.equal((await revokeSecond).status(), 200);
    await page.getByRole('button', { name: '重试连接助手' }).waitFor(); assert.equal(await actions('users').count(), 0, 'forged auth_user role cannot grant actions');
    assert.equal(await page.locator('[data-helper-native-unavailable]').count(), 1);
    await page.evaluate(() => fixture.identity('fixture-admin-jwt')); await actions('users').first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(100); assert.equal(await actions('users').count(), 2);
    await actions('users').first().click(); await dialog.getByText('fresh7@example.invalid', { exact: true }).waitFor();
    const mobileBox = await dialog.boundingBox(); assert(mobileBox.width <= 358 && mobileBox.height <= 760 && mobileBox.y >= 0);
    await page.screenshot({ path: path.join(directory, 'mobile-mail.png'), fullPage: true }); await close();
    await page.evaluate(() => fixture.mobileID(false));
    await page.getByText('在列设置中显示 ID 后，可使用邮件或定时按钮。', { exact: true }).waitFor();
    assert.equal(await actions('users').count(), 0, 'hidden mobile ID never guessed from name/order');
    assert.equal(await page.locator('[data-helper-native-mobile-hint]').count(), 1, 'one compact mobile hint');
    await page.evaluate(() => fixture.mobileID(true)); await actions('users').first().waitFor();
    assert.equal(await page.locator('[data-helper-native-mobile-hint]').count(), 0, 'hint removed once real ID is visible');
    await page.evaluate(() => fixture.rows(['not-an-id']));
    await page.getByText('当前 ID 格式不兼容，助手未添加行操作。', { exact: true }).waitFor();
    assert.equal(await actions('users').count(), 0, 'malformed ID fails closed');
    await page.evaluate(() => history.pushState({}, '', '/profile')); await page.waitForTimeout(50);
    assert.equal(await page.locator('[data-helper-native-mobile-hint]').count(), 0, 'route exit removes mobile hint');
    await page.evaluate(() => fixture.route('users')); await actions('users').first().waitFor();
    await page.setViewportSize({ width: 1440, height: 1100 });
    statusFailure = true; await page.reload(); await page.getByRole('button', { name: '重试连接助手' }).waitFor();
    await page.getByRole('button', { name: '重试连接助手' }).click(); await actions('users').first().waitFor();
    await page.getByRole('button', { name: '深色', exact: true }).click(); await actions('users').first().click(); await dialog.getByText('fresh7@example.invalid', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'desktop-mail-dark.png'), fullPage: true });
    await dialog.locator('summary').click(); await dialog.getByRole('button', { name: '打开', exact: true }).click(); await page.locator('.helper-native-console').waitFor();
    await page.evaluate(() => history.pushState({}, '', '/login')); await dialog.waitFor({ state: 'detached' }); assert.equal(await page.evaluate(() => sessionStorage.getItem('helper_native_console_session')), null); assert.equal(await page.evaluate(() => sessionStorage.getItem('helper_setup_session')), 'keep-standalone-setup', 'route exit never wipes standalone setup session');
    enhancementEnabled = false;
    await page.goto(origin + '/admin/accounts');
    await page.locator('tr[data-row-id="1"]').waitFor();
    assert.equal(await page.evaluate(() => window.__sub2apiHelperNative), undefined, 'disabling enhancement and refreshing removes injected script');
    assert.equal(await page.locator('[data-helper-native-action]').count(), 0, 'disabling enhancement removes injected action buttons');
    await page.locator('tr[data-row-id="1"]').getByRole('button', { name: '编辑', exact: true }).click();
    assert.equal(await page.evaluate(() => fixture.nativeClicks), 1, 'native table actions continue working after rollback');
    assert.equal(schedules.get(1).name, '自定义夜间计划', 'saved backend schedule remains active after UI rollback');
    assert.deepEqual(browserDialogs, [], 'native flows use explicit modal buttons, no browser confirm');
    assert.equal(await page.evaluate(() => window.fetch === window.fixtureOriginalFetch && XMLHttpRequest.prototype.open === window.fixtureOriginalXHROpen && XMLHttpRequest.prototype.send === window.fixtureOriginalXHRSend), true, 'navigation/logout must keep native fetch/XHR unchanged');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, screenshots: directory, apiCalls: calls.length, checks: ['unmanaged read makes zero writes', 'explicit all-day/window saves', 'foreign plan read-only', 'virtual row current-ID lookup', 'observer dedupe/no polling', 'SPA route changes', 'fresh selected user only', 'untrusted labels text-only', '429 no retry', 'opaque advanced session/no JWT URL', 'identity/logout clears iframe+session', 'explicit best-effort session revoke', 'no business fetch/XHR interception', 'regular user denied', 'mobile visible ID only', 'transient failure explicit retry', 'dark/mobile modal bounds', 'no browser confirm'] }, null, 2));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

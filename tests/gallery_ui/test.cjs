/* Offline browser regression for the real read-only gallery and portal. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');
(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-gallery-ui-'));
  const cssInput = path.join(directory, 'input.css'), cssFile = path.join(directory, 'native.css');
  await fs.writeFile(cssInput, await fs.readFile(path.join(root, 'ui/upstream.css'), 'utf8') + '\n' + await fs.readFile(path.join(root, 'ui/adapters.css'), 'utf8'));
  const compiled = spawnSync(process.execPath, [path.join(root, 'ui/node_modules/tailwindcss/lib/cli.js'), '-i', cssInput, '-c', path.join(root, 'ui/tailwind.config.js'), '-o', cssFile, '--minify'], { encoding: 'utf8' });
  assert.equal(compiled.status, 0, compiled.stderr);
  let empty = false, fail = false, onlyFirst = false, comparisonUnavailable = false, leaks = 0;
  const calls = [];
  const groups = [{ group_id: 1, name: '白鹭单车分组', model: 'fixture-html', enabled: true, interval_minutes: 60 }, { group_id: 2, name: '月夜纸船分组', model: 'fixture-svg', enabled: false, interval_minutes: 90, comparison_member: true, comparison_enabled: true }, { group_id: 3, name: '失败示例分组', model: 'fixture-failed', enabled: true, interval_minutes: 90 }];
  const sharedPrompt = '请用 HTML 或 SVG 生成一幅自然主题作品。\n<img src=x onerror=alert(1)> 仅为同轮提示词文本';
  const html = '<style>@keyframes fly{from{transform:translateX(0)}to{transform:translateX(40px)}}body{background:#ecfeff}.scene{height:100vh;display:grid;place-items:center}#bird{width:70px;height:40px;background:#14b8a6;border-radius:50%;animation:fly .3s linear infinite alternate}</style><main class="scene"><div id="bird"></div></main><script>parent.PWNED=1;fetch("/leak")</script><img src="/leak" onerror="parent.PWNED=2">';
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240"><rect width="320" height="240" fill="#0f172a"/><circle id="moon" cx="230" cy="60" r="30" fill="#fef3c7"/><path id="boat" d="M60 155L155 120L225 155Z" fill="#14b8a6"/><text x="25" y="210" fill="white">月夜纸船</text></svg>';
  function work(group, n) { return { id: `${group === 1 ? 'html' : 'svg'}-${n}`, title: group === 1 ? `白鹭单车 ${n + 1}` : `月夜纸船 ${n + 1}`, model: groups[group - 1].model, status: 'success', created_at: new Date(Date.now() - n * 60000).toISOString(), latency_ms: 1200 + n, kind: 'html', content: group === 1 ? n === 0 ? html : '<style>body{background:#ccfbf1}</style><h2>静态作品</h2>' : svg }; }
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/leak') { leaks++; res.writeHead(200); res.end('not allowed'); return; }
    if (url.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    if (url.pathname.startsWith('/_helper/api/portal/')) {
      calls.push(url.pathname);
      let value;
      if (url.pathname.endsWith('/session')) value = { session: 'fixture-session' };
      else if (url.pathname.endsWith('/modules')) value = { benchmark: true };
      else if (url.pathname === '/_helper/api/portal/gallery') { if (fail) { res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: '示例服务暂时不可用' })); return; } value = { groups: empty ? [] : onlyFirst ? groups.slice(0, 1) : groups, timezone: 'Asia/Shanghai' }; }
      else if (url.pathname === '/_helper/api/portal/gallery/comparison') {
        if (comparisonUnavailable) { res.writeHead(404); res.end(); return; }
        const available = empty ? [] : onlyFirst ? groups.slice(0, 1) : groups;
        value = { timezone: 'Asia/Shanghai', config: available.length ? { enabled: true, interval_minutes: 60, group_ids: available.map(group => group.group_id), next_at: new Date(Date.now() + 3600000).toISOString() } : null, round: available.length ? { id: 'shared-round-1', prompt: sharedPrompt, status: 'partial', created_at: new Date(Date.now()).toISOString(), items: available.map(group => { const item = { group_id: group.group_id, name: group.name, model: group.model, status: group.group_id === 3 ? 'failed' : 'success' }; if (group.group_id !== 3) { item.work = work(group.group_id, 0); item.work.preview = item.work.content; delete item.work.content; } return item; }) } : null };
      }
      else {
        const match = url.pathname.match(/\/gallery\/(\d+)(?:\/works\/([\w-]+))?$/), group = Number(match?.[1]);
        if (!match || !groups[group - 1]) { res.writeHead(404); res.end(); return; }
        if (match[2]) { const n = Number(match[2].split('-').at(-1)); value = { ...work(group, n), prompt: sharedPrompt }; }
        else value = { group: { ...groups[group - 1], next_at: new Date(Date.now() + 3600000).toISOString(), comparison_next_at: group === 2 ? new Date(Date.now() + 7200000).toISOString() : null }, stats: { success_24h: 8, failed_24h: 2, total: 31 }, works: Array.from({ length: group === 1 ? 13 : 4 }, (_, n) => { const item = work(group, n); item.preview = item.content; delete item.content; return item; }) };
      }
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); return;
    }
    const file = url.pathname === '/_helper/static/style.css' ? cssFile : path.join(root, url.pathname.startsWith('/_helper/static/') ? 'helper/static/' + path.basename(url.pathname) : 'helper/static/feature.html');
    res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; connect-src 'self'; object-src 'none'" }); res.end(await fs.readFile(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' }), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', item => { if (item.type() === 'error' && !/Blocked script execution|Content Security Policy|Failed to load resource|Refused to|violates the following/.test(item.text())) errors.push(item.text()); });
    await page.clock.install();
    await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt&theme=light');
    await page.waitForFunction(() => document.querySelector('[role=tab][aria-selected=true]')?.textContent === '同题对比');
    assert.equal(await page.locator('.helper-gallery-comparison-item').count(), 3, 'same round compares all accessible groups');
    assert((await page.locator('.helper-gallery-info').textContent()).includes('部分完成'));
    assert((await page.locator('.helper-gallery-comparison-item[data-group-id="3"]').textContent()).includes('失败'));
    assert((await page.locator('.helper-gallery-comparison-item[data-group-id="3"]').textContent()).includes('fixture-failed'));
    await page.getByText('本轮共用提示词', { exact: true }).click(); assert.equal(await page.locator('.helper-gallery-shared-prompt pre').textContent(), sharedPrompt); assert.equal(await page.locator('.helper-gallery-shared-prompt img').count(), 0);
    assert.equal(calls.filter(value => value === '/_helper/api/portal/gallery/comparison').length, 1); assert.equal(calls.filter(value => /\/gallery\/\d+$/.test(value)).length, 0, 'comparison uses one shared round, no group prefetch');
    await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).click(); await page.getByText('生成提示词', { exact: true }).waitFor(); assert.equal(await page.locator('.helper-gallery-dialog .helper-gallery-prompt').textContent(), sharedPrompt); await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.screenshot({ path: path.join(directory, 'gallery-comparison.png'), fullPage: true });
    await page.getByRole('tab', { name: '白鹭单车分组', exact: true }).click();
    await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor();
    assert.equal(await page.locator('.helper-gallery-work').count(), 12, 'latest twelve only');
    assert.equal(await page.locator('header,aside,.brand,.benchmark-form').count(), 0);
    await page.getByText('24 小时失败', { exact: true }).waitFor(); assert.equal(new URL(page.url()).search, '');
    assert.equal(calls.filter(value => value.includes('/works/')).length, 1, 'no extra detail requests on group render');
    const artwork = page.locator('.helper-gallery-work iframe').first();
    assert.equal(await artwork.getAttribute('sandbox'), ''); assert.equal(await artwork.getAttribute('inert'), ''); assert.equal(await artwork.getAttribute('allow'), null);
    const frame = await artwork.elementHandle().then(handle => handle.contentFrame()); await frame.locator('#bird').waitFor({ state: 'attached' });
    assert.equal(await frame.locator('body').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(236, 254, 255)', 'inline model CSS renders under inherited CSP');
    const transforms = new Set(); for (let n = 0; n < 4; n++) { transforms.add(await frame.locator('#bird').evaluate(node => getComputedStyle(node).transform)); await page.waitForTimeout(75); } assert(transforms.size > 1, 'CSS animation remains active');
    assert.equal(await page.evaluate(() => window.PWNED), undefined); assert.equal(leaks, 0, 'model scripts/images cannot contact parent or network');
    await page.screenshot({ path: path.join(directory, 'gallery-html.png'), fullPage: true });
    await page.getByRole('tab', { name: '月夜纸船分组', exact: true }).click(); await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).waitFor();
    assert((await page.locator('.helper-gallery-info').textContent()).includes('同题任务')); assert.notEqual(await page.locator('.helper-gallery-stat:last-child strong').textContent(), '已暂停', 'group uses comparison schedule rather than disabled individual schedule');
    const moonFrame = await page.locator('.helper-gallery-work iframe').first().elementHandle().then(handle => handle.contentFrame()); await moonFrame.locator('#moon').waitFor({ state: 'attached' }); assert.equal(await moonFrame.locator('#moon').evaluate(node => getComputedStyle(node).fill), 'rgb(254, 243, 199)');
    await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).click(); await page.getByRole('dialog', { name: '作品详情', exact: true }).waitFor(); await page.getByText('生成提示词', { exact: true }).waitFor(); assert((await page.locator('.helper-gallery-prompt').textContent()).includes('<img')); assert.equal(await page.locator('.helper-gallery-prompt img').count(), 0); assert.equal(calls.filter(value => value.endsWith('/works/svg-0')).length, 2);
    const box = await page.locator('.helper-gallery-dialog').boundingBox(); assert(box.width <= 1152 && box.height <= 1012 && box.y >= 0); await page.screenshot({ path: path.join(directory, 'gallery-detail.png'), fullPage: true }); await page.getByRole('button', { name: '关闭', exact: true }).click();
    const beforePoll = calls.filter(value => value === '/_helper/api/portal/gallery/2').length; await page.clock.fastForward(30000); await page.waitForFunction(() => document.querySelector('[role=tab][aria-selected=true]')?.textContent === '月夜纸船分组'); await page.waitForTimeout(100); assert(calls.filter(value => value === '/_helper/api/portal/gallery/2').length > beforePoll, 'modest poll retains selected group');
    await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.classList.add('dark')); await page.waitForTimeout(350); await page.screenshot({ path: path.join(directory, 'gallery-mobile-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).click(); await page.getByText('生成提示词', { exact: true }).waitFor(); onlyFirst = true; await page.clock.fastForward(30000); await page.getByRole('tab', { name: '白鹭单车分组', exact: true }).waitFor(); await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor(); assert.equal(await page.locator('dialog').count(), 0, 'revoked group closes stale detail');
    await page.evaluate(() => document.querySelector('.helper-gallery').parentElement.remove()); const afterDetach = calls.length; await page.clock.fastForward(60000); await page.waitForTimeout(80); assert.equal(calls.length, afterDetach, 'detached view stops timers/requests');
    empty = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt'); await page.getByText('暂无可用分组。', { exact: true }).waitFor();
    empty = false; fail = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt'); await page.getByText('示例服务暂时不可用', { exact: true }).waitFor(); fail = false; await page.getByRole('button', { name: '刷新', exact: true }).click(); await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor();
    comparisonUnavailable = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt'); await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor(); assert.equal(await page.getByRole('tab', { name: '同题对比', exact: true }).count(), 0, 'older servers gracefully keep group gallery');
    assert.equal(leaks, 0); assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0); assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, screenshots: directory, checks: ['same shared round defaults to comparison', 'group model/status including failure', 'shared prompt plaintext and real detail', 'comparison endpoint gracefully optional', 'HTML CSS renders/animates in sandbox', 'raw SVG visible', 'scripts/network blocked', 'latest12/no detail prefetch', 'group tab/poll retention', 'detached cleanup', 'empty/error/retry', 'mobile/dark/no key storage'] }, null, 2));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

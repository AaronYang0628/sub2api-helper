/* Offline browser regression for the real read-only gallery and portal. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');

async function startRealFeatureBackend(directory, parentOrigin) {
  const script = path.join(directory, 'real-feature-backend.py');
  await fs.writeFile(script, String.raw`
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import socket
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(sys.argv[1]) / 'tests'))
import uvicorn
import helper.app as app_module
from helper.config import Config
from helper.store import Store
from helper.upstream import UpstreamError
from test_app import FakeMailer, FakeUpstream

class FixtureUpstream(FakeUpstream):
    async def profile(self, token):
        actors = {'real-feature-user-jwt': 7, 'real-feature-empty-jwt': 7, 'real-feature-admin-jwt': 99}
        if token not in actors:
            raise UpstreamError('Fixture browser login is invalid', 401)
        return await self.get_user(actors[token])

    async def available_groups(self, token):
        return {3} if token == 'real-feature-user-jwt' else set()

    async def benchmark_key(self, key_id, group_id):
        return 'fixture-model-key-never-rendered'

async def generate(config, selection):
    return {'kind': 'html', 'content': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><circle id="real-fixture-art" cx="60" cy="40" r="30" fill="#14b8a6"/></svg>', 'mime': 'text/html'}

app_module.generate_artwork = generate
store = Store(str(Path(__file__).with_name('real-feature.db')))
config = Config(sub2api_url='http://127.0.0.1:9', sub2api_public_url=sys.argv[2],
                sub2api_admin_key='fixture-bound-admin-key', admin_token='fixture-helper-admin-token',
                native_ui=False, db_path=str(Path(__file__).with_name('real-feature.db')))
app = app_module.create_app(config, store=store, upstream=FixtureUpstream(), mailer=FakeMailer(), background=False)
store.set_setting('modules', {'benchmark': True, 'pricing': True, 'ranking': True})
for group_id, name, model, effort in [(3, '实际菜单可见分组', 'fixture-loopback-model', 'high'), (4, '禁止泄露分组', 'restricted-model', 'none')]:
    app.state.gallery.configure(group_id, {'name': name, 'model': model, 'key_id': group_id,
                                         'api_format': 'chat', 'prompt': 'Fixture immutable executed prompt',
                                         'enabled': False, 'interval_minutes': 30, 'reasoning_effort': effort})
original_lifespan = app.router.lifespan_context

@asynccontextmanager
async def ready_lifespan(application):
    async with original_lifespan(application):
        for group_id in (3, 4):
            await application.state.gallery.start(group_id)
            await asyncio.gather(*list(application.state.gallery._tasks.values()))
        yield

app.router.lifespan_context = ready_lifespan
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('127.0.0.1', 0))
sock.listen(128)
print('PORT=' + str(sock.getsockname()[1]), flush=True)
uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', log_level='error', access_log=False, proxy_headers=False)).run(sockets=[sock])
`);
  let stderr = '';
  const child = spawn(process.env.HELPER_PYTHON_PATH || 'python3', [script, root, parentOrigin], { stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', bytes => { stderr += bytes.toString(); });
  const port = await new Promise((resolve, reject) => {
    let stdout = '';
    const timeout = setTimeout(() => reject(new Error('Real feature fixture did not announce its port: ' + stderr)), 10000);
    child.stdout.on('data', bytes => { stdout += bytes.toString(); const match = stdout.match(/PORT=(\d+)/); if (match) { clearTimeout(timeout); resolve(Number(match[1])); } });
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Real feature fixture exited ${code}: ${stderr}`)); });
  });
  const origin = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try { const result = await fetch(origin + '/_helper/healthz'); if (result.ok) return { child, origin }; } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  child.kill('SIGTERM');
  throw new Error('Real feature fixture failed startup: ' + stderr);
}

(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-gallery-ui-'));
  const cssInput = path.join(directory, 'input.css'), cssFile = path.join(directory, 'native.css');
  await fs.writeFile(cssInput, await fs.readFile(path.join(root, 'ui/upstream.css'), 'utf8') + '\n' + await fs.readFile(path.join(root, 'ui/adapters.css'), 'utf8'));
  const compiled = spawnSync(process.execPath, [path.join(root, 'ui/node_modules/tailwindcss/lib/cli.js'), '-i', cssInput, '-c', path.join(root, 'ui/tailwind.config.js'), '-o', cssFile, '--minify'], { encoding: 'utf8' });
  assert.equal(compiled.status, 0, compiled.stderr);
  let empty = false, noWorks = false, fail = false, onlyFirst = false, leaks = 0;
  const calls = [];
  let realBackendOrigin = '', liveIngress = false, realBackend;
  const fixtureCreatedAt = Date.now();
  const groups = [{ group_id: 1, name: '白鹭单车分组', model: 'fixture-html', reasoning_effort: 'high' }, { group_id: 2, name: '月夜纸船分组', model: 'fixture-svg', reasoning_effort: 'low' }, { group_id: 3, name: '失败示例分组', model: 'fixture-failed', reasoning_effort: 'default' }, { group_id: 4, name: '等待参与分组', model: 'fixture-queued', reasoning_effort: 'medium' }];
  const sharedPrompt = '请用 HTML 或 SVG 生成一幅自然主题作品。\n<img src=x onerror=alert(1)> 仅为同轮提示词文本';
  const validInlineImage = 'data:image/gif;base64,' + Buffer.from('47494638396101000100800000000000ffffff21ff0b4e45545343415045322e30030100000021f904000a0000002c000000000100010000020244010021f904000a0000002c00000000010001000002024c01003b', 'hex').toString('base64');
  // This carries a PNG signature but is not decodable, exercising the actual
  // browser decoder rather than just a MIME/base64 check for historical HTML.
  const corruptInlineImage = 'data:image/png;base64,' + Buffer.from('89504e470d0a1a0a0000', 'hex').toString('base64');
  const html = '<style>@keyframes fly{from{transform:translateX(0)}to{transform:translateX(40px)}}body{background:#ecfeff}.scene{height:100vh;display:grid;place-items:center}#bird{width:70px;height:40px;background:#14b8a6;border-radius:50%;animation:fly .3s linear infinite alternate}</style><main class="scene"><div id="bird"></div></main><script>parent.PWNED=1;fetch("/leak")</script><img src="/leak" onerror="parent.PWNED=2"><img src="/old-asset.png" alt="旧图"><img src="' + corruptInlineImage + '" alt="损坏内嵌图"><picture><source srcset="/old-asset.png"><img id="valid-inline-image" src="' + validInlineImage + '" srcset="/old-asset.png 2x" alt="有效内嵌动图"></picture>';
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240"><rect width="320" height="240" fill="#0f172a"/><circle id="moon" cx="230" cy="60" r="30" fill="#fef3c7"/><path id="boat" d="M60 155L155 120L225 155Z" fill="#14b8a6"/><text x="25" y="210" fill="white">月夜纸船</text></svg>';
  function work(group, n) { return { id: `${group === 1 ? 'html' : group === 2 ? 'svg' : 'failed'}-${n}`, group_id: group, round_id: n ? `past-round-${n}` : 'latest-round', title: group === 1 ? `白鹭单车 ${n + 1}` : group === 2 ? `月夜纸船 ${n + 1}` : '失败作品', model: groups[group - 1].model, status: group === 3 ? 'failed' : 'success', reasoning_effort: groups[group - 1].reasoning_effort, created_at: new Date(fixtureCreatedAt - n * 60000).toISOString(), latency_ms: group === 3 ? null : 1200 + n, kind: 'html', ...(group === 3 ? { error: 'Fixture generation failed.' } : { content: group === 1 ? n === 0 ? html : '<style>body{background:#ccfbf1}</style><h2>静态作品</h2>' : svg }) }; }
  function feedItem(group, n = 0) {
    const item = { ...groups[group - 1], status: group === 4 ? 'queued' : group === 3 ? 'failed' : 'success', created_at: new Date(fixtureCreatedAt - n * 60000).toISOString(), round_id: n ? `past-round-${n}` : 'latest-round' };
    if (group !== 4) { item.work = work(group, n); item.work.preview = item.work.content; delete item.work.content; }
    return item;
  }
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/real-menu') {
      const childOrigin = url.searchParams.get('mode') === 'same' ? `http://${req.headers.host}` : realBackendOrigin;
      const token = ({ user: 'real-feature-user-jwt', empty: 'real-feature-empty-jwt', admin: 'real-feature-admin-jwt', invalid: 'forged-browser-jwt' })[url.searchParams.get('auth')] || '';
      const source = new URL('/_helper/features/' + (url.searchParams.get('feature') || 'benchmark'), childOrigin);
      if (token) source.searchParams.set('token', token);
      source.searchParams.set('user_id', '99');
      const escaped = source.href.replaceAll('&', '&amp;').replaceAll('"', '&quot;');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': `default-src 'none'; frame-src 'self' ${realBackendOrigin}; style-src 'unsafe-inline'; img-src data:` });
      res.end(`<!doctype html><html><head><meta charset="UTF-8"><link rel="icon" href="data:,"></head><body><iframe id="feature-menu" title="真实菜单功能" src="${escaped}" style="width:100%;height:1000px;border:0"></iframe></body></html>`);
      return;
    }
    if (liveIngress && url.pathname.startsWith('/_helper/')) {
      const backend = new URL(realBackendOrigin);
      const proxied = http.request({ hostname: backend.hostname, port: backend.port, path: req.url, method: req.method,
        headers: { ...req.headers, host: backend.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
      proxied.on('error', error => { res.writeHead(502); res.end(error.message); }); req.pipe(proxied); return;
    }
    if (['/leak', '/old-asset.png'].includes(url.pathname)) { leaks++; res.writeHead(200); res.end('not allowed'); return; }
    if (url.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    if (url.pathname.startsWith('/_helper/api/portal/')) {
      calls.push(url.pathname);
      let body = ''; for await (const chunk of req) body += chunk; const input = body ? JSON.parse(body) : {};
      let value;
      if (url.pathname.endsWith('/session')) value = { session: 'fixture-session', role: input.token === 'fixture-admin-jwt' ? 'admin' : 'user' };
      else if (url.pathname.endsWith('/modules')) value = { benchmark: true };
      else if (url.pathname === '/_helper/api/portal/gallery/feed') {
        if (fail) { res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: '示例服务暂时不可用' })); return; }
        const available = empty || noWorks ? [] : onlyFirst ? [1] : [1, 2, 3, 4];
        const latest = available.map(group => feedItem(group));
        const history = [1, 2].filter(group => available.includes(group)).flatMap(group => Array.from({ length: group === 1 ? 11 : 3 }, (_, n) => feedItem(group, n + 1))).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
        value = { items: [...latest, ...history], next_at: empty ? null : new Date(Date.now() + 3600000).toISOString(), timezone: 'Asia/Shanghai' };
      }
      else {
        const match = url.pathname.match(/\/gallery\/(\d+)(?:\/works\/([\w-]+))?$/), group = Number(match?.[1]);
        if (!match || !groups[group - 1]) { res.writeHead(404); res.end(); return; }
        if (match[2]) { const n = Number(match[2].split('-').at(-1)); value = { ...work(group, n), prompt: sharedPrompt }; }
        else { res.writeHead(404); res.end(); return; }
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
    await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor();
    assert.equal(await page.locator('#feature-main h1,.page-head,select,[role="tab"],[role="tablist"]').count(), 0, 'embedded feed has no duplicate title, source picker or tabs');
    assert.equal(await page.locator('.helper-gallery-stat,.helper-gallery-shared-prompt').count(), 0, 'the feed contains works and a next-run line, without summary blocks');
    assert.equal(await page.locator('.helper-gallery-work').count(), 18, 'latest participants plus bounded recent history are visible together');
    assert.deepEqual(await page.locator('.helper-gallery-work').evaluateAll(nodes => nodes.slice(0, 4).map(node => node.dataset.groupId)), ['1', '2', '3', '4'], 'latest attempt for every participant is ahead of old history');
    assert.equal(await page.locator('.helper-gallery-work[data-group-id="1"]').count(), 12);
    assert.equal(await page.locator('.helper-gallery-work[data-group-id="2"]').count(), 4);
    assert((await page.locator('.helper-gallery-work[data-group-id="1"]').first().locator('.helper-gallery-source').textContent()).includes('白鹭单车分组'));
    assert((await page.locator('.helper-gallery-work[data-group-id="1"]').first().locator('.helper-gallery-model').textContent()).includes('思考强度：高'));
    assert((await page.locator('.helper-gallery-work[data-group-id="3"]').textContent()).includes('失败'));
    assert((await page.locator('.helper-gallery-work[data-group-id="3"]').textContent()).includes('fixture-failed'));
    const queued = page.locator('.helper-gallery-work[data-group-id="4"]');
    assert.match(await queued.textContent(), /等待|排队/);
    assert.equal(await queued.evaluate(node => node.tagName), 'ARTICLE', 'an intended participant without a work is a non-clickable queued card');
    assert.equal(calls.filter(value => value === '/_helper/api/portal/gallery/feed').length, 1, 'initial gallery uses one aggregate feed request');
    assert.equal(calls.filter(value => value.includes('/works/')).length, 0, 'previews do not prefetch private work details');
    assert.equal(calls.filter(value => value === '/_helper/api/portal/gallery' || value.endsWith('/gallery/comparison') || /\/gallery\/\d+$/.test(value)).length, 0, 'flat feed does not fan out into legacy group/comparison reads');
    assert.equal(await page.locator('header,aside,.brand,.benchmark-form').count(), 0);
    assert.equal(new URL(page.url()).search, '');
    await page.screenshot({ path: path.join(directory, 'gallery-feed.png'), fullPage: true });

    const artwork = page.locator('.helper-gallery-work[data-group-id="1"] iframe').first();
    assert.equal(await artwork.getAttribute('sandbox'), ''); assert.equal(await artwork.getAttribute('inert'), ''); assert.equal(await artwork.getAttribute('allow'), null);
    const artworkHandle = await artwork.elementHandle();
    const frame = await artworkHandle.contentFrame(); await frame.locator('#bird').waitFor({ state: 'attached' });
    await frame.getByRole('note').filter({ hasText: '图片无法显示 · 旧图' }).waitFor({ state: 'attached' });
    await frame.getByRole('note').filter({ hasText: '图片无法显示 · 损坏内嵌图' }).waitFor({ state: 'attached' });
    await frame.waitForFunction(() => document.querySelector('#valid-inline-image')?.naturalWidth === 1);
    assert.equal(await frame.locator('img').count(), 1, 'historical invalid images become readable notes while valid inline images remain');
    assert.equal(await frame.locator('#valid-inline-image').getAttribute('srcset'), null);
    assert.equal(await frame.locator('picture source').count(), 0, 'external responsive-image candidates stay inert');
    assert.equal(await frame.locator('body').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(236, 254, 255)', 'inline model CSS renders under inherited CSP');
    const transforms = new Set(); for (let n = 0; n < 4; n++) { transforms.add(await frame.locator('#bird').evaluate(node => getComputedStyle(node).transform)); await page.waitForTimeout(75); } assert(transforms.size > 1, 'CSS animation remains active');
    assert.equal(await page.evaluate(() => window.PWNED), undefined); assert.equal(leaks, 0, 'model scripts/images cannot contact parent or network');
    const moonFrame = await page.locator('.helper-gallery-work[data-group-id="2"] iframe').first().elementHandle().then(handle => handle.contentFrame()); await moonFrame.locator('#moon').waitFor({ state: 'attached' }); assert.equal(await moonFrame.locator('#moon').evaluate(node => getComputedStyle(node).fill), 'rgb(254, 243, 199)');
    await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).click();
    await page.getByRole('dialog', { name: '作品详情', exact: true }).waitFor();
    await page.getByText('生成提示词', { exact: true }).waitFor();
    assert.equal(await page.locator('.helper-gallery-dialog .helper-gallery-prompt').textContent(), sharedPrompt);
    assert.equal(await page.locator('.helper-gallery-prompt img').count(), 0);
    assert.equal(calls.filter(value => value.endsWith('/works/svg-0')).length, 1);
    const box = await page.locator('.helper-gallery-dialog').boundingBox(); assert(box.width <= 1152 && box.height <= 1012 && box.y >= 0); await page.screenshot({ path: path.join(directory, 'gallery-detail.png'), fullPage: true }); await page.getByRole('button', { name: '关闭', exact: true }).click();
    const beforePoll = calls.filter(value => value === '/_helper/api/portal/gallery/feed').length;
    await page.clock.fastForward(30000); await page.waitForTimeout(100);
    assert(calls.filter(value => value === '/_helper/api/portal/gallery/feed').length > beforePoll, 'one aggregate feed refresh replaces per-group polling');
    assert.equal(await page.locator('.helper-gallery-work').count(), 18);
    assert.equal(await artworkHandle.evaluate(node => node.isConnected), true, 'unchanged aggregate refresh preserves iframe animation instead of rebuilding thumbnails');
    await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.classList.add('dark')); await page.waitForTimeout(350);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'flat feed fits a narrow embedded page');
    await page.screenshot({ path: path.join(directory, 'gallery-mobile-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '查看作品：月夜纸船 1', exact: true }).click(); await page.getByText('生成提示词', { exact: true }).waitFor();
    onlyFirst = true; await page.clock.fastForward(30000);
    await page.waitForFunction(() => document.querySelectorAll('.helper-gallery-work').length === 12);
    assert.equal(await page.locator('.helper-gallery-work[data-group-id="2"],.helper-gallery-work[data-group-id="3"],.helper-gallery-work[data-group-id="4"]').count(), 0);
    assert.equal(await page.locator('dialog').count(), 0, 'revoked source closes its stale detail and disappears on feed refresh');
    await page.evaluate(() => document.querySelector('.helper-gallery').parentElement.remove()); const afterDetach = calls.length; await page.clock.fastForward(60000); await page.waitForTimeout(80); assert.equal(calls.length, afterDetach, 'detached view stops timers/requests');

    empty = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt');
    await page.locator('.helper-gallery-empty').waitFor();
    assert((await page.locator('.helper-gallery-empty').textContent()).trim());
    assert.equal(await page.locator('.helper-gallery-work,.helper-gallery-stat,select').count(), 0);
    assert((await page.locator('.helper-gallery-empty').boundingBox()).height <= 120, 'empty gallery stays compact inside its menu frame');
    assert.equal(await page.getByRole('link', { name: '配置参测来源', exact: true }).count(), 0, 'ordinary empty gallery must not show admin configuration');
    await page.goto(origin + '/_helper/features/benchmark?token=fixture-admin-jwt');
    await page.getByRole('link', { name: '配置参测来源', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '配置参测来源', exact: true }).getAttribute('href'), '/_helper/setup#analytics', 'fresh admin can enter setup with a visible connection form');
    empty = false; fail = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt'); await page.getByText('示例服务暂时不可用', { exact: true }).waitFor(); fail = false; await page.getByRole('button', { name: '刷新', exact: true }).click(); await page.getByRole('button', { name: '查看作品：白鹭单车 1', exact: true }).waitFor();
    onlyFirst = false; noWorks = true; await page.goto(origin + '/_helper/features/benchmark?token=fixture-jwt'); await page.locator('.helper-gallery-empty').waitFor();
    assert.equal(await page.locator('.helper-gallery-work,.helper-gallery-stat,select').count(), 0, 'configured sources without work do not become invented participants or metric blocks');
    assert((await page.locator('.helper-gallery-empty').boundingBox()).height <= 120);
    noWorks = false;
    assert.equal(leaks, 0); assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0); assert.deepEqual(errors, []);
    await page.close();

    // Verify the actual helper HTML, CSP, JWT handshake and assets in a menu
    // iframe. Only the upstream data/model are fake; portal and storage are real.
    const parentOrigin = `http://localhost:${server.address().port}`;
    realBackend = await startRealFeatureBackend(directory, parentOrigin);
    realBackendOrigin = realBackend.origin; liveIngress = true;
    const livePage = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
    const traffic = [], assetResponses = [], liveErrors = [];
    livePage.on('request', request => traffic.push({ url: request.url(), headers: request.headers() }));
    livePage.on('response', response => { if (new URL(response.url()).pathname.startsWith('/_helper/static/')) assetResponses.push({ path: new URL(response.url()).pathname, status: response.status(), type: response.headers()['content-type'] }); });
    livePage.on('pageerror', error => liveErrors.push(error.message));
    const menu = () => livePage.frameLocator('#feature-menu');
    for (const mode of ['same', 'cross']) {
      const beforeMenu = traffic.length;
      await livePage.goto(parentOrigin + `/real-menu?mode=${mode}&auth=user`);
      await menu().locator('.helper-gallery-work').first().waitFor();
      assert.equal(await menu().locator('select,[role="tab"],[role="tablist"]').count(), 0);
      assert.deepEqual(await menu().locator('.helper-gallery-work').evaluateAll(nodes => [...new Set(nodes.map(node => node.dataset.groupId))]), ['3'], 'real portal exposes only the JWT user’s current group');
      assert((await menu().locator('.helper-gallery-model').first().textContent()).includes('fixture-loopback-model'));
      assert((await menu().locator('.helper-gallery-model').first().textContent()).includes('思考强度：高'));
      assert.equal(await menu().locator('#feature-main h1,.page-head').count(), 0);
      assert.equal(await menu().locator('.helper-gallery-stat').count(), 0);
      assert.equal(await menu().getByText('禁止泄露分组', { exact: true }).count(), 0);
      assert.equal(traffic.slice(beforeMenu).filter(request => new URL(request.url).pathname.endsWith('/gallery/feed')).length, 1);
      const directFrame = await livePage.locator('#feature-menu').elementHandle().then(handle => handle.contentFrame());
      assert.equal(new URL(directFrame.url()).search, '', 'the actual feature page removes query JWT before API calls');
      const storage = await directFrame.evaluate(() => ['localStorage', 'sessionStorage'].map(name => { try { return window[name].length; } catch (error) { if (error.name === 'SecurityError') return 'blocked'; throw error; } }));
      assert(storage.every(value => value === 0 || value === 'blocked'), 'portal JWT/session stays in memory even when third-party storage is blocked');
      await menu().locator('.helper-gallery-work').first().click();
      await menu().getByText('生成提示词', { exact: true }).waitFor();
      assert.equal(await menu().locator('.helper-gallery-dialog .helper-gallery-prompt').textContent(), 'Fixture immutable executed prompt');
      await menu().getByRole('button', { name: '关闭', exact: true }).click();
    }
    const expectedAssets = ['feature.js', 'gallery-view.js', 'style.css', 'gallery-view.css'];
    for (const asset of expectedAssets) assert(assetResponses.some(response => response.path.endsWith('/' + asset) && response.status === 200), `real menu must load ${asset}`);
    assert(assetResponses.every(response => response.status === 200), JSON.stringify(assetResponses));
    for (const request of traffic.filter(value => new URL(value.url).pathname.startsWith('/_helper/static/'))) {
      assert.equal(new URL(request.url).search, '');
      assert.equal(request.headers.authorization, undefined);
      assert(!request.headers.referer, 'initial parser asset fetches must not leak the iframe JWT in Referer');
    }
    await livePage.goto(parentOrigin + '/real-menu?mode=cross&auth=empty');
    await menu().locator('.helper-gallery-empty').waitFor();
    assert((await menu().locator('.helper-gallery-empty').textContent()).trim());
    assert((await menu().locator('.helper-gallery-empty').boundingBox()).height <= 120);
    assert.equal(await menu().locator('.helper-gallery-work,.helper-gallery-stat,select').count(), 0);
    assert.equal(await menu().getByRole('link', { name: '配置参测来源', exact: true }).count(), 0);
    for (const [feature, expectedTitle, expectedContent] of [
      ['pricing', '模型价格统计', 'sample'], ['ranking', '用户用量排名', '用户 #1']
    ]) {
      await livePage.goto(parentOrigin + `/real-menu?mode=cross&auth=user&feature=${feature}`);
      await menu().locator('table').waitFor();
      assert.equal(await menu().locator('#feature-main h1,.page-head').count(), 0, `${feature} iframe must not duplicate the host title or intro`);
      assert.equal(await menu().getByText(expectedTitle, { exact: true }).count(), 0);
      assert((await menu().locator('table').textContent()).includes(expectedContent));
      const childFrame = await livePage.locator('#feature-menu').elementHandle().then(handle => handle.contentFrame());
      assert((await childFrame.title()).includes(expectedTitle), 'feature identity remains accessible through document title');
      if (feature === 'ranking') assert(!/Alice|Bob|alice@example|bob@example/.test(await menu().locator('table').textContent()), 'ordinary ranking stays anonymous');
    }
    const beforeInvalid = traffic.length;
    await livePage.goto(parentOrigin + '/real-menu?mode=cross&auth=invalid');
    await menu().getByRole('alert').waitFor();
    assert((await menu().getByRole('alert').textContent()).trim(), 'invalid login keeps a compact readable error');
    assert.equal(await menu().locator('.helper-gallery').count(), 0, 'forged user_id=99 cannot turn an invalid JWT into an administrator');
    assert.equal(traffic.slice(beforeInvalid).filter(request => new URL(request.url).pathname.includes('/api/portal/gallery')).length, 0);
    const beforeMissing = traffic.length;
    await livePage.goto(parentOrigin + '/real-menu?mode=cross&auth=missing');
    await menu().getByRole('alert').waitFor();
    assert((await menu().getByRole('alert').textContent()).includes('请从 sub2api'));
    assert.equal(traffic.slice(beforeMissing).filter(request => new URL(request.url).pathname.endsWith('/api/portal/session')).length, 0);

    const beforeBlocked = traffic.filter(request => new URL(request.url).pathname.endsWith('/api/portal/session')).length;
    await livePage.goto(origin + '/real-menu?mode=cross&auth=user');
    await livePage.waitForTimeout(300);
    assert.equal(traffic.filter(request => new URL(request.url).pathname.endsWith('/api/portal/session')).length, beforeBlocked, 'an unauthorized ancestor must be blocked by the actual helper CSP before executing feature JS');
    assert.equal(await menu().locator('#feature-main').count(), 0);
    assert.deepEqual(liveErrors, []);
    await livePage.close();
    console.log(JSON.stringify({ passed: true, screenshots: directory, checks: ['single aggregate flat feed without selectors/tabs', 'latest participants ahead of bounded recent history', 'source/model/thought on each card', 'actual queued participant placeholders', 'executed prompt plaintext in on-demand detail', 'HTML CSS renders/animates in sandbox', 'legacy broken images become notes without requests; valid inline GIF remains', 'raw SVG visible', 'scripts/network blocked', 'no detail prefetch or group fanout', 'one aggregate poll with immediate ACL revocation cleanup', 'compact empty state without invented participants', 'gallery/pricing/ranking frames have no duplicate titles or intro', 'real pricing data and anonymous ranking table', 'empty user/admin configuration entry', 'actual same-origin and loopback cross-origin menu iframe', 'real JWT rejection and current group ACL', 'real CSP rejects unauthorized ancestor', 'all real feature assets load without JWT referrer', 'detached cleanup', 'mobile/dark/no key storage'] }, null, 2));
  } finally {
    if (realBackend) {
      realBackend.child.kill('SIGTERM');
      await Promise.race([new Promise(resolve => realBackend.child.once('exit', resolve)), new Promise(resolve => setTimeout(() => { realBackend.child.kill('SIGKILL'); resolve(); }, 3000))]);
    }
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

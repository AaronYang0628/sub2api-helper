/* Portable, offline mixed-media result and fixed quiz browser regression. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const zlib = require('node:zlib');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');
function crc32(bytes) { let value = 0xffffffff; for (const byte of bytes) { value ^= byte; for (let bit = 0; bit < 8; bit++) value = value & 1 ? (value >>> 1) ^ 0xedb88320 : value >>> 1; } return (value ^ 0xffffffff) >>> 0; }
function chunk(type, data) { const name = Buffer.from(type), size = Buffer.alloc(4), crc = Buffer.alloc(4); size.writeUInt32BE(data.length); crc.writeUInt32BE(crc32(Buffer.concat([name, data]))); return Buffer.concat([size, name, data, crc]); }
const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(2, 0); ihdr.writeUInt32BE(2, 4); ihdr[8] = 8; ihdr[9] = 6;
const png = Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.from([0,20,184,166,255,20,184,166,255,0,20,184,166,255,20,184,166,255]))), chunk('IEND', Buffer.alloc(0))]);
// Two genuine GIF image-descriptor blocks, black then white, with looping enabled.
const gif = Buffer.from('47494638396101000100800000000000ffffff21ff0b4e45545343415045322e30030100000021f904000a0000002c000000000100010000020244010021f904000a0000002c00000000010001000002024c01003b', 'hex');
function gifFrames(bytes) { let at = 13 + 3 * 2 ** ((bytes[10] & 7) + 1), count = 0; while (at < bytes.length && bytes[at] !== 0x3b) { const block = bytes[at++]; if (block === 0x21) at++; else if (block === 0x2c) { const packed = bytes[at + 8]; at += 9; if (packed & 0x80) at += 3 * 2 ** ((packed & 7) + 1); at++; count++; } else throw new Error('Invalid GIF fixture'); while (bytes[at]) at += bytes[at] + 1; at++; } return count; }
assert.equal(gifFrames(gif), 2);
const pngURI = 'data:image/png;base64,' + png.toString('base64'), gifURI = 'data:image/gif;base64,' + gif.toString('base64');
const external = 'https://media.example.invalid/generated.gif?fixture=1';
const mixed = { model: 'fixture-model', score: 1, completed: 5, total: 5, results: [
  { question: '<img src=x onerror=window.PWNED=1>', expected: '391', answer: '第一行\n<script>window.PWNED=1</script>\n<a href="javascript:alert(1)">不要执行</a>', passed: false, scored: true, latency_ms: 120 },
  { question: '文本与图片', expected: 'YES', answer: 'YES', passed: true, scored: true, latency_ms: 220, media: [{ src: pngURI, mime: 'image/png', alt: '<img onerror=alert(1)> 测试图片' }] },
  { question: '只返回动图', expected: '', answer: '', passed: false, scored: false, latency_ms: 320, media_omitted: 2, media: [{ src: gifURI, mime: 'image/gif', alt: '两帧循环动图' }] },
  { question: '外部图片需明确加载', expected: '', answer: '', passed: false, scored: false, latency_ms: 420, media: [{ src: external, mime: 'image/gif', alt: '外部 GIF' }, { src: 'http://media.example.invalid/bad.png' }, { src: 'https://user:pass@media.example.invalid/bad.png' }, { src: 'data:image/svg+xml;base64,PHN2Zy8+' }, { src: 'https:\\media.example.invalid/bad.png' }, { src: 'https://media.example.invalid/bad.svg' }] },
  { question: '请求错误示例', expected: '7', answer: '', passed: false, scored: false, latency_ms: 10, error: '模型请求失败，请稍后重试。' },
] };
(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-benchmark-ui-'));
  const posts = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/_helper/api/')) {
      let body = ''; for await (const chunk of req) body += chunk;
      if (req.method === 'POST') posts.push({ path: url.pathname, body: body ? JSON.parse(body) : {} });
      const value = url.pathname === '/_helper/api/admin/gallery' ? { groups: [], timezone: 'Asia/Shanghai' }
        : url.pathname.endsWith('/options') ? { keys: [{ id: 10, group_id: 1, group_name: '示例分组 A', name: '示例 Key A' }, { id: 20, group_id: 2, group_name: '示例分组 B', name: '示例 Key B' }] }
        : url.pathname === '/_helper/api/admin/benchmark' ? mixed : {};
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); return;
    }
    if (url.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    const content = url.pathname === '/renderer' ? '<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><link rel="stylesheet" href="/_helper/static/style.css"><link rel="stylesheet" href="/_helper/static/benchmark-results.css"><script src="/_helper/static/benchmark-results.js" defer></script></head><body><main id="result" class="feature-shell"></main></body></html>' : await fs.readFile(path.join(root, url.pathname.startsWith('/_helper/static/') ? 'helper/static/' + path.basename(url.pathname) : 'helper/static/index.html'));
    res.writeHead(200, { 'Content-Type': url.pathname.endsWith('.js') ? 'text/javascript' : url.pathname.endsWith('.css') ? 'text/css' : 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; object-src 'none'; frame-src 'none'" }); res.end(content);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' }), errors = [];
    page.on('pageerror', error => errors.push(error.message)); page.on('console', value => { if (value.type() === 'error' && !/Failed to load resource/.test(value.text())) errors.push(value.text()); }); page.on('dialog', value => value.accept());
    let remoteCalls = 0;
    await page.route('https://media.example.invalid/**', async route => { remoteCalls++; assert.equal(route.request().headers().referer, undefined); assert.equal(route.request().headers().authorization, undefined); await route.fulfill({ contentType: 'image/gif', body: gif, headers: { 'Cache-Control': 'no-store' } }); });
    await page.addInitScript(() => sessionStorage.setItem('helper_session', 'fixture-opaque-session'));
    await page.goto(origin + '/_helper/setup#analytics'); await page.getByText('固定题自检', { exact: true }).click();
    const quiz = page.locator('.benchmark-form');
    await quiz.locator('[name=model]').fill('my-model'); await quiz.locator('[name=group_id]').selectOption('2'); await quiz.locator('[name=api_format]').selectOption('responses');
    await page.getByRole('button', { name: '开始检测', exact: true }).click(); await page.getByRole('heading', { name: '检测结果', exact: true }).waitFor();
    assert.equal(await page.locator('.benchmark-form').isVisible(), false); assert.equal(await page.locator('.helper-benchmark-question').count(), 5);
    await page.getByText('1 / 2', { exact: true }).waitFor(); await page.getByText('5 / 5', { exact: true }).waitFor(); await page.getByText('未判分 2', { exact: true }).waitFor(); await page.getByText('请求失败 1', { exact: true }).waitFor();
    assert.equal(await page.locator('.helper-benchmark-results script,.helper-benchmark-results iframe,.helper-benchmark-results svg,.helper-benchmark-results a,.helper-benchmark-results object').count(), 0);
    assert.equal(await page.evaluate(() => window.PWNED), undefined); assert((await page.locator('.helper-benchmark-text').first().textContent()).includes('<img'));
    const embedded = page.locator('.helper-benchmark-results img'); assert.equal(await embedded.count(), 2); await page.waitForFunction(() => [...document.querySelectorAll('.helper-benchmark-results img')].some(img => img.src.startsWith('data:image/png') && img.naturalWidth === 2));
    const gifImage = page.locator('img[alt="两帧循环动图"]'); await gifImage.scrollIntoViewIfNeeded(); await page.waitForFunction(() => document.querySelector('img[alt="两帧循环动图"]').naturalWidth === 1);
    assert.equal(await gifImage.getAttribute('src'), gifURI, 'GIF remains the original animated image, never flattened');
    await page.getByText('已忽略 2 项不支持或超出限制的媒体内容。仅展示 PNG、JPEG、WebP、GIF 及无 URL 凭据的 HTTPS 图片。', { exact: true }).waitFor();
    assert.equal(remoteCalls, 0, 'external images never load automatically');
    await page.getByRole('button', { name: '加载外部图片', exact: true }).click(); await page.getByText('外部图片已加载 · media.example.invalid', { exact: true }).waitFor(); assert.equal(remoteCalls, 1);
    assert.equal(await page.locator('.helper-benchmark-remote img').getAttribute('referrerpolicy'), 'no-referrer'); assert.equal(await page.locator('.helper-benchmark-remote img').getAttribute('crossorigin'), null);
    await page.screenshot({ path: path.join(directory, 'mixed-results.png'), fullPage: true });
    await page.getByRole('button', { name: '返回配置', exact: true }).click(); assert.equal(await quiz.isVisible(), true); assert.equal(await quiz.locator('[name=model]').inputValue(), 'my-model'); assert.equal(await quiz.locator('[name=group_id]').inputValue(), '2'); assert.equal(await quiz.locator('[name=key_id]').inputValue(), '20'); assert.equal(await quiz.locator('[name=api_format]').inputValue(), 'responses'); assert.equal(await page.locator('.helper-benchmark-results').count(), 0);
    assert.equal(new URL(page.url()).search, ''); assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], ['helper_session']], 'only the login session is stored');
    assert.deepEqual(posts.find(value => value.path.endsWith('/benchmark')).body, { model: 'my-model', key_id: 20, group_id: 2, api_format: 'responses' });
    await page.goto(origin + '/renderer'); await page.waitForFunction(() => typeof helperBenchmarkResults === 'function');
    await page.evaluate(data => document.getElementById('result').replaceChildren(helperBenchmarkResults(data)), { total: 1, completed: 1, results: [{ question: '媒体题', answer: '', scored: false, passed: false, media: [{ src: gifURI, mime: 'image/gif' }] }] });
    await page.getByText('未判分', { exact: true }).first().waitFor(); assert.equal(await page.getByText('错误 0', { exact: true }).count(), 1); assert.equal(await page.getByRole('button', { name: '返回配置' }).count(), 0, 'onBack is optional');
    await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.classList.add('dark')); await page.waitForTimeout(350); await page.screenshot({ path: path.join(directory, 'media-only-mobile-dark.png'), fullPage: true });
    await page.evaluate(() => document.getElementById('result').replaceChildren(helperBenchmarkResults({ results: [], total: 5, completed: 0 }))); await page.getByText('没有可展示的检测结果。返回配置后可重新运行。', { exact: true }).waitFor();
    await page.evaluate(url => document.getElementById('result').replaceChildren(helperBenchmarkResults({ total: 1, completed: 1, results: [{ scored: false, answer: '', media: [{ src: url }] }] })), 'https://media.example.invalid/retry.gif');
    let retries = 0; await page.route('https://media.example.invalid/retry.gif', async route => { retries++; await route.fulfill({ status: retries === 1 ? 404 : 200, contentType: 'image/gif', body: retries === 1 ? Buffer.from('invalid-image') : gif, headers: { 'Cache-Control': 'no-store' } }); });
    await page.getByRole('button', { name: '加载外部图片' }).click(); await page.getByText('图片加载失败，可检查地址后重试。', { exact: true }).waitFor({timeout:5000}); await page.getByRole('button', { name: '重试加载' }).click(); await page.getByText('外部图片已加载 · media.example.invalid', { exact: true }).waitFor(); assert.equal(retries, 2);
    await page.evaluate(() => document.getElementById('result').replaceChildren(helperBenchmarkResults({ total: 1, completed: 1, results: [{ scored: false, answer: '', media: [{ src: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB', mime: 'image/png', alt: '损坏的旧图片' }] }] })));
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.getByText('图片无法显示。', { exact: true }).waitFor({ timeout: 5000 });
    assert.equal(await page.locator('.helper-benchmark-results img').count(), 0, 'corrupt inline data becomes a readable placeholder instead of a broken image');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, screenshots: directory, checks: ['dedicated results view/retained form', 'plaintext XSS-safe answers', 'valid PNG+two-frame GIF preserved', 'corrupt inline image fallback', 'media-only neutral scoring', 'graded denominator/completed/error counts', 'unsupported/omitted media shown', 'external HTTPS explicit load/no referrer/no helper auth', 'failure+manual retry', 'optional back/empty/dark/mobile', 'no result or key storage/URL'] }, null, 2));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

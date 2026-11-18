/* Offline browser regression for group gallery administration. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');
const configs = new Map([
  [1, { group_id: 1, name: '<img src=x onerror=window.PWNED=1>', model: 'old-model', key_id: 10, api_format: 'chat', interval_minutes: 30, enabled: false, prompt: '只返回完整 SVG 作品。', running: false }],
  [2, { group_id: 2, name: '已撤销 Key 的分组', model: 'kept-model', key_id: 20, api_format: 'responses', interval_minutes: 30, enabled: true, prompt: '保留的创作提示词', running: false }],
]);
let failCatalog = false;
const keys = [{ id: 10, name: 'Group one key', group_id: 1, group_name: '第一组' }, { id: 30, name: 'New key', group_id: 3, group_name: '第三组' }];
const writes = [], errors = [];
let runReads = 0, rejectSave = false;
function detail(groupID) { return { group: configs.get(groupID), stats: { success_24h: 1, failed_24h: 0, total: 1 }, works: [] }; }
const fixture = `
window.helperGallery=(container)=>{ container.textContent='最近生成的作品'; return {refresh(){window.VIEW_REFRESHES=(window.VIEW_REFRESHES||0)+1},destroy(){window.VIEW_DESTROYED=true}} };
const request=async(path,method='GET',data)=>{const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});const body=await response.json();if(!response.ok)throw new Error(body.detail);return body};
window.controller=helperGalleryAdmin(document.getElementById('admin'),{request});
`;
(async () => {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      let raw = ''; for await (const part of req) raw += part;
      const body = raw ? JSON.parse(raw) : undefined;
      const match = url.pathname.match(/^\/api\/gallery\/(\d+)(\/run|\/pause)?$/);
      let output, status = 200;
      if (url.pathname === '/api/gallery') {
        if (failCatalog) { status = 503; output = { detail: '分组目录暂不可用，请稍后刷新。' }; }
        else output = { groups: [...configs.values()], timezone: 'Asia/Shanghai' };
      }
      else if (url.pathname === '/api/benchmark/options') output = { keys };
      else if (match) {
        const groupID = Number(match[1]);
        if (req.method !== 'GET') writes.push({ path: url.pathname, method: req.method, body });
        if (req.method === 'PUT') {
          if (rejectSave) { status = 422; output = { detail: '所选 Key 已失效，请刷新后重试。' }; }
          else { configs.set(groupID, { ...configs.get(groupID), ...body, group_id: groupID }); output = detail(groupID); }
        } else if (match[2] === '/pause') { configs.get(groupID).enabled = false; output = detail(groupID); }
        else if (match[2] === '/run') { configs.get(groupID).running = true; runReads = 0; output = { status: 'queued' }; }
        else {
          const group = configs.get(groupID);
          if (!group) { status = 404; output = { detail: '分组未配置' }; }
          else { if (group.running && ++runReads > 1) group.running = false; output = detail(groupID); }
        }
      } else { status = 404; output = { detail: 'Not found' }; }
      res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(output)); return;
    }
    if (url.pathname === '/fixture.js') { res.writeHead(200, { 'Content-Type': 'text/javascript' }); res.end(fixture); return; }
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'helper/static', path.basename(url.pathname));
      res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(await fs.readFile(file)); return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'" });
    res.end('<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/gallery-admin.css"><script src="/static/gallery-admin.js" defer></script><script src="/fixture.js" defer></script></head><body class="embedded"><main id="admin" class="embed-shell"></main></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1100 }, locale: 'zh-CN' });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const group = page.locator('[name=gallery_group_id]'), model = page.locator('[name=gallery_model]');
    await model.waitFor(); assert.equal(await model.inputValue(), 'old-model');
    assert.equal(await page.evaluate(() => window.PWNED), undefined);
    assert.equal(await page.locator('#admin img').count(), 0);
    assert.equal(await page.getByRole('button', { name: '生成一轮', exact: true }).isEnabled(), true);
    await model.fill('new-model'); await page.locator('[name=gallery_api_format]').selectOption('responses');
    await page.locator('[name=gallery_interval_minutes]').fill('45');
    assert.equal(await page.getByRole('button', { name: '生成一轮', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '保存配置', exact: true }).click(); await page.getByText('配置已保存。', { exact: true }).waitFor();
    assert.deepEqual(writes[0].body, { name: '<img src=x onerror=window.PWNED=1>', model: 'new-model', key_id: 10, api_format: 'responses', interval_minutes: 45, enabled: false, prompt: '只返回完整 SVG 作品。' });
    await page.locator('[name=gallery_enabled]').check(); await page.getByText('自动生成已开启。', { exact: true }).waitFor();
    assert.equal(configs.get(1).enabled, true); assert.equal(writes.filter(value => value.path.endsWith('/run')).length, 0);
    await page.locator('[name=gallery_enabled]').uncheck(); await page.getByText('自动生成已暂停。', { exact: true }).waitFor();
    assert.equal(configs.get(1).enabled, false); assert(writes.some(value => value.path.endsWith('/pause')));
    await page.getByRole('button', { name: '生成一轮', exact: true }).click(); await page.getByText('任务已提交，正在等待生成结果…', { exact: true }).waitFor();
    await page.getByText('本轮生成已结束，可在下方查看作品。', { exact: true }).waitFor({ timeout: 8000 });
    assert.equal(writes.filter(value => value.path.endsWith('/run')).length, 1); assert((await page.evaluate(() => window.VIEW_REFRESHES)) >= 1);
    await group.selectOption('2'); await page.locator('[name=gallery_model]').waitFor(); await page.waitForFunction(() => document.querySelector('[name=gallery_model]')?.value === 'kept-model');
    assert.equal(await page.locator('[name=gallery_key_id]').inputValue(), '20');
    assert.equal(await page.getByRole('button', { name: '保存配置', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '生成一轮', exact: true }).isDisabled(), true);
    assert.equal(await page.locator('[name=gallery_enabled]').isEnabled(), true, 'revoked key must not block pausing');
    await page.locator('[name=gallery_enabled]').uncheck(); await page.getByText('自动生成已暂停。', { exact: true }).waitFor();
    assert.equal(configs.get(2).enabled, false); assert.equal(await page.locator('[name=gallery_enabled]').isDisabled(), true, 'cannot re-enable revoked key');
    await group.selectOption('3'); await page.waitForFunction(() => document.querySelector('[name=gallery_interval_minutes]')?.value === '30');
    assert.equal(await page.locator('[name=gallery_enabled]').isChecked(), false); assert.equal(await page.getByRole('button', { name: '生成一轮', exact: true }).isDisabled(), true);
    await page.locator('[name=gallery_model]').fill('new-group-model'); rejectSave = true;
    await page.getByRole('button', { name: '保存配置', exact: true }).click(); await page.getByText('所选 Key 已失效，请刷新后重试。', { exact: true }).waitFor();
    assert.equal(await page.locator('[name=gallery_model]').inputValue(), 'new-group-model', 'failed save retains edits'); rejectSave = false;
    await page.getByRole('button', { name: '保存配置', exact: true }).click(); await page.getByText('配置已保存。', { exact: true }).waitFor();
    assert.equal(configs.get(3).enabled, false); assert.equal(configs.get(3).key_id, 30);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0, 'no model key/config stored in browser storage');
    failCatalog = true; await page.reload(); await page.getByText('分组目录暂不可用，请稍后刷新。', { exact: true }).waitFor();
    assert.equal(await page.locator('[name=gallery_model]').count(), 0, 'failed catalog cannot silently replace saved jobs with new defaults');
    assert.equal(await page.getByRole('button', { name: '刷新分组', exact: true }).isEnabled(), true);
    failCatalog = false; await page.getByRole('button', { name: '刷新分组', exact: true }).click(); await page.locator('[name=gallery_model]').waitFor();
    await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.classList.add('dark'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile layout must fit viewport');
    await page.evaluate(() => document.getElementById('admin').remove()); await page.waitForFunction(() => window.VIEW_DESTROYED === true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, checks: ['group/key metadata', 'save exact safe fields', 'schedule toggle and pause', 'explicit queued generation', 'revoked-key config view and pause', 'new config defaults', 'failure retains form', 'catalog failure and retry', 'inert names/no browser key storage', 'dark/mobile and detach cleanup'] }, null, 2));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });

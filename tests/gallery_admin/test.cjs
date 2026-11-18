/* Offline regression for saved group records, explicit editing and shared jobs. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { chromium } = createRequire(path.join(root, 'ui/package.json'))(process.env.HELPER_PLAYWRIGHT_PATH || 'playwright-core');
const malicious = '<img src=x onerror=window.PWNED=1>';
const configs = new Map([
  [1, { group_id: 1, name: malicious, model: 'old-model', key_id: 10, api_format: 'chat', interval_minutes: 30, enabled: false, prompt: '只返回完整 SVG 作品。', running: false }],
  [2, { group_id: 2, name: '已撤销 Key 的分组', model: 'kept-model', key_id: 20, api_format: 'responses', reasoning_effort: 'high', interval_minutes: 30, enabled: true, prompt: '保留的创作提示词', running: false }],
]);
const upstreamGroups = [1, 2, 3, 4].map(id => ({ id, name: `官方分组 ${id}`, platform: 'openai', status: 'active' }));
let keys = [{ id: 10, name: 'Primary team key', group_id: 1, group_name: '第一组' }, { id: 30, name: 'New group key', group_id: 3, group_name: '第三组' }];
const writes = [], errors = [];
let failCatalog = false, failOptions = false, keyError = null, runReads = 0, rejectSave = false;
let comparisonConfig = null, comparisonRound = null, comparisonReads = 0, rejectComparison = false;
const member = groupID => comparisonConfig?.group_ids.includes(groupID) || false;
function detail(groupID) { return { group: { ...configs.get(groupID), comparison_member: member(groupID) }, stats: { success_24h: 1, failed_24h: 0, total: 1 }, works: [] }; }
const fixture = `
window.helperGallery=(container)=>{ window.VIEW_CREATED=true;container.textContent='最近生成的作品';return {ready:Promise.resolve(),refresh(){window.VIEW_REFRESHES=(window.VIEW_REFRESHES||0)+1},selectGroup(id){window.VIEW_GROUP=id},destroy(){window.VIEW_DESTROYED=true}} };
const request=async(path,method='GET',data)=>{const response=await fetch('/_helper/api/admin'+path,{method,headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});const body=await response.json();if(!response.ok)throw new Error(body.detail);return body};
window.controller=helperGalleryAdmin(document.getElementById('admin'),{request});
`;
(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'helper-gallery-admin-'));
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/_helper/api/')) {
      let raw = ''; for await (const part of req) raw += part;
      const body = raw ? JSON.parse(raw) : undefined;
      const match = url.pathname.match(/^\/_helper\/api\/admin\/gallery\/(\d+)(\/run|\/pause)?$/);
      let output, status = 200;
      if (url.pathname === '/_helper/api/admin/gallery') {
        if (failCatalog) { status = 503; output = { detail: '已保存分组目录暂不可用，请稍后刷新。' }; }
        else output = { groups: [...configs.values()].map(group => ({ ...group, comparison_member: member(group.group_id) })), timezone: 'Asia/Shanghai' };
      } else if (url.pathname === '/_helper/api/admin/gallery/options') {
        if (failOptions) { status = 503; output = { detail: '分组与 Key 目录暂不可用。' }; }
        else output = { groups: upstreamGroups, keys: keyError ? [] : keys, group_error: null, key_error: keyError };
      } else if (/^\/_helper\/api\/admin\/gallery\/comparison(?:\/(?:run|pause))?$/.test(url.pathname)) {
        if (req.method !== 'GET') writes.push({ path: url.pathname, method: req.method, body });
        if (req.method === 'DELETE') comparisonConfig = null;
        else if (req.method === 'PUT') {
          if (rejectComparison) { status = 422; output = { detail: '同题设置暂时无法保存，请重试。' }; }
          else comparisonConfig = { ...body, next_at: body.enabled ? '2026-11-19T00:00:00Z' : null, running: false };
        } else if (url.pathname.endsWith('/pause')) comparisonConfig.enabled = false;
        else if (url.pathname.endsWith('/run')) {
          comparisonConfig.running = true; comparisonReads = 0;
          comparisonRound = { id: 1, prompt: comparisonConfig.prompt, status: 'running', items: comparisonConfig.group_ids.map((groupID, index) => ({ group_id: groupID, name: configs.get(groupID).name, model: configs.get(groupID).model, status: index ? 'queued' : 'running' })) };
        } else if (comparisonRound?.status === 'running' && ++comparisonReads > 1) {
          comparisonRound.status = 'partial'; comparisonConfig.running = false;
          comparisonRound.items.forEach((item, index) => { item.status = index ? 'failed' : 'success'; });
        }
        output ??= { config: comparisonConfig, round: comparisonRound, timezone: 'Asia/Shanghai' };
      } else if (match) {
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
    if (url.pathname.startsWith('/_helper/static/')) {
      const file = path.join(root, 'helper/static', path.basename(url.pathname));
      res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(await fs.readFile(file)); return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'" });
    res.end('<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><link rel="stylesheet" href="/_helper/static/style.css"><link rel="stylesheet" href="/_helper/static/gallery-admin.css"><script src="/_helper/static/icons.js" defer></script><script src="/_helper/static/gallery-admin.js" defer></script><script src="/fixture.js" defer></script></head><body class="embedded"><main id="admin" class="embed-shell"></main></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.HELPER_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
    page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
    const row = groupID => page.locator(`[data-gallery-group-id="${groupID}"]`);
    const edit = async groupID => { await row(groupID).getByRole('button', { name: /^编辑分组 / }).click(); await page.locator('[name=gallery_model]').waitFor(); };
    const close = async () => page.getByRole('button', { name: '关闭编辑', exact: true }).click();
    await page.goto(`http://127.0.0.1:${server.address().port}/_helper/setup`);
    await row(1).waitFor(); assert.equal(await page.locator('[data-gallery-group-id]').count(), 2);
    assert.equal(await page.locator('[name=gallery_model]').count(), 0, 'persisted records do not auto-open a form');
    assert.equal(await page.evaluate(() => window.VIEW_CREATED), undefined, 'work grid stays collapsed until requested');
    assert.match(await row(1).innerText(), /old-model/); assert.match(await row(1).innerText(), /Primary team key/); assert.match(await row(1).innerText(), /默认/);
    assert.match(await row(2).innerText(), /高/); assert.equal(await page.evaluate(() => window.PWNED), undefined); assert.equal(await page.locator('#admin img').count(), 0);
    await page.screenshot({ path: path.join(screenshots, 'saved-groups.png'), fullPage: true });
    await edit(1); const model = page.locator('[name=gallery_model]');
    assert.equal(await model.inputValue(), 'old-model'); assert.equal(await page.locator('[name=gallery_reasoning_effort]').inputValue(), 'default');
    await model.fill('new-model'); await page.locator('[name=gallery_api_format]').selectOption('responses'); await page.locator('[name=gallery_reasoning_effort]').selectOption('xhigh');
    await page.locator('[name=gallery_interval_minutes]').fill('45'); await page.getByRole('button', { name: '保存配置', exact: true }).click();
    await page.getByText('配置已保存。', { exact: true }).waitFor(); assert.equal(await page.locator('[name=gallery_model]').count(), 0);
    assert.deepEqual(writes[0].body, { name: malicious, model: 'new-model', key_id: 10, api_format: 'responses', reasoning_effort: 'xhigh', interval_minutes: 45, enabled: false, prompt: '只返回完整 SVG 作品。' });
    assert.match(await row(1).innerText(), /new-model/); assert.match(await row(1).innerText(), /超高/); assert.equal(configs.get(2).model, 'kept-model', 'editing one row preserves other persisted jobs');
    await edit(1); await page.locator('[name=gallery_enabled]').check(); await page.getByText('自动生成已开启。', { exact: true }).waitFor();
    const geometry = await page.locator('.helper-gallery-admin-form .helper-switch').evaluate(label => { const track=label.querySelector('.switch').getBoundingClientRect(),caption=label.lastElementChild.getBoundingClientRect();return {width:track.width,left:caption.left,right:track.right}; });
    assert(geometry.width >= 40 && geometry.left >= geometry.right, 'switch track and label must not overlap');
    await page.locator('[name=gallery_enabled]').uncheck(); await page.getByText('自动生成已暂停。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '生成一轮', exact: true }).click(); await page.getByText('本轮生成已结束，可在下方查看作品。', { exact: true }).waitFor({ timeout: 8000 });
    assert.equal(writes.filter(value => value.path.endsWith('/run')).length, 1); await close();
    await edit(2); assert.equal(await page.getByRole('button', { name: '保存配置', exact: true }).isDisabled(), true); assert.equal(await page.locator('[name=gallery_enabled]').isEnabled(), true); await close();
    await row(2).getByRole('button', { name: '暂停', exact: true }).click(); await page.getByText('自动生成已暂停。', { exact: true }).waitFor(); assert.equal(configs.get(2).enabled, false);
    await page.getByRole('button', { name: '新增分组', exact: true }).click(); const picker = page.getByRole('combobox', { name: '选择要添加的分组', exact: true });
    assert.equal(await picker.locator('option[value="1"]').count(), 0, 'saved groups cannot be accidentally re-added'); await picker.selectOption('4');
    await page.locator('[name=gallery_model]').waitFor(); assert.equal(await page.locator('[name=gallery_name]').inputValue(), '官方分组 4'); assert.equal(await page.getByRole('button', { name: '保存配置', exact: true }).isDisabled(), true);
    await page.getByText('此分组尚无可用 API Key。', { exact: false }).waitFor(); await close(); assert.equal(configs.has(4), false);
    await page.getByRole('button', { name: '新增分组', exact: true }).click(); await picker.selectOption('3'); await page.locator('[name=gallery_model]').fill('new-group-model'); await page.locator('[name=gallery_reasoning_effort]').selectOption('medium');
    rejectSave = true; await page.getByRole('button', { name: '保存配置', exact: true }).click(); await page.getByText('所选 Key 已失效，请刷新后重试。', { exact: true }).waitFor(); assert.equal(await page.locator('[name=gallery_model]').inputValue(), 'new-group-model'); rejectSave = false;
    await page.getByRole('button', { name: '保存配置', exact: true }).click(); await page.getByText('配置已保存。', { exact: true }).waitFor(); assert.equal(configs.get(3).reasoning_effort, 'medium'); assert.equal(configs.get(3).enabled, false); assert.equal(await page.locator('[data-gallery-group-id]').count(), 3);
    const sharedPanel = page.locator('details.helper-gallery-comparison'); assert.equal(await sharedPanel.getAttribute('open'), null); await sharedPanel.locator(':scope > summary').click();
    await page.locator('[name=gallery_comparison_group_ids][value="1"]').check(); await page.locator('[name=gallery_comparison_group_ids][value="3"]').check(); assert.equal(await page.locator('[name=gallery_comparison_group_ids][value="2"]').isDisabled(), true);
    const commonPrompt = '所有分组请用 SVG 创作相同主题：海边落日。'; const originalPrompts = [configs.get(1).prompt, configs.get(3).prompt];
    await page.locator('[name=gallery_comparison_prompt]').fill(commonPrompt); await page.getByRole('button', { name: '保存同题设置', exact: true }).click(); await page.getByText('同题设置已保存。', { exact: true }).waitFor();
    assert.deepEqual(writes.find(value=>value.path.endsWith('/comparison')&&value.method==='PUT').body, { group_ids: [1,3], prompt: commonPrompt, interval_minutes: 30, enabled: false }); assert.deepEqual([configs.get(1).prompt,configs.get(3).prompt],originalPrompts);
    await sharedPanel.locator(':scope > summary').click(); await row(1).getByRole('button', { name: '同题已暂停', exact: true }).click(); assert.notEqual(await sharedPanel.getAttribute('open'), null, 'membership link opens shared settings');
    await edit(3); assert.equal(await page.locator('[name=gallery_prompt]').isVisible(), false); assert.equal(await page.locator('[name=gallery_reasoning_effort]').inputValue(), 'medium'); await close();
    await page.locator('[name=gallery_comparison_enabled]').check(); await page.getByText('同题定时已开启。', { exact: true }).waitFor(); await page.locator('[name=gallery_comparison_enabled]').uncheck(); await page.getByText('同题定时已暂停，当前一轮会继续完成。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '同题生成一轮', exact: true }).click(); await page.getByText('本轮同题对比已结束，可在下方查看各分组的作品。', { exact: true }).waitFor({ timeout: 8000 }); assert.equal(comparisonRound.status, 'partial');
    await page.locator('[name=gallery_comparison_prompt]').fill(commonPrompt+' 保留编辑。'); rejectComparison=true; await page.getByRole('button',{name:'保存同题设置',exact:true}).click(); await page.getByText('同题设置暂时无法保存，请重试。',{exact:true}).waitFor(); assert.equal(await page.locator('[name=gallery_comparison_prompt]').inputValue(),commonPrompt+' 保留编辑。'); rejectComparison=false;
    await page.getByRole('button',{name:'退出同题配置',exact:true}).click(); await page.getByText('已退出同题配置，作品已保留。',{exact:true}).waitFor(); assert.equal(comparisonConfig,null); assert.equal(comparisonRound.items.length,2); assert.match(await row(1).innerText(),/已暂停/);
    await row(1).getByRole('button',{name:/^查看分组 /}).click(); await page.waitForFunction(()=>window.VIEW_CREATED===true); assert.equal(await page.evaluate(()=>window.VIEW_GROUP),1);
    await edit(1); await page.locator('[name=gallery_model]').fill('unsaved model retained'); await page.getByRole('button',{name:'刷新分组',exact:true}).click(); assert.equal(await page.locator('[name=gallery_model]').inputValue(),'unsaved model retained'); await close();
    configs.get(2).enabled=true; failOptions=true; await page.getByRole('button',{name:'刷新分组',exact:true}).click(); await page.getByText('分组目录暂不可用：',{exact:false}).waitFor(); assert.equal(await page.locator('[data-gallery-group-id]').count(),3); assert.equal(await page.getByRole('button',{name:'新增分组',exact:true}).isDisabled(),true);
    await row(2).getByRole('button',{name:'暂停',exact:true}).click(); await page.getByText('自动生成已暂停。',{exact:true}).waitFor(); assert.equal(configs.get(2).enabled,false,'options failure cannot prevent stopping an existing job');
    failOptions=false; failCatalog=true; await page.getByRole('button',{name:'刷新分组',exact:true}).click(); await page.getByText('已保存分组目录暂不可用，请稍后刷新。',{exact:true}).waitFor(); assert.equal(await page.locator('[data-gallery-group-id]').count(),3,'cached records remain visible'); assert.equal(await page.getByRole('button',{name:'新增分组',exact:true}).isDisabled(),true,'unconfirmed saved catalog cannot create over an unknown job'); await page.reload(); await page.getByText('无法确认已有配置，请刷新后重试。',{exact:true}).waitFor(); assert.equal(await page.locator('[name=gallery_model]').count(),0); assert.equal(await page.getByRole('button',{name:'新增分组',exact:true}).isDisabled(),true);
    failCatalog=false; await page.getByRole('button',{name:'刷新分组',exact:true}).click(); await row(1).waitFor(); assert.match(await row(1).innerText(),/new-model/); assert.match(await row(1).innerText(),/超高/);
    await page.setViewportSize({width:390,height:844}); await page.evaluate(()=>document.documentElement.classList.add('dark')); await page.waitForTimeout(350); assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true); await page.screenshot({path:path.join(screenshots,'saved-groups-mobile-dark.png'),fullPage:true});
    await edit(1); await page.screenshot({path:path.join(screenshots,'single-group-editor-mobile.png'),fullPage:true}); assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true); await close();
    configs.clear(); keys=[]; comparisonConfig=null; await page.reload(); await page.getByText('还没有配置模型分组',{exact:true}).waitFor(); await page.getByText('已读取 4 个上游分组，尚无可用的分组 API Key。',{exact:false}).waitFor(); assert.equal(await page.getByRole('button',{name:'新增分组',exact:true}).isEnabled(),true); await page.screenshot({path:path.join(screenshots,'empty-no-key.png'),fullPage:true});
    keyError='可用 Key 查询失败'; await page.getByRole('button',{name:'刷新分组',exact:true}).click(); await page.getByText('API Key 目录暂不可用，请刷新后确认可用的分组 Key。',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0); await page.locator('.helper-gallery-admin-preview > summary').click(); await page.waitForFunction(()=>window.VIEW_CREATED===true); await page.evaluate(()=>document.getElementById('admin').remove()); await page.waitForFunction(()=>window.VIEW_DESTROYED===true); assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,screenshots,checks:['persisted list without automatic form/grid','explicit add/edit one group','Key names and reasoning strength safe payload','native switch geometry','revoked-key and options-failure pause','official no-Key groups remain visible','new saves and failed edits retained','shared membership links and operations','refresh retains draft and jobs','catalog failure and retry','meaningful empty state','mobile/dark and lazy-view cleanup']},null,2));
  } finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
})().catch(error=>{console.error(error);process.exitCode=1});

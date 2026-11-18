/* Group gallery administration. Browser receives selectable key IDs, never keys. */
'use strict';
(() => {
  const DEFAULT_PROMPT = '请创作一幅完整的 SVG 作品。只输出 SVG 源码，不要解释或 Markdown 代码块。';
  const fields = ['name', 'model', 'key_id', 'api_format', 'interval_minutes', 'enabled', 'prompt'];
  const id = value => (typeof value === 'number' || (typeof value === 'string' && /^[1-9][0-9]*$/.test(value)))
    && Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
  function el(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === undefined || value === null) continue;
      if (key === 'class') node.className = value;
      else if (['value', 'disabled', 'checked', 'hidden', 'required'].includes(key)) node[key] = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child !== undefined && child !== null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  const button = (text, action, classes = 'button') => el('button', { type: 'button', class: classes, onclick: action }, text);
  const field = (label, control, hint) => el('label', { class: 'field' }, el('span', {}, label), control, hint ? el('small', {}, hint) : null);
  const input = (name, value, attributes = {}) => el('input', { class: 'input', name, value, ...attributes });
  const detailGroup = detail => detail && typeof detail.group === 'object' && detail.group !== null ? detail.group : null;
  function configPayload(group) {
    return Object.fromEntries(fields.map(key => [key, group[key]]));
  }
  function errorText(error) { return error?.message || '操作未完成，请稍后重试。'; }

  window.helperGalleryAdmin = function helperGalleryAdmin(container, { request } = {}) {
    if (!(container instanceof Element) || typeof request !== 'function') throw new TypeError('Gallery administration requires a container and request function');
    const state = { keys: [], groups: [], timezone: 'Asia/Shanghai', groupID: null, saved: null, detail: null,
      dirty: false, busy: false, pendingEnabled: null, epoch: 0, destroyed: false, poll: null, polls: 0, viewer: null, controls: null };
    const comparison = { data: null, loaded: false, dirty: false, busy: false, pendingEnabled: null, revision: 0, poll: null, polls: 0, controls: null };
    let attached = container.isConnected;
    const detachObserver = new MutationObserver(() => {
      if (container.isConnected) attached = true;
      else if (attached) destroy();
    });
    detachObserver.observe(document.body, { childList: true, subtree: true });
    const attachTimeout = setTimeout(() => { if (!container.isConnected) destroy(); }, 60000);
    container.classList.add('helper-gallery-admin');
    const feedback = el('p', { class: 'helper-gallery-admin-feedback', role: 'status', 'aria-live': 'polite' });
    const select = el('select', { class: 'input', name: 'gallery_group_id', 'aria-label': '作品分组', disabled: true });
    const refresh = button('刷新分组', () => loadCatalog());
    const configuration = el('div', { class: 'helper-gallery-admin-config' });
    const gallery = el('div', { class: 'helper-gallery-admin-gallery' });
    const comparisonPanel = el('section', { class: 'card helper-gallery-comparison' },
      el('div', { class: 'card-header' }, el('div', {}, el('h2', {}, '同题对比'), el('p', {}, '选定多个分组，用同一条提示词生成一轮作品。'))));
    const comparisonBody = el('div', { class: 'card-body' }); comparisonPanel.append(comparisonBody);
    container.replaceChildren(comparisonPanel, el('section', { class: 'card' },
      el('div', { class: 'card-header' }, el('div', {}, el('h2', {}, '分组模型设置'), el('p', {}, '为各分组选择模型与 API Key，再加入上方的同题对比。')), refresh),
      el('div', { class: 'card-body' }, field('作品分组', select, '使用管理员普通用户身份下已有的分组 API Key。'), feedback, configuration)), gallery);

    function say(text, kind = '') { feedback.textContent = text; feedback.className = `helper-gallery-admin-feedback ${kind}`; }
    function current(epoch, groupID = state.groupID) { return !state.destroyed && epoch === state.epoch && groupID === state.groupID; }
    function liveKeys(groupID = state.groupID) { return state.keys.filter(key => id(key.group_id) === groupID); }
    function liveKey(keyID, groupID = state.groupID) { return liveKeys(groupID).some(key => id(key.id) === id(keyID)); }
    function formatDate(value) {
      const date = new Date(value);
      if (!value || Number.isNaN(date.getTime())) return '等待调度';
      try { return date.toLocaleString('zh-CN', { timeZone: state.timezone, hour12: false }); }
      catch (_) { return date.toLocaleString('zh-CN', { hour12: false }); }
    }
    function stopPolling() { clearTimeout(state.poll); state.poll = null; state.polls = 0; }
    function destroy() { state.destroyed = true; state.epoch++; comparison.revision++; stopPolling(); clearTimeout(comparison.poll); clearTimeout(attachTimeout); detachObserver.disconnect(); state.viewer?.destroy?.(); }
    function comparisonMember() { return state.saved?.comparison_member === true || (comparison.data?.config?.group_ids || []).some(value => id(value) === state.groupID); }
    function syncControls() {
      const controls = state.controls;
      const busy = state.busy || comparison.busy;
      select.disabled = busy || !select.options.length;
      refresh.disabled = busy;
      syncComparison();
      if (!controls) return;
      const member = comparisonMember();
      controls.fieldset.disabled = busy;
      controls.save.disabled = busy || !liveKey(controls.key.value);
      controls.run.hidden = member; controls.switch.hidden = member; controls.promptField.hidden = member; controls.intervalField.hidden = member; controls.scheduleNote.hidden = member;
      controls.costNote.textContent = member ? '此处只修改分组的模型与 Key；同题提示词和生成间隔请在上方设置。' : '生成会实际调用所选模型并消耗该分组 Key 的额度。手动生成使用已保存的配置；暂停只停止后续定时生成。';
      controls.run.disabled = busy || member || !state.saved || state.dirty || !!state.saved.running || !liveKey(state.saved.key_id);
      controls.toggle.disabled = busy || member || !state.saved || (!state.saved.enabled && (state.dirty || !liveKey(state.saved.key_id)));
      controls.toggle.checked = typeof state.pendingEnabled === 'boolean' ? state.pendingEnabled : state.saved?.enabled === true;
      controls.toggleLabel.textContent = state.saved?.enabled ? '自动生成已开启' : '自动生成已暂停';
      controls.status.replaceChildren(
        el('span', { class: `pill ${state.saved?.running || member ? 'blue' : state.saved?.enabled ? 'green' : ''}` }, state.saved?.running ? '正在生成' : member ? '参与同题对比' : state.saved?.enabled ? '定时已开启' : '已暂停'),
        el('span', { class: 'caption' }, member ? '使用上方的统一提示词与定时设置，不再单独生成。' : state.saved?.enabled ? `下次生成：${formatDate(state.saved.next_at)} · ${state.timezone}` : '关闭定时后仍可手动生成一轮。'));
      controls.lastError.textContent = state.saved?.last_error ? `最近生成失败：${String(state.saved.last_error)}` : '';
      controls.keyHint.textContent = !liveKey(controls.key.value)
        ? '当前 Key 不可用。请选择有效 Key 后保存或启用；已开启的定时任务仍可直接暂停。'
        : state.dirty ? '有未保存的设置。保存后即可使用新配置生成。' : '密钥内容仅由服务端读取，不会发送到浏览器。';
    }
    function updateCatalogGroup(group) {
      const index = state.groups.findIndex(item => id(item.group_id) === state.groupID);
      const summary = { ...group, group_id: state.groupID };
      if (index < 0) state.groups.push(summary); else state.groups[index] = summary;
    }
    function comparisonRunning() { return comparison.data?.config?.running === true || comparison.data?.round?.status === 'running'; }
    function comparisonEligible(groupID) {
      const group = state.groups.find(item => id(item.group_id) === groupID);
      return !!group && typeof group.model === 'string' && !!group.model.trim() && liveKey(group.key_id, groupID);
    }
    function chosenComparisonGroups() { return [...(comparison.controls?.choices.querySelectorAll('input:checked') || [])].map(control => id(control.value)).filter(Boolean); }
    function comparisonSay(text, kind = '') {
      const target = comparison.controls?.feedback;
      if (target) { target.textContent = text; target.className = `helper-gallery-admin-feedback ${kind}`; }
    }
    function comparisonChoices(selected = chosenComparisonGroups()) {
      const controls = comparison.controls; if (!controls) return;
      const choices = new Map(state.groups.map(group => [id(group.group_id), group]));
      for (const groupID of selected) if (!choices.has(groupID)) choices.set(groupID, { group_id: groupID, name: `分组 #${groupID}` });
      controls.choices.replaceChildren(...[...choices].map(([groupID, group]) => {
        const checkbox = input('gallery_comparison_group_ids', groupID, { type: 'checkbox', checked: selected.includes(groupID) }); checkbox.className = '';
        return el('label', { class: 'check-chip' }, checkbox, el('span', {}, `${group.name || `分组 #${groupID}`} · ${group.model || '尚未配置模型'}${comparisonEligible(groupID) ? '' : '（当前不可用）'}`));
      }));
      syncComparison();
    }
    function syncComparison() {
      const controls = comparison.controls; if (!controls) return;
      const groups = chosenComparisonGroups(), valid = groups.length >= 2 && groups.length <= 32 && groups.every(comparisonEligible);
      const config = comparison.data?.config, busy = comparison.busy || state.busy;
      controls.fields.disabled = busy || !comparison.loaded;
      for (const checkbox of controls.choices.querySelectorAll('input')) checkbox.disabled = !comparisonEligible(id(checkbox.value)) && !checkbox.checked;
      controls.save.disabled = busy || !comparison.loaded || !valid;
      controls.run.disabled = busy || !config || comparison.dirty || comparisonRunning() || !(config.group_ids || []).every(value => comparisonEligible(id(value)));
      controls.clear.hidden = !config; controls.clear.disabled = busy;
      controls.toggle.disabled = busy || !config || (!config.enabled && (comparison.dirty || !(config.group_ids || []).every(value => comparisonEligible(id(value)))));
      controls.toggle.checked = typeof comparison.pendingEnabled === 'boolean' ? comparison.pendingEnabled : config?.enabled === true;
      controls.toggleLabel.textContent = config?.enabled ? '同题定时已开启' : '同题定时已暂停';
      controls.count.textContent = `已选 ${groups.length} 个分组 · 至少选择 2 个已配置模型和有效 Key 的分组。`;
      const round = comparison.data?.round, items = Array.isArray(round?.items) ? round.items : [];
      const finished = items.filter(item => ['success', 'failed', 'interrupted'].includes(item.status)).length;
      controls.status.replaceChildren(el('span', { class: `pill ${comparisonRunning() ? 'blue' : config?.enabled ? 'green' : ''}` }, comparisonRunning() ? '本轮正在生成' : config?.enabled ? '同题定时已开启' : '同题定时已暂停'),
        el('span', { class: 'caption' }, config?.enabled ? `下次同题对比：${formatDate(config.next_at)}` : '暂停仅停止后续定时轮次，当前一轮会继续完成。'));
      controls.round.replaceChildren(...(round ? [el('p', { class: 'caption' }, `最近一轮 · 已完成 ${finished} / ${items.length}`), el('div', { class: 'helper-gallery-comparison-items' }, items.map(item => {
        const names = { queued: '等待生成', running: '正在生成', success: '已完成', failed: '生成失败', interrupted: '已中断' };
        return el('div', { class: 'helper-gallery-comparison-item' }, el('span', {}, `${item.name || `分组 #${item.group_id}`} · ${item.model || '未设置模型'}`), el('span', { class: `pill ${item.status === 'success' ? 'green' : ['failed', 'interrupted'].includes(item.status) ? 'red' : ''}` }, names[item.status] || '等待结果'));
      }))] : []));
    }
    function drawComparison() {
      const config = comparison.data?.config;
      const prompt = el('textarea', { class: 'input', name: 'gallery_comparison_prompt', rows: 5, required: true, maxlength: 8000 }, config?.prompt || DEFAULT_PROMPT);
      const interval = input('gallery_comparison_interval', config?.interval_minutes ?? 30, { type: 'number', min: 5, max: 1440, step: 1, required: true });
      const choices = el('div', { class: 'helper-gallery-comparison-groups' });
      const count = el('p', { class: 'caption' });
      const save = el('button', { type: 'submit', class: 'button primary' }, '保存同题设置');
      const run = button('同题生成一轮', () => runComparison());
      const clear = button('退出同题配置', async () => {
        if (!comparison.data?.config || comparison.busy || state.busy || !confirm('退出同题配置？保留作品，分组恢复原本的独立定时设置。')) return;
        await mutateComparison('DELETE', '', undefined, '已退出同题配置，作品已保留。');
      }, 'button ghost');
      const toggle = input('gallery_comparison_enabled', '', { type: 'checkbox', checked: config?.enabled === true }); toggle.className = '';
      const toggleLabel = el('span'); toggle.addEventListener('change', () => toggleComparison(toggle.checked));
      const feedback = el('p', { class: 'helper-gallery-admin-feedback', role: 'status', 'aria-live': 'polite' });
      const status = el('div', { class: 'helper-gallery-admin-status', role: 'status' }), round = el('div', { class: 'helper-gallery-comparison-round' });
      const fields = el('fieldset', { class: 'helper-gallery-admin-fields' }, el('div', { class: 'field' }, el('span', {}, '参与分组'), choices), count,
        field('统一提示词', prompt, '所有选中的分组收到完全相同的提示词，并保留各自的模型与 Key。此提示词会随作品向用户展示。'),
        field('同题生成间隔（分钟）', interval, '默认 30 分钟；首次保存保持暂停。'),
        el('div', { class: 'helper-gallery-admin-actions' }, el('div', { class: 'button-group' }, save, run, clear), el('label', { class: 'switch' }, toggle, toggleLabel)),
        el('p', { class: 'caption' }, '每轮会调用所选分组的模型并消耗额度。智商检测菜单上线后才执行定时生成；参与分组的单独定时和手动生成会停用。'));
      const form = el('form', { class: 'helper-gallery-comparison-form' }, status, fields, feedback, round);
      const changed = event => { if (event.target !== toggle) { comparison.dirty = true; syncComparison(); } };
      form.addEventListener('input', changed); form.addEventListener('change', changed);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (comparison.busy || state.busy) return;
        const group_ids = chosenComparisonGroups(), text = prompt.value.trim(), minutes = Number(interval.value);
        if (group_ids.length < 2 || group_ids.length > 32 || !group_ids.every(comparisonEligible) || !text || new TextEncoder().encode(text).length > 16000 || !Number.isInteger(minutes) || minutes < 5 || minutes > 1440) {
          comparisonSay('请选择至少两个有效分组，填写提示词，并将间隔设置为 5～1440 的整数分钟。', 'error'); return;
        }
        await mutateComparison('PUT', '', { group_ids, prompt: text, interval_minutes: minutes, enabled: comparison.data?.config?.enabled === true }, '同题设置已保存。');
      });
      comparison.controls = { choices, count, prompt, interval, save, run, clear, toggle, toggleLabel, feedback, status, round, fields };
      comparison.dirty = false; comparisonBody.replaceChildren(form); comparisonChoices((config?.group_ids || []).map(id).filter(Boolean));
    }
    function acceptComparison(data, replaceForm) {
      if (!data || typeof data !== 'object' || !Object.hasOwn(data, 'config') || (data.config !== null && (typeof data.config !== 'object' || !Array.isArray(data.config.group_ids)))) throw new Error('同题对比响应不兼容，请刷新后重试。');
      comparison.data = data; comparison.loaded = true;
      if (replaceForm || !comparison.controls) drawComparison(); else syncComparison();
      syncControls();
    }
    async function readComparison(revision, replaceForm = false) {
      const data = await request('/gallery/comparison');
      if (!state.destroyed && revision === comparison.revision) acceptComparison(data, replaceForm);
    }
    async function updateMemberDetail() {
      if (state.saved) await readDetail(state.epoch, false);
    }
    async function mutateComparison(method, suffix, body, message) {
      if (comparison.busy || state.busy || !comparison.loaded) return;
      const revision = ++comparison.revision;
      comparison.busy = true; comparisonSay('正在保存同题设置…'); syncControls();
      try {
        await request(`/gallery/comparison${suffix}`, method, body);
        if (state.destroyed || revision !== comparison.revision) return;
        await readComparison(revision, true); await updateMemberDetail();
        comparisonSay(message, 'success'); await refreshGallery();
      } catch (error) { if (!state.destroyed && revision === comparison.revision) comparisonSay(errorText(error), 'error'); }
      finally { if (!state.destroyed && revision === comparison.revision) { comparison.busy = false; syncControls(); if (comparisonRunning()) pollComparison(revision); } }
    }
    async function toggleComparison(enabled) {
      if (!comparison.data?.config || comparison.busy || state.busy) return;
      comparison.pendingEnabled = enabled;
      try {
        if (!enabled) await mutateComparison('POST', '/pause', {}, '同题定时已暂停，当前一轮会继续完成。');
        else if (!comparison.dirty) {
          const config = comparison.data.config;
          await mutateComparison('PUT', '', { group_ids: config.group_ids, prompt: config.prompt, interval_minutes: config.interval_minutes, enabled: true }, '同题定时已开启。');
        }
      } finally { comparison.pendingEnabled = null; syncControls(); }
    }
    function pollComparison(revision) {
      clearTimeout(comparison.poll);
      comparison.poll = setTimeout(async () => {
        if (state.destroyed || !container.isConnected || revision !== comparison.revision) return;
        try {
          await readComparison(revision);
          if (state.destroyed || revision !== comparison.revision) return;
          if (comparisonRunning()) {
            if (comparison.polls++ < 180) pollComparison(revision);
            else comparisonSay('本轮仍在生成，可刷新分组继续查看进度。');
          } else {
            const status = comparison.data?.round?.status;
            comparisonSay('本轮同题对比已结束，可在下方查看各分组的作品。', ['failed', 'interrupted'].includes(status) ? 'error' : status === 'partial' ? '' : 'success');
            await updateMemberDetail(); await refreshGallery();
          }
        } catch (error) { if (!state.destroyed && revision === comparison.revision) comparisonSay(errorText(error), 'error'); }
      }, 2000);
    }
    async function runComparison() {
      const config = comparison.data?.config;
      if (comparison.busy || state.busy || !config || comparison.dirty || comparisonRunning() || !(config.group_ids || []).every(value => comparisonEligible(id(value)))) return;
      const revision = ++comparison.revision; comparison.polls = 0; comparison.busy = true; comparisonSay('正在提交同题生成任务…'); syncControls();
      try {
        await request('/gallery/comparison/run', 'POST', {});
        if (state.destroyed || revision !== comparison.revision) return;
        await readComparison(revision); comparisonSay('本轮已提交，各分组将使用同一条提示词生成。', 'success'); pollComparison(revision);
      } catch (error) { if (!state.destroyed && revision === comparison.revision) comparisonSay(errorText(error), 'error'); }
      finally { if (!state.destroyed && revision === comparison.revision) { comparison.busy = false; syncControls(); } }
    }
    drawComparison();
    async function refreshGallery() {
      await state.viewer?.refresh?.();
      if (state.saved) await state.viewer?.selectGroup?.(state.groupID);
    }
    function defaultGroup(groupID) {
      const key = liveKeys(groupID)[0];
      return { group_id: groupID, name: key?.group_name || `分组 #${groupID}`, model: '', key_id: id(key?.id),
        api_format: 'chat', interval_minutes: 30, enabled: false, prompt: DEFAULT_PROMPT };
    }
    function drawConfiguration(group, saved) {
      state.controls = null; state.saved = saved ? group : null; state.dirty = false;
      const name = input('gallery_name', group.name || '', { required: true, maxlength: 100 });
      const model = input('gallery_model', group.model || '', { required: true, maxlength: 150, placeholder: '例如 gpt-4.1' });
      const key = el('select', { class: 'input', name: 'gallery_key_id', required: true });
      for (const option of liveKeys()) key.append(el('option', { value: id(option.id) }, `${option.name || 'API Key'} · #${id(option.id)}`));
      if (!liveKey(group.key_id) && id(group.key_id)) key.prepend(el('option', { value: id(group.key_id), disabled: true }, `原 Key #${id(group.key_id)}（当前不可用）`));
      if (id(group.key_id)) key.value = String(id(group.key_id));
      if (!key.options.length) key.append(el('option', { value: '', disabled: true }, '暂无可用的分组 Key'));
      const keyHint = el('small', { class: 'helper-gallery-admin-key-hint' });
      const format = el('select', { class: 'input', name: 'gallery_api_format' }, el('option', { value: 'chat' }, 'Chat Completions'), el('option', { value: 'responses' }, 'Responses'));
      format.value = group.api_format === 'responses' ? 'responses' : 'chat';
      const interval = input('gallery_interval_minutes', group.interval_minutes ?? 30, { type: 'number', min: 5, max: 1440, step: 1, required: true });
      const prompt = el('textarea', { class: 'input', name: 'gallery_prompt', rows: 7, required: true, maxlength: 8000 }, group.prompt || '');
      const save = el('button', { type: 'submit', class: 'button primary' }, '保存配置');
      const run = button('生成一轮', () => runOnce());
      const toggle = input('gallery_enabled', '', { type: 'checkbox', checked: saved && group.enabled === true });
      toggle.className = '';
      const toggleLabel = el('span');
      toggle.addEventListener('change', () => toggleSchedule(toggle.checked));
      const status = el('div', { class: 'helper-gallery-admin-status', role: 'status' });
      const lastError = el('p', { class: 'helper-gallery-admin-error', role: 'status' });
      const intervalField = field('生成间隔（分钟）', interval, '默认 30 分钟，可设置 5～1440 分钟。');
      const promptField = field('创作提示词', prompt, '可要求模型返回完整 HTML 或 SVG 源码。此提示词及生成作品会向该分组的用户展示，预览在隔离环境中打开。');
      const scheduleNote = el('p', { class: 'caption' }, '智商检测菜单上线后才执行定时生成。');
      const switchControl = el('label', { class: 'switch' }, toggle, toggleLabel), costNote = el('p', { class: 'caption' });
      const fieldset = el('fieldset', { class: 'helper-gallery-admin-fields' },
        el('div', { class: 'helper-gallery-admin-grid' }, field('展示名称', name), field('模型', model), field('调用 API Key', key),
          field('接口格式', format), intervalField), keyHint, promptField,
        el('div', { class: 'helper-gallery-admin-actions' }, el('div', { class: 'button-group' }, save, run), switchControl), scheduleNote, costNote);
      const form = el('form', { class: 'helper-gallery-admin-form' }, status, lastError, fieldset);
      form.addEventListener('input', event => { if (event.target !== toggle) { state.dirty = true; syncControls(); } });
      form.addEventListener('change', event => { if (event.target !== toggle) { state.dirty = true; syncControls(); } });
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (state.busy || !liveKey(key.value)) return;
        const payload = { name: name.value.trim(), model: model.value.trim(), key_id: id(key.value), api_format: format.value,
          interval_minutes: Number(interval.value), enabled: state.saved?.enabled === true, prompt: prompt.value.trim() };
        if (!payload.name || !payload.model || !payload.prompt || !Number.isInteger(payload.interval_minutes) || payload.interval_minutes < 5 || payload.interval_minutes > 1440) {
          say('请填写名称、模型、提示词，并将间隔设置为 5～1440 的整数分钟。', 'error'); return;
        }
        const bytes = new TextEncoder();
        if (bytes.encode(payload.name).length > 120 || bytes.encode(payload.model).length > 150 || bytes.encode(payload.prompt).length > 16000) {
          say('名称、模型名称或提示词过长，请缩短后保存。', 'error'); return;
        }
        await mutate('PUT', '', payload, '配置已保存。');
      });
      state.controls = { fieldset, name, model, key, format, interval, prompt, save, run, toggle, toggleLabel, status, lastError, keyHint,
        intervalField, promptField, scheduleNote, switch: switchControl, costNote };
      configuration.replaceChildren(form); syncControls();
    }
    async function readDetail(epoch, replaceForm = false) {
      const groupID = state.groupID;
      const detail = await request(`/gallery/${groupID}`);
      if (!current(epoch, groupID)) return null;
      const group = detailGroup(detail);
      if (!group || id(group.group_id) !== groupID) throw new Error('分组配置响应不兼容，请刷新后重试。');
      state.saved = group; state.detail = detail; updateCatalogGroup(group);
      if (replaceForm) drawConfiguration(group, true); else syncControls();
      return detail;
    }
    async function mutate(method, suffix, body, success) {
      if (state.busy || comparison.busy || !state.groupID) return;
      const epoch = state.epoch, groupID = state.groupID;
      state.busy = true; say('正在保存…'); syncControls();
      try {
        await request(`/gallery/${groupID}${suffix}`, method, body);
        if (!current(epoch, groupID)) return;
        await readDetail(epoch, true);
        comparisonChoices();
        say(success, 'success');
        await refreshGallery();
      } catch (error) { if (current(epoch, groupID)) say(errorText(error), 'error'); }
      finally { if (current(epoch, groupID)) { state.busy = false; syncControls(); } }
    }
    async function toggleSchedule(enabled) {
      if (state.busy || comparison.busy || !state.saved || comparisonMember()) return;
      state.pendingEnabled = enabled;
      try {
        if (!enabled) await mutate('POST', '/pause', {}, '自动生成已暂停。');
        else if (!state.dirty && liveKey(state.saved.key_id)) await mutate('PUT', '', { ...configPayload(state.saved), enabled: true }, '自动生成已开启。');
      } finally { state.pendingEnabled = null; syncControls(); }
    }
    function pollGeneration(epoch, groupID) {
      clearTimeout(state.poll);
      state.poll = setTimeout(async () => {
        if (!current(epoch, groupID) || !container.isConnected) return;
        try {
          await readDetail(epoch);
          if (!current(epoch, groupID)) return;
          if (state.saved?.running) {
            if (state.polls++ < 180) pollGeneration(epoch, groupID);
            else say('本轮仍在生成，可点击刷新分组继续查看进度。');
          } else { say(state.saved?.last_error ? `生成失败：${String(state.saved.last_error)}` : '本轮生成已结束，可在下方查看作品。', state.saved?.last_error ? 'error' : 'success'); await refreshGallery(); }
        } catch (error) { if (current(epoch, groupID)) say(`${errorText(error)} 可点击刷新分组查看执行结果。`, 'error'); }
      }, 2000);
    }
    async function runOnce() {
      if (state.busy || comparison.busy || comparisonMember() || !state.saved || state.dirty || !liveKey(state.saved.key_id) || state.saved.running) return;
      const epoch = state.epoch, groupID = state.groupID;
      state.busy = true; say('正在提交生成任务…'); syncControls();
      try {
        await request(`/gallery/${groupID}/run`, 'POST', {});
        if (!current(epoch, groupID)) return;
        say('任务已提交，正在等待生成结果…', 'success');
        await readDetail(epoch);
        pollGeneration(epoch, groupID);
      } catch (error) { if (current(epoch, groupID)) say(errorText(error), 'error'); }
      finally { if (current(epoch, groupID)) { state.busy = false; syncControls(); } }
    }
    async function chooseGroup() {
      stopPolling(); state.epoch++; state.groupID = id(select.value); state.saved = null; state.detail = null; state.dirty = false; state.controls = null;
      const epoch = state.epoch;
      configuration.replaceChildren(); say('');
      if (!state.groupID) { configuration.append(el('p', { class: 'caption' }, '暂无分组配置或可用的分组 Key，请先在 sub2api 创建后刷新。')); return; }
      const summary = state.groups.find(group => id(group.group_id) === state.groupID);
      if (!summary) { drawConfiguration(defaultGroup(state.groupID), false); return; }
      configuration.append(el('p', { class: 'caption', role: 'status' }, '正在读取分组配置…'));
      try {
        await readDetail(epoch, true);
        await state.viewer?.selectGroup?.(state.groupID);
        if (current(epoch) && !comparisonMember() && state.saved.running) pollGeneration(epoch, state.groupID);
      } catch (error) { if (current(epoch)) { configuration.replaceChildren(); say(errorText(error), 'error'); } }
    }
    select.addEventListener('change', chooseGroup);
    async function loadCatalog() {
      if (state.busy || comparison.busy || state.destroyed) return;
      const previous = state.groupID;
      const revision = ++comparison.revision; clearTimeout(comparison.poll);
      state.busy = true; syncControls(); say('正在读取分组和可用 Key…');
      const results = await Promise.allSettled([request('/gallery'), request('/benchmark/options'), request('/gallery/comparison')]);
      if (state.destroyed) return;
      const [catalog, options, shared] = results;
      if (shared.status === 'fulfilled') {
        try { acceptComparison(shared.value, true); }
        catch (error) { comparison.loaded = false; syncComparison(); comparisonSay(errorText(error), 'error'); }
      } else comparisonSay(`同题设置读取失败：${errorText(shared.reason)} 请刷新分组重试。`, 'error');
      if (catalog.status === 'fulfilled' && Array.isArray(catalog.value?.groups)) {
        state.groups = catalog.value.groups.filter(group => id(group?.group_id));
        state.timezone = typeof catalog.value.timezone === 'string' ? catalog.value.timezone : 'Asia/Shanghai';
      } else {
        state.busy = false; syncControls();
        say(catalog.status === 'rejected' ? errorText(catalog.reason) : '作品分组列表响应不兼容。', 'error');
        return;
      }
      state.keys = options.status === 'fulfilled' && Array.isArray(options.value?.keys)
        ? options.value.keys.filter(key => id(key?.id) && id(key?.group_id)) : [];
      comparisonChoices();
      const choices = new Map(state.groups.map(group => [id(group.group_id), group.name || `分组 #${id(group.group_id)}`]));
      for (const key of state.keys) if (!choices.has(id(key.group_id))) choices.set(id(key.group_id), key.group_name || `分组 #${id(key.group_id)}`);
      select.replaceChildren(...[...choices].map(([groupID, name]) => el('option', { value: groupID }, `${name} · #${groupID}`)));
      if (choices.has(previous)) select.value = String(previous);
      state.busy = false; refresh.disabled = false; select.disabled = !choices.size;
      await chooseGroup();
      syncControls();
      if (comparisonRunning()) pollComparison(revision);
      if (options.status === 'rejected') say(`可用 Key 读取失败：${errorText(options.reason)} 已保存配置仍可查看或暂停。`, 'error');
      else if (catalog.status === 'rejected') say(errorText(catalog.reason), 'error');
      if (!state.viewer && typeof window.helperGallery === 'function') {
        state.viewer = window.helperGallery(gallery, { request, timezone: state.timezone, groupId: state.groupID });
        await state.viewer.ready;
        if (!state.destroyed && state.saved) await state.viewer.selectGroup?.(state.groupID);
      }
    }
    loadCatalog().catch(error => { if (!state.destroyed) { state.busy = false; say(errorText(error), 'error'); syncControls(); } });
    return { refresh: loadCatalog, destroy };
  };
})();

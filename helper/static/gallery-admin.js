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
    container.replaceChildren(el('section', { class: 'card' },
      el('div', { class: 'card-header' }, el('div', {}, el('h2', {}, '分组作品生成'), el('p', {}, '为分组保存模型与提示词，按间隔生成 HTML 或 SVG 作品。')), refresh),
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
    function destroy() { state.destroyed = true; state.epoch++; stopPolling(); clearTimeout(attachTimeout); detachObserver.disconnect(); state.viewer?.destroy?.(); }
    function syncControls() {
      const controls = state.controls;
      select.disabled = state.busy || !select.options.length;
      refresh.disabled = state.busy;
      if (!controls) return;
      controls.fieldset.disabled = state.busy;
      controls.save.disabled = state.busy || !liveKey(controls.key.value);
      controls.run.disabled = state.busy || !state.saved || state.dirty || !!state.saved.running || !liveKey(state.saved.key_id);
      controls.toggle.disabled = state.busy || !state.saved || (!state.saved.enabled && (state.dirty || !liveKey(state.saved.key_id)));
      controls.toggle.checked = typeof state.pendingEnabled === 'boolean' ? state.pendingEnabled : state.saved?.enabled === true;
      controls.toggleLabel.textContent = state.saved?.enabled ? '自动生成已开启' : '自动生成已暂停';
      controls.status.replaceChildren(
        el('span', { class: `pill ${state.saved?.running ? 'blue' : state.saved?.enabled ? 'green' : ''}` }, state.saved?.running ? '正在生成' : state.saved?.enabled ? '定时已开启' : '已暂停'),
        el('span', { class: 'caption' }, state.saved?.enabled ? `下次生成：${formatDate(state.saved.next_at)} · ${state.timezone}` : '关闭定时后仍可手动生成一轮。'));
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
      const fieldset = el('fieldset', { class: 'helper-gallery-admin-fields' },
        el('div', { class: 'helper-gallery-admin-grid' }, field('展示名称', name), field('模型', model), field('调用 API Key', key),
          field('接口格式', format), field('生成间隔（分钟）', interval, '默认 30 分钟，可设置 5～1440 分钟。')),
        keyHint, field('创作提示词', prompt, '可要求模型返回完整 HTML 或 SVG 源码。此提示词及生成作品会向该分组的用户展示，预览在隔离环境中打开。'),
        el('div', { class: 'helper-gallery-admin-actions' }, el('div', { class: 'button-group' }, save, run), el('label', { class: 'switch' }, toggle, toggleLabel)),
        el('p', { class: 'caption' }, '智商检测菜单上线后才执行定时生成。'),
        el('p', { class: 'caption' }, '生成会实际调用所选模型并消耗该分组 Key 的额度。手动生成使用已保存的配置；暂停只停止后续定时生成。'));
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
      state.controls = { fieldset, name, model, key, format, interval, prompt, save, run, toggle, toggleLabel, status, lastError, keyHint };
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
      if (state.busy || !state.groupID) return;
      const epoch = state.epoch, groupID = state.groupID;
      state.busy = true; say('正在保存…'); syncControls();
      try {
        await request(`/gallery/${groupID}${suffix}`, method, body);
        if (!current(epoch, groupID)) return;
        await readDetail(epoch, true);
        say(success, 'success');
        await refreshGallery();
      } catch (error) { if (current(epoch, groupID)) say(errorText(error), 'error'); }
      finally { if (current(epoch, groupID)) { state.busy = false; syncControls(); } }
    }
    async function toggleSchedule(enabled) {
      if (state.busy || !state.saved) return;
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
          if (state.saved?.running && state.polls++ < 120) pollGeneration(epoch, groupID);
          else { say(state.saved?.last_error ? `生成失败：${String(state.saved.last_error)}` : '本轮生成已结束，可在下方查看作品。', state.saved?.last_error ? 'error' : 'success'); await refreshGallery(); }
        } catch (error) { if (current(epoch, groupID)) say(`${errorText(error)} 可点击刷新分组查看执行结果。`, 'error'); }
      }, 2000);
    }
    async function runOnce() {
      if (state.busy || !state.saved || state.dirty || !liveKey(state.saved.key_id) || state.saved.running) return;
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
        if (current(epoch) && state.saved.running) pollGeneration(epoch, state.groupID);
      } catch (error) { if (current(epoch)) { configuration.replaceChildren(); say(errorText(error), 'error'); } }
    }
    select.addEventListener('change', chooseGroup);
    async function loadCatalog() {
      if (state.busy || state.destroyed) return;
      const previous = state.groupID;
      state.busy = true; syncControls(); say('正在读取分组和可用 Key…');
      const results = await Promise.allSettled([request('/gallery'), request('/benchmark/options')]);
      if (state.destroyed) return;
      const [catalog, options] = results;
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
      const choices = new Map(state.groups.map(group => [id(group.group_id), group.name || `分组 #${id(group.group_id)}`]));
      for (const key of state.keys) if (!choices.has(id(key.group_id))) choices.set(id(key.group_id), key.group_name || `分组 #${id(key.group_id)}`);
      select.replaceChildren(...[...choices].map(([groupID, name]) => el('option', { value: groupID }, `${name} · #${groupID}`)));
      if (choices.has(previous)) select.value = String(previous);
      state.busy = false; refresh.disabled = false; select.disabled = !choices.size;
      await chooseGroup();
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

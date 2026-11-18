/* Group gallery administration. Browser receives selectable key IDs, never keys. */
'use strict';
(() => {
  const DEFAULT_PROMPT = '请创作一幅完整的 SVG 作品。只输出 SVG 源码，不要解释或 Markdown 代码块。';
  const fields = ['name', 'model', 'key_id', 'api_format', 'reasoning_effort', 'interval_minutes', 'enabled', 'prompt'];
  const EFFORTS = { default: '默认', none: '无', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '超高', max: '最高' };
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
  const icon = name => typeof window.helperIcon === 'function' ? window.helperIcon(name, 'helper-gallery-admin-icon') : null;
  const button = (text, action, classes = 'button', symbol) => el('button', { type: 'button', class: classes, onclick: action }, symbol ? icon(symbol) : null, el('span', {}, text));
  const toggleControl = (control, caption) => el('label', { class: 'helper-switch' }, control,
    el('span', { class: 'switch', 'aria-hidden': 'true' }, el('span', { class: 'switch-thumb' })), caption);
  const field = (label, control, hint) => el('label', { class: 'field' }, el('span', {}, label), control, hint ? el('small', {}, hint) : null);
  const input = (name, value, attributes = {}) => el('input', { class: 'input', name, value, ...attributes });
  const detailGroup = detail => detail && typeof detail.group === 'object' && detail.group !== null ? detail.group : null;
  function configPayload(group) {
    return Object.fromEntries(fields.map(key => [key, key === 'reasoning_effort' ? group[key] || 'default' : group[key]]));
  }
  function errorText(error) { return error?.message || '操作未完成，请稍后重试。'; }

  window.helperGalleryAdmin = function helperGalleryAdmin(container, { request } = {}) {
    if (!(container instanceof Element) || typeof request !== 'function') throw new TypeError('Gallery administration requires a container and request function');
    const state = { keys: [], optionsGroups: [], groups: [], timezone: 'Asia/Shanghai', groupID: null, saved: null, detail: null,
      catalogLoaded: false, catalogError: null, optionsLoaded: false, groupError: null, keyError: null, editorOpen: false, adding: false,
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
    const select = el('select', { class: 'input', name: 'gallery_group_id', 'aria-label': '选择要添加的分组', disabled: true });
    const refresh = button('刷新分组', () => loadCatalog(), 'button', 'refresh');
    const add = button('新增分组', () => openAdd(), 'button primary', 'plus');
    const groupCount = el('span', { class: 'caption' });
    const list = el('div', { class: 'helper-gallery-admin-list', role: 'list', 'aria-label': '已配置分组' });
    const optionsFeedback = el('p', { class: 'helper-gallery-admin-options', role: 'status' });
    const editorFeedback = el('p', { class: 'helper-gallery-admin-feedback', role: 'status', 'aria-live': 'polite' });
    const editorTitle = el('h3', {}, '新增分组');
    const editorClose = button('关闭编辑', () => closeEditor(), 'button ghost', 'x');
    const picker = field('上游分组', select, '选择官方分组目录中的分组。没有可用 API Key 时可查看，创建或启用对应分组 Key 后才能保存。');
    const configuration = el('div', { class: 'helper-gallery-admin-config' });
    const editor = el('section', { class: 'card helper-gallery-admin-editor', hidden: true },
      el('div', { class: 'card-header' }, editorTitle, editorClose), el('div', { class: 'card-body' }, picker, editorFeedback, configuration));
    const gallery = el('div', { class: 'helper-gallery-admin-gallery' });
    const comparisonSummary = el('span', { class: 'caption' });
    const comparisonPanel = el('details', { class: 'card helper-gallery-comparison' },
      el('summary', { class: 'card-header' }, el('div', {}, el('h2', {}, '同题对比'), comparisonSummary), icon('chevronRight')));
    const comparisonBody = el('div', { class: 'card-body' }); comparisonPanel.append(comparisonBody);
    const previewPanel = el('details', { class: 'card helper-gallery-admin-preview' },
      el('summary', { class: 'card-header' }, el('h2', {}, '预览作品'), icon('chevronRight')), el('div', { class: 'card-body' }, gallery));
    previewPanel.addEventListener('toggle', () => { if (previewPanel.open) ensureViewer(); });
    container.replaceChildren(el('section', { class: 'card helper-gallery-admin-catalog' },
      el('div', { class: 'card-header' }, el('div', {}, el('h2', {}, '已配置分组'), groupCount), el('div', { class: 'button-group' }, refresh, add)),
      el('div', { class: 'card-body' }, feedback, optionsFeedback, list)), editor, comparisonPanel, previewPanel);
    list.append(el('p', { class: 'caption' }, '正在读取已保存分组…'));

    function say(text, kind = '') {
      const target = state.editorOpen ? editorFeedback : feedback;
      target.textContent = text; target.className = `helper-gallery-admin-feedback ${kind}`;
    }
    function current(epoch, groupID = state.groupID) { return !state.destroyed && epoch === state.epoch && groupID === state.groupID; }
    function liveKeys(groupID = state.groupID) { return state.keyError ? [] : state.keys.filter(key => id(key.group_id) === groupID); }
    function liveKey(keyID, groupID = state.groupID) { return liveKeys(groupID).some(key => id(key.id) === id(keyID)); }
    function formatDate(value) {
      const date = new Date(value);
      if (!value || Number.isNaN(date.getTime())) return '等待调度';
      try { return date.toLocaleString('zh-CN', { timeZone: state.timezone, hour12: false }); }
      catch (_) { return date.toLocaleString('zh-CN', { hour12: false }); }
    }
    function availableGroups() { return state.optionsGroups.filter(group => !state.groups.some(saved => id(saved.group_id) === id(group.id))); }
    function effortLabel(value) { return EFFORTS[value || 'default'] || String(value); }
    function member(groupID) {
      return comparison.loaded ? (comparison.data?.config?.group_ids || []).some(value => id(value) === groupID)
        : state.groups.find(group => id(group.group_id) === groupID)?.comparison_member === true;
    }
    function openComparison() { comparisonPanel.open = true; comparisonPanel.scrollIntoView({ block: 'start' }); }
    function cell(label, ...children) { return el('div', { class: 'helper-gallery-admin-cell' }, el('span', { class: 'helper-gallery-admin-cell-label' }, label), ...children); }
    function renderRows() {
      if (!state.catalogLoaded) return;
      groupCount.textContent = `${state.groups.length} 个已配置分组`;
      const messages = [];
      if (state.groupError) messages.push(`分组目录暂不可用：${state.groupError}`);
      if (state.keyError) messages.push(`API Key 目录暂不可用：${state.keyError}。已有配置仍可查看和暂停。`);
      optionsFeedback.textContent = messages.join(' ');
      if (!state.groups.length) {
        const reason = state.groupError ? '上游分组目录暂不可用，请刷新后添加。' : state.keyError ? 'API Key 目录暂不可用，请刷新后确认可用的分组 Key。'
          : state.optionsLoaded && state.optionsGroups.length && !state.keys.length ? `已读取 ${state.optionsGroups.length} 个上游分组，尚无可用的分组 API Key。请在 sub2api 管理员普通用户的 API 密钥页创建或启用对应分组 Key。`
          : state.optionsLoaded && !state.optionsGroups.length ? '上游尚无分组，请先在 sub2api 创建分组。'
          : '点击“新增分组”，选择上游分组并保存模型设置。';
        list.replaceChildren(el('div', { class: 'helper-gallery-admin-empty' }, icon('inbox'), el('strong', {}, '还没有配置模型分组'), el('p', {}, reason)));
        return;
      }
      const header = el('div', { class: 'helper-gallery-admin-list-head', 'aria-hidden': 'true' }, ['分组', '模型', 'API Key', '思考强度', '状态', '操作'].map(text => el('span', {}, text)));
      const rows = state.groups.map(group => {
        const groupID = id(group.group_id), key = state.keys.find(item => id(item.id) === id(group.key_id) && id(item.group_id) === groupID);
        const official = state.optionsGroups.find(item => id(item.id) === groupID);
        const unavailable = state.optionsLoaded && !state.keyError && !key;
        const status = el('div', { class: 'helper-gallery-admin-row-status' });
        if (group.running) status.append(el('span', { class: 'pill blue' }, '生成中'));
        if (member(groupID)) status.append(button(`同题${comparison.data?.config?.enabled ? '运行中' : '已暂停'}`, openComparison, 'button link'));
        else if (!group.running) status.append(el('span', { class: `pill ${group.enabled ? 'green' : ''}` }, group.enabled ? '独立定时' : '已暂停'));
        const edit = button('编辑', () => openEditor(groupID), 'button small', 'edit'); edit.setAttribute('aria-label', `编辑分组 ${group.name || groupID}`);
        const view = button('作品', () => showPreview(groupID), 'button small ghost', 'grid'); view.setAttribute('aria-label', `查看分组 ${group.name || groupID} 的作品`);
        const actions = el('div', { class: 'helper-gallery-admin-row-actions' }, edit, view);
        if (group.enabled && !member(groupID)) actions.append(button('暂停', () => pauseRow(groupID), 'button small ghost', 'clock'));
        return el('article', { class: 'helper-gallery-admin-row', role: 'listitem', 'data-gallery-group-id': groupID, 'data-editing': state.editorOpen && groupID === state.groupID },
          cell('分组', el('strong', {}, group.name || `分组 #${groupID}`), el('small', {}, `${official?.name || '上游分组'} · #${groupID}`)),
          cell('模型', el('span', { class: 'helper-gallery-admin-model' }, group.model || '未设置'), el('small', {}, group.api_format === 'responses' ? 'Responses' : 'Chat Completions')),
          cell('API Key', el('span', {}, key?.name || `Key #${group.key_id}`), unavailable ? el('small', { class: 'helper-gallery-admin-error' }, '当前不可用') : state.keyError ? el('small', {}, '名称暂不可用') : null),
          cell('思考强度', el('span', {}, effortLabel(group.reasoning_effort))), cell('状态', status), actions);
      });
      list.replaceChildren(header, ...rows);
      for (const control of list.querySelectorAll('button')) control.disabled = state.busy || comparison.busy;
    }
    function closeEditor(force = false) {
      if (!force && (state.busy || comparison.busy)) return false;
      if (!force && state.dirty && !confirm('放弃此分组尚未保存的修改？')) return false;
      stopPolling(); state.epoch++; state.groupID = null; state.saved = null; state.detail = null;
      state.editorOpen = false; state.adding = false; state.dirty = false; state.controls = null;
      editor.hidden = true; configuration.replaceChildren(); editorFeedback.textContent = ''; renderRows(); syncControls();
      return true;
    }
    function openAdd() {
      if (state.busy || comparison.busy || !state.catalogLoaded || state.catalogError || state.groupError || !availableGroups().length || !closeEditor()) return;
      state.editorOpen = true; state.adding = true; editor.hidden = false; picker.hidden = false; editorTitle.textContent = '新增分组';
      select.replaceChildren(el('option', { value: '' }, '选择要添加的分组'), ...availableGroups().map(group => el('option', { value: id(group.id) }, `${group.name || `分组 #${group.id}`} · #${group.id}`)));
      configuration.replaceChildren(el('p', { class: 'caption' }, '选择一个上游分组后，设置该组使用的模型、API Key 与思考强度。'));
      syncControls(); editor.scrollIntoView({ block: 'start' }); select.focus();
    }
    async function openEditor(groupID) {
      if (state.busy || comparison.busy) return;
      if (state.editorOpen && state.groupID === groupID) { editor.scrollIntoView({ block: 'start' }); return; }
      if (!closeEditor()) return;
      state.editorOpen = true; state.adding = false; state.groupID = groupID; editor.hidden = false; picker.hidden = true;
      editorTitle.textContent = `编辑分组 · ${state.groups.find(group => id(group.group_id) === groupID)?.name || `#${groupID}`}`;
      configuration.replaceChildren(el('p', { class: 'caption', role: 'status' }, '正在读取已保存的模型设置…'));
      const epoch = state.epoch; renderRows(); editor.scrollIntoView({ block: 'start' });
      try {
        await readDetail(epoch, true);
        if (current(epoch) && !comparisonMember() && state.saved.running) pollGeneration(epoch, groupID);
      } catch (error) { if (current(epoch)) { configuration.replaceChildren(); say(errorText(error), 'error'); } }
    }
    async function pauseRow(groupID) {
      if (state.busy || comparison.busy || member(groupID)) return;
      state.busy = true; syncControls();
      try {
        await request(`/gallery/${groupID}/pause`, 'POST', {});
        const detail = await request(`/gallery/${groupID}`);
        if (state.destroyed) return;
        const group = detailGroup(detail); if (!group || id(group.group_id) !== groupID) throw new Error('分组响应不兼容，请刷新后查看状态。');
        updateCatalogGroup(group);
        if (state.groupID === groupID) { state.saved = group; if (!state.dirty) drawConfiguration(group, true); }
        say('自动生成已暂停。', 'success');
      } catch (error) { if (!state.destroyed) say(errorText(error), 'error'); }
      finally { if (!state.destroyed) { state.busy = false; syncControls(); } }
    }
    async function ensureViewer() {
      if (!state.viewer && typeof window.helperGallery === 'function') state.viewer = window.helperGallery(gallery, { request, timezone: state.timezone });
      await state.viewer?.ready;
    }
    async function showPreview(groupID) {
      previewPanel.open = true; previewPanel.scrollIntoView({ block: 'start' }); await ensureViewer();
      if (!state.destroyed) state.viewer?.focusSource?.(groupID);
    }
    function stopPolling() { clearTimeout(state.poll); state.poll = null; state.polls = 0; }
    function destroy() { state.destroyed = true; state.epoch++; comparison.revision++; stopPolling(); clearTimeout(comparison.poll); clearTimeout(attachTimeout); detachObserver.disconnect(); state.viewer?.destroy?.(); }
    function comparisonMember() { return member(state.groupID); }
    function syncControls() {
      const controls = state.controls;
      const busy = state.busy || comparison.busy;
      select.disabled = busy || !select.options.length;
      refresh.disabled = busy;
      add.disabled = busy || !state.catalogLoaded || !!state.catalogError || !state.optionsLoaded || !!state.groupError || !availableGroups().length;
      editorClose.disabled = busy;
      for (const control of list.querySelectorAll('button')) control.disabled = busy;
      syncComparison();
      if (!controls) return;
      const member = comparisonMember();
      controls.fieldset.disabled = busy;
      controls.save.disabled = busy || !liveKey(controls.key.value) || (state.adding && (!!state.catalogError || state.groups.some(group => id(group.group_id) === state.groupID)));
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
      controls.keyHint.textContent = state.keyError ? 'API Key 目录暂不可用。请刷新后保存；已有定时任务仍可直接暂停。' : !liveKey(controls.key.value)
        ? '此分组尚无可用 API Key。请到 sub2api 管理员普通用户的 API 密钥页创建或启用对应分组 Key，再刷新。已有定时任务仍可暂停。'
        : state.dirty ? '有未保存的设置。保存后即可使用新配置生成。' : '密钥内容仅由服务端读取，不会发送到浏览器。';
    }
    function updateCatalogGroup(group) {
      const groupID = id(group.group_id) || state.groupID;
      const index = state.groups.findIndex(item => id(item.group_id) === groupID);
      const summary = { ...group, group_id: groupID };
      if (index < 0) state.groups.push(summary); else state.groups[index] = summary;
      renderRows();
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
      comparisonSummary.textContent = config ? `${(config.group_ids || []).length} 个参与分组 · ${config.enabled ? '定时已开启' : '定时已暂停'}` : '共用一个提示词比较不同分组的模型；保存后可开启定时。';
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
      controls.round.replaceChildren(...(round ? [el('details', { class: 'helper-gallery-admin-round-details' }, el('summary', { class: 'caption' }, `最近一轮 · 已完成 ${finished} / ${items.length}`), el('div', { class: 'helper-gallery-comparison-items' }, items.map(item => {
        const names = { queued: '等待生成', running: '正在生成', success: '已完成', failed: '生成失败', interrupted: '已中断' };
        return el('div', { class: 'helper-gallery-comparison-item' }, el('span', {}, `${item.name || `分组 #${item.group_id}`} · ${item.model || '未设置模型'}`), el('span', { class: `pill ${item.status === 'success' ? 'green' : ['failed', 'interrupted'].includes(item.status) ? 'red' : ''}` }, names[item.status] || '等待结果'));
      })))] : []));
    }
    function drawComparison() {
      const config = comparison.data?.config;
      const prompt = el('textarea', { class: 'input', name: 'gallery_comparison_prompt', rows: 5, required: true, maxlength: 8000 }, config?.prompt || DEFAULT_PROMPT);
      const interval = input('gallery_comparison_interval', config?.interval_minutes ?? 30, { type: 'number', min: 5, max: 1440, step: 1, required: true });
      const choices = el('div', { class: 'helper-gallery-comparison-groups' });
      const count = el('p', { class: 'caption' });
      const save = el('button', { type: 'submit', class: 'button primary' }, '保存同题设置');
      const run = button('同题生成一轮', () => runComparison(), 'button', 'play');
      const clear = button('退出同题配置', async () => {
        if (!comparison.data?.config || comparison.busy || state.busy || !confirm('退出同题配置？保留作品，分组恢复原本的独立定时设置。')) return;
        await mutateComparison('DELETE', '', undefined, '已退出同题配置，作品已保留。');
      }, 'button ghost', 'x');
      const toggle = input('gallery_comparison_enabled', '', { type: 'checkbox', checked: config?.enabled === true }); toggle.className = '';
      const toggleLabel = el('span'); toggle.addEventListener('change', () => toggleComparison(toggle.checked));
      const feedback = el('p', { class: 'helper-gallery-admin-feedback', role: 'status', 'aria-live': 'polite' });
      const status = el('div', { class: 'helper-gallery-admin-status', role: 'status' }), round = el('div', { class: 'helper-gallery-comparison-round' });
      const fields = el('fieldset', { class: 'helper-gallery-admin-fields' }, el('div', { class: 'field' }, el('span', {}, '参与分组'), choices), count,
        field('统一提示词', prompt, '所有选中的分组收到完全相同的提示词，并保留各自的模型与 Key。此提示词会随作品向用户展示。'),
        field('同题生成间隔（分钟）', interval, '默认 30 分钟；首次保存保持暂停。'),
        el('div', { class: 'helper-gallery-admin-actions' }, el('div', { class: 'button-group' }, save, run, clear), toggleControl(toggle, toggleLabel)),
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
      renderRows();
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
    }
    function defaultGroup(groupID) {
      const key = liveKeys(groupID)[0];
      const official = state.optionsGroups.find(group => id(group.id) === groupID);
      return { group_id: groupID, name: official?.name || key?.group_name || `分组 #${groupID}`, model: '', key_id: id(key?.id),
        api_format: 'chat', reasoning_effort: 'default', interval_minutes: 30, enabled: false, prompt: DEFAULT_PROMPT };
    }
    function populateKeys(control, selected) {
      control.replaceChildren(...liveKeys().map(option => el('option', { value: id(option.id) }, `${option.name || 'API Key'} · #${id(option.id)}`)));
      if (!liveKey(selected) && id(selected)) control.prepend(el('option', { value: id(selected), disabled: true }, `原 Key #${id(selected)}（当前不可用）`));
      if (id(selected)) control.value = String(id(selected));
      if (!control.options.length) control.append(el('option', { value: '', disabled: true }, '暂无可用的分组 Key'));
    }
    function drawConfiguration(group, saved) {
      state.controls = null; state.saved = saved ? group : null; state.dirty = false;
      const name = input('gallery_name', group.name || '', { required: true, maxlength: 100 });
      const model = input('gallery_model', group.model || '', { required: true, maxlength: 150, placeholder: '例如 gpt-4.1' });
      const key = el('select', { class: 'input', name: 'gallery_key_id', required: true });
      populateKeys(key, group.key_id);
      const keyHint = el('small', { class: 'helper-gallery-admin-key-hint' });
      const format = el('select', { class: 'input', name: 'gallery_api_format' }, el('option', { value: 'chat' }, 'Chat Completions'), el('option', { value: 'responses' }, 'Responses'));
      format.value = group.api_format === 'responses' ? 'responses' : 'chat';
      const effort = el('select', { class: 'input', name: 'gallery_reasoning_effort' }, Object.entries(EFFORTS).map(([value, label]) => el('option', { value }, label)));
      effort.value = Object.hasOwn(EFFORTS, group.reasoning_effort) ? group.reasoning_effort : 'default';
      const interval = input('gallery_interval_minutes', group.interval_minutes ?? 30, { type: 'number', min: 5, max: 1440, step: 1, required: true });
      const prompt = el('textarea', { class: 'input', name: 'gallery_prompt', rows: 7, required: true, maxlength: 8000 }, group.prompt || '');
      const save = el('button', { type: 'submit', class: 'button primary' }, icon('checkCircle'), el('span', {}, '保存配置'));
      const cancel = button('取消', () => closeEditor(), 'button ghost');
      const run = button('生成一轮', () => runOnce(), 'button', 'play');
      const toggle = input('gallery_enabled', '', { type: 'checkbox', checked: saved && group.enabled === true });
      toggle.className = '';
      const toggleLabel = el('span');
      toggle.addEventListener('change', () => toggleSchedule(toggle.checked));
      const status = el('div', { class: 'helper-gallery-admin-status', role: 'status' });
      const lastError = el('p', { class: 'helper-gallery-admin-error', role: 'status' });
      const intervalField = field('生成间隔（分钟）', interval, '默认 30 分钟，可设置 5～1440 分钟。');
      const promptField = field('创作提示词', prompt, '可要求模型返回完整 HTML 或 SVG 源码。此提示词及生成作品会向该分组的用户展示，预览在隔离环境中打开。');
      const scheduleNote = el('p', { class: 'caption' }, '智商检测菜单上线后才执行定时生成。');
      const switchControl = toggleControl(toggle, toggleLabel), costNote = el('p', { class: 'caption' });
      const fieldset = el('fieldset', { class: 'helper-gallery-admin-fields' },
        el('div', { class: 'helper-gallery-admin-grid' }, field('展示名称', name), field('模型', model), field('调用 API Key', key),
          field('接口格式', format), field('思考强度', effort, '默认不发送额外参数。其他档位按模型与网关支持情况生效，可能被映射或忽略。'), intervalField), keyHint, promptField,
        el('div', { class: 'helper-gallery-admin-actions' }, el('div', { class: 'button-group' }, save, cancel, run), switchControl), scheduleNote, costNote);
      const form = el('form', { class: 'helper-gallery-admin-form' }, status, lastError, fieldset);
      form.addEventListener('input', event => { if (event.target !== toggle) { state.dirty = true; syncControls(); } });
      form.addEventListener('change', event => { if (event.target !== toggle) { state.dirty = true; syncControls(); } });
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (state.busy || !liveKey(key.value)) return;
        if (state.adding && state.catalogError) { say('暂时无法确认已有配置，请刷新后再新增分组。', 'error'); return; }
        if (state.adding && state.groups.some(item => id(item.group_id) === state.groupID)) { say('此分组已有配置，请关闭新增界面后编辑已保存的记录。', 'error'); return; }
        const payload = { name: name.value.trim(), model: model.value.trim(), key_id: id(key.value), api_format: format.value,
          reasoning_effort: effort.value, interval_minutes: Number(interval.value), enabled: state.saved?.enabled === true, prompt: prompt.value.trim() };
        if (!payload.name || !payload.model || !payload.prompt || !Number.isInteger(payload.interval_minutes) || payload.interval_minutes < 5 || payload.interval_minutes > 1440) {
          say('请填写名称、模型、提示词，并将间隔设置为 5～1440 的整数分钟。', 'error'); return;
        }
        const bytes = new TextEncoder();
        if (bytes.encode(payload.name).length > 120 || bytes.encode(payload.model).length > 150 || bytes.encode(payload.prompt).length > 16000) {
          say('名称、模型名称或提示词过长，请缩短后保存。', 'error'); return;
        }
        if (await mutate('PUT', '', payload, '配置已保存。')) { closeEditor(true); say('配置已保存。', 'success'); }
      });
      state.controls = { fieldset, name, model, key, format, effort, interval, prompt, save, run, toggle, toggleLabel, status, lastError, keyHint,
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
        return true;
      } catch (error) { if (current(epoch, groupID)) say(errorText(error), 'error'); return false; }
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
      if (!state.adding || state.busy || comparison.busy) return;
      const groupID = id(select.value), previous = state.groupID;
      if (state.dirty && !confirm('放弃此分组尚未保存的修改？')) { select.value = previous || ''; return; }
      stopPolling(); state.epoch++; state.groupID = groupID; state.saved = null; state.detail = null; state.dirty = false; state.controls = null;
      editorFeedback.textContent = '';
      if (!groupID) { configuration.replaceChildren(el('p', { class: 'caption' }, '选择一个上游分组后设置模型。')); return; }
      drawConfiguration(defaultGroup(groupID), false);
    }
    select.addEventListener('change', chooseGroup);
    async function loadCatalog() {
      if (state.busy || comparison.busy || state.destroyed) return;
      const revision = ++comparison.revision; clearTimeout(comparison.poll);
      state.busy = true; syncControls();
      const results = await Promise.allSettled([request('/gallery'), request('/gallery/options'), request('/gallery/comparison')]);
      if (state.destroyed) return;
      const [catalog, options, shared] = results;
      if (catalog.status === 'fulfilled' && Array.isArray(catalog.value?.groups)) {
        state.groups = catalog.value.groups.filter(group => id(group?.group_id));
        state.catalogLoaded = true; state.catalogError = null;
        state.timezone = typeof catalog.value.timezone === 'string' ? catalog.value.timezone : 'Asia/Shanghai';
        if (!state.editorOpen) feedback.textContent = '';
      } else {
        const message = catalog.status === 'rejected' ? errorText(catalog.reason) : '已保存分组列表响应不兼容。';
        state.catalogError = message;
        feedback.textContent = message; feedback.className = 'helper-gallery-admin-feedback error';
        if (!state.catalogLoaded) { groupCount.textContent = '暂未读取已保存分组'; list.replaceChildren(el('p', { class: 'caption' }, '无法确认已有配置，请刷新后重试。')); }
      }
      if (options.status === 'fulfilled' && options.value && Array.isArray(options.value.groups) && Array.isArray(options.value.keys)) {
        state.optionsLoaded = true;
        state.optionsGroups = options.value.groups.filter(group => id(group?.id));
        state.keys = options.value.keys.filter(key => id(key?.id) && id(key?.group_id));
        state.groupError = options.value.group_error ? String(options.value.group_error) : null;
        state.keyError = options.value.key_error ? String(options.value.key_error) : null;
      } else {
        state.optionsLoaded = false;
        const message = options.status === 'rejected' ? errorText(options.reason) : '分组与 API Key 目录响应不兼容。';
        state.groupError = message; state.keyError = message;
      }
      if (shared.status === 'fulfilled') {
        try { acceptComparison(shared.value, !comparison.dirty); }
        catch (error) { comparison.loaded = false; syncComparison(); comparisonSay(errorText(error), 'error'); }
      } else {
        comparison.loaded = false; syncComparison();
        comparisonSay(`同题设置读取失败：${errorText(shared.reason)} 请刷新分组重试。`, 'error');
      }
      comparisonChoices();
      if (state.editorOpen && state.groupID) {
        if (state.dirty && state.controls) {
          const summary = state.groups.find(group => id(group.group_id) === state.groupID);
          if (state.saved && summary) state.saved = summary;
          populateKeys(state.controls.key, state.controls.key.value);
        } else if (state.saved) {
          try { await readDetail(state.epoch, true); } catch (error) { say(errorText(error), 'error'); }
        } else drawConfiguration(defaultGroup(state.groupID), false);
      }
      state.busy = false; renderRows(); syncControls();
      if (comparisonRunning()) pollComparison(revision);
    }
    loadCatalog().catch(error => { if (!state.destroyed) { state.busy = false; say(errorText(error), 'error'); syncControls(); } });
    return { refresh: loadCatalog, destroy };
  };
})();

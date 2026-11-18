/* Read-only generated-work gallery. Model markup is never inserted into the
 * application document. Previews run in opaque, script-free sandboxed frames. */
'use strict';
(() => {
  const PREFIX = '<!doctype html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; script-src \'none\'; frame-src \'none\'; connect-src \'none\'; object-src \'none\'; form-action \'none\'; base-uri \'none\'"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;min-height:100%;max-width:100%;overflow-x:hidden}body{font-family:system-ui,sans-serif}img,svg{max-width:100%}body>svg:only-child{display:block;width:100%;height:100vh}</style></head><body>';
  const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
  const COMPARISON = 'comparison';
  const EFFORT_LABELS = new Map(Object.entries({ default: '模型默认', none: '关闭', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高' }));
  const str = value => value === undefined || value === null ? '' : String(value);
  const count = value => Number.isFinite(Number(value)) && Number(value) >= 0 ? Math.floor(Number(value)) : 0;
  function modelSettings(model, effort) { return [str(model), EFFORT_LABELS.has(effort) ? `思考强度：${EFFORT_LABELS.get(effort)}` : ''].filter(Boolean).join(' · '); }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'disabled' || key === 'hidden') node[key] = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  function button(text, action, classes = 'btn btn-secondary btn-sm') { return el('button', { type: 'button', class: classes, onclick: action }, text); }
  function validGroup(value) { const text = str(value); return /^[1-9][0-9]*$/.test(text) && Number.isSafeInteger(Number(text)) ? text : null; }
  function validWork(value) { const text = str(value); return /^[A-Za-z0-9_-]{1,128}$/.test(text) ? text : null; }
  function imageSource(work) {
    const source = str(work.content ?? work.preview), match = source.match(/^data:(image\/(?:png|jpeg|webp|gif));base64,([a-z0-9+/]+={0,2})$/i);
    if (!match || source.length > 3 * 1024 * 1024 || match[2].length % 4) return null;
    const mime = match[1].toLowerCase(), hint = str(work.mime).toLowerCase();
    if (!IMAGE_TYPES.has(mime) || (hint && hint !== mime)) return null;
    const size = match[2].length * 3 / 4 - (match[2].endsWith('==') ? 2 : match[2].endsWith('=') ? 1 : 0);
    if (size > 2 * 1024 * 1024) return null;
    let bytes; try { bytes = atob(match[2]); } catch (_) { return null; }
    const valid = mime === 'image/png' ? bytes.slice(0, 8) === '\x89PNG\r\n\x1a\n'
      : mime === 'image/jpeg' ? bytes.slice(0, 3) === '\xff\xd8\xff'
      : mime === 'image/gif' ? ['GIF87a', 'GIF89a'].includes(bytes.slice(0, 6))
      : bytes.slice(0, 4) === 'RIFF' && bytes.slice(8, 12) === 'WEBP';
    return valid ? source : null;
  }
  async function prepareInlineImages(content) {
    // A detached template keeps legacy markup inert, including relative image
    // URLs. Only approved data URIs reach the browser's image decoder.
    const template = document.createElement('template');
    template.innerHTML = content;
    for (const candidate of template.content.querySelectorAll('picture > source')) candidate.remove();
    await Promise.all([...template.content.querySelectorAll('img')].map(async img => {
      const source = imageSource({ content: img.getAttribute('src') });
      if (source) {
        const probe = new Image();
        probe.src = source;
        try {
          await probe.decode();
          img.removeAttribute('srcset');
          img.setAttribute('src', source);
          return;
        } catch (_) { /* Fall through to a readable placeholder. */ }
      }
      img.replaceWith(el('span', { role: 'note' }, img.alt ? `图片无法显示 · ${img.alt}` : '图片无法显示'));
    }));
    return template.innerHTML;
  }
  function artwork(work, large = false) {
    const content = str(work.content ?? work.preview);
    if (!content) return el('div', { class: 'helper-gallery-placeholder' }, work.status === 'failed' ? '生成失败，暂无预览' : work.status === 'running' ? '正在生成' : '点击查看完整作品');
    if (['image', 'raster', 'gif'].includes(work.kind)) {
      const source = imageSource(work);
      if (!source) return el('div', { class: 'helper-gallery-placeholder' }, '图片格式不受支持');
      const img = el('img', { src: source, alt: str(work.title) || '模型生成作品', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
      img.addEventListener('error', () => img.replaceWith(el('div', { class: 'helper-gallery-placeholder' }, '图片无法显示')));
      return img;
    }
    if (work.kind === 'text') return el('pre', { class: 'helper-gallery-text-artwork' }, content);
    if (!['html', 'svg'].includes(work.kind)) return el('div', { class: 'helper-gallery-placeholder' }, '暂无可显示的预览');
    if (content.length > (large ? 512 : 128) * 1024) return el('div', { class: 'helper-gallery-placeholder' }, large ? '作品超出预览大小限制' : '点击查看完整作品');
    // The server removes these tags. Reject legacy navigation/embedded
    // documents before they are put into a sandboxed preview.
    if (/<\s*(?:meta|base|iframe|frame|frameset|object|embed)\b/i.test(content)) return el('div', { class: 'helper-gallery-placeholder' }, '作品包含不支持的文档内容');
    const frame = el('iframe', { class: 'helper-gallery-artwork', sandbox: '', title: str(work.title) || '模型生成作品', tabindex: '-1', referrerpolicy: 'no-referrer', inert: '' });
    const display = value => { frame.srcdoc = PREFIX + value + '</body></html>'; };
    if (/<\s*img\b/i.test(content)) {
      void prepareInlineImages(content).then(display, () => display('<p>作品中的图片无法显示。</p>'));
    } else display(content);
    return frame;
  }
  window.helperGallery = function helperGallery(container, { request, timezone = 'Asia/Shanghai', groupId, settingsUrl } = {}) {
    if (!(container instanceof Element) || typeof request !== 'function') throw new Error('Gallery requires a container and a request function');
    let destroyed = false, connected = container.isConnected, selected = validGroup(groupId), catalog = [], payload = null, comparison = null, selectionInitialized = Boolean(selected);
    let loadSequence = 0, modalSequence = 0, refreshPromise = null, modal = null, modalGroup = null;
    const root = el('section', { class: 'helper-gallery', 'aria-label': '模型生成作品' });
    const groupSelect = el('select', { class: 'input helper-gallery-select', 'aria-label': '作品分组', disabled: true });
    groupSelect.addEventListener('change', () => groupSelect.value === COMPARISON ? selectComparison() : selectGroup(groupSelect.value));
    const groupField = el('label', { class: 'helper-gallery-group-field' }, el('span', {}, '作品分组'), groupSelect);
    const refreshButton = button('刷新', () => refresh());
    const info = el('p', { class: 'helper-gallery-info' });
    const error = el('div', { class: 'helper-gallery-error', role: 'alert', hidden: true });
    const stats = el('div', { class: 'helper-gallery-stats' });
    const shared = el('div', { class: 'helper-gallery-comparison', hidden: true });
    const nextValue = el('strong');
    const works = el('div', { class: 'helper-gallery-grid' });
    const empty = el('div', { class: 'card helper-gallery-empty', role: 'status' }, '正在读取作品分组…');
    root.append(el('div', { class: 'helper-gallery-toolbar' }, groupField, refreshButton), info, error, stats, shared, works, empty);
    container.replaceChildren(root);
    function active() { return !destroyed && (!connected || container.isConnected); }
    function formatDate(value) {
      const date = new Date(value);
      if (!value || !Number.isFinite(date.getTime())) return '时间未知';
      try { return new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(date); } catch (_) { return date.toLocaleString('zh-CN', { hour12: false }); }
    }
    function latency(value) { return Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(1)} 秒` : `${Math.round(Number(value))} ms` : '耗时未知'; }
    function showError(value) { error.textContent = str(value?.message || value || '作品读取失败，请稍后重试。'); error.hidden = false; }
    function closeModal() { modalSequence++; modalGroup = null; if (modal) { if (modal.open) modal.close(); modal.remove(); modal = null; } }
    function hasComparison() { return Boolean(comparison && (comparison.config || comparison.round)); }
    function statusLabel(value) { return ({ queued: '排队中', running: '生成中', success: '已完成', partial: '部分完成', failed: '失败', interrupted: '已中断' })[value] || '等待生成'; }
    function statusBadge(status) { return el('span', { class: `pill ${status === 'success' ? 'green' : ['failed', 'interrupted'].includes(status) ? 'red' : 'gray'}` }, statusLabel(status)); }
    function renderGroups() {
      const entries = [...(hasComparison() ? [{ id: COMPARISON, name: '同题对比' }] : []), ...catalog.map(group => ({ id: str(group.group_id), name: str(group.name) || `分组 #${group.group_id}` }))];
      if (entries.length !== groupSelect.options.length || entries.some(({ id, name }, i) => groupSelect.options[i].value !== id || groupSelect.options[i].text !== name)) {
        groupSelect.replaceChildren(...entries.map(({ id, name }) => el('option', { value: id }, name)));
      }
      groupSelect.disabled = entries.length === 0;
      groupSelect.value = selected || '';
    }
    function updateCountdown() {
      if (!payload) return;
      const group = payload.group || {};
      if (group.enabled === false) { nextValue.textContent = '已暂停'; return; }
      const due = Date.parse(group.next_at || '');
      if (!Number.isFinite(due)) { nextValue.textContent = '暂无计划'; return; }
      const seconds = Math.ceil((due - Date.now()) / 1000);
      if (seconds <= 0) { nextValue.textContent = '等待生成'; return; }
      const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60;
      nextValue.textContent = `${hours ? `${hours}时 ` : ''}${String(minutes).padStart(2, '0')}分 ${String(rest).padStart(2, '0')}秒`;
    }
    function draw(data) {
      shared.hidden = true; shared.replaceChildren();
      const group = data.group || {}, values = data.stats || {};
      payload = { ...data, group: group.comparison_member ? { ...group, enabled: group.comparison_enabled ?? group.enabled, next_at: group.comparison_next_at ?? group.next_at } : group };
      info.textContent = [modelSettings(group.model, group.reasoning_effort) || '未设置模型', group.comparison_member ? '同题任务' : group.interval_minutes ? `每 ${count(group.interval_minutes)} 分钟生成` : '', timezone].filter(Boolean).join(' · ');
      stats.replaceChildren(...[['24 小时成功', count(values.success_24h)], ['24 小时失败', count(values.failed_24h)], ['作品总数', count(values.total)], ['距离下次生成', nextValue]].map(([label, value]) => el('div', { class: 'card helper-gallery-stat' }, el('span', {}, label), value instanceof Node ? value : el('strong', {}, value))));
      updateCountdown();
      const items = (Array.isArray(data.works) ? data.works : []).filter(item => item && validWork(item.id)).sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)).slice(0, 12);
      works.replaceChildren(...items.map(item => workCard(item, { groupId: selected, model: group.model })));
      empty.hidden = items.length > 0;
      if (!items.length) empty.textContent = group.running ? '正在生成第一份作品。' : '此分组还没有生成作品。';
    }
    function workCard(work, { groupId, groupName, model, effort, status = work.status, createdAt } = {}) {
      const id = validWork(work.id), title = str(work.title) || (groupName ? str(model) || '等待作品' : '未命名作品');
      const preview = !id && !work.content && !work.preview ? el('div', { class: 'helper-gallery-placeholder' }, statusLabel(status)) : artwork({ ...work, status });
      const children = [
        groupName ? el('div', { class: 'helper-gallery-comparison-heading' }, el('strong', {}, groupName), statusBadge(status)) : null,
        el('div', { class: 'helper-gallery-preview' }, preview),
        el('div', { class: 'helper-gallery-caption' }, el('div', { class: 'helper-gallery-title' }, el('h3', {}, title), !groupName && ['failed', 'interrupted'].includes(status) ? statusBadge(status) : null), el('p', {}, modelSettings(work.model || model, work.reasoning_effort ?? effort)), el('div', { class: 'helper-gallery-meta' }, el('time', { datetime: str(work.created_at || createdAt) }, formatDate(work.created_at || createdAt)), el('span', {}, work.latency_ms == null ? '—' : latency(work.latency_ms))), work.error ? el('p', { class: 'helper-gallery-card-error' }, str(work.error)) : null)
      ];
      const card = id ? button(children, () => openWork(groupId, id), 'card helper-gallery-work') : el('article', { class: 'card helper-gallery-work helper-gallery-pending' }, children);
      if (id) card.setAttribute('aria-label', `查看作品：${title}`);
      if (groupName) { card.classList.add('helper-gallery-comparison-item'); card.setAttribute('data-group-id', groupId); }
      return card;
    }
    function drawComparison() {
      const round = comparison?.round, config = comparison?.config || {};
      payload = { group: config }; error.hidden = true;
      const items = (Array.isArray(round?.items) ? round.items : []).filter(item => item && validGroup(item.group_id));
      const seen = new Set(), unique = items.filter(item => { const id = str(item.group_id); if (seen.has(id)) return false; seen.add(id); return true; });
      info.textContent = [round ? `${statusLabel(round.status)} · ${formatDate(round.created_at)}` : '同一个提示词，查看不同分组的模型表现', config.interval_minutes ? `每 ${count(config.interval_minutes)} 分钟一轮` : '', timezone].filter(Boolean).join(' · ');
      stats.replaceChildren(...[['本轮成功', unique.filter(item => item.status === 'success').length], ['失败 / 中断', unique.filter(item => ['failed', 'interrupted'].includes(item.status)).length], ['参与分组', unique.length], ['距离下次生成', nextValue]].map(([label, value]) => el('div', { class: 'card helper-gallery-stat' }, el('span', {}, label), value instanceof Node ? value : el('strong', {}, value))));
      updateCountdown();
      const promptOpen = Boolean(shared.querySelector('details')?.open);
      shared.replaceChildren(); shared.hidden = !round;
      if (round) {
        const details = el('details', { class: 'card helper-gallery-shared-prompt' }, el('summary', {}, '本轮共用提示词'), el('pre', { class: 'helper-gallery-prompt' }, str(round.prompt) || '暂无提示词记录'));
        details.open = promptOpen; shared.append(details);
      }
      works.replaceChildren(...unique.map(item => workCard(item.work || {}, { groupId: str(item.group_id), groupName: str(item.name) || `分组 #${item.group_id}`, model: item.model, effort: item.reasoning_effort, status: item.status, createdAt: round.created_at })));
      empty.hidden = unique.length > 0; if (!unique.length) empty.textContent = '暂无同题对比记录。';
    }
    function selectComparison() {
      if (!hasComparison() || destroyed) return;
      selected = COMPARISON; selectionInitialized = true; loadSequence++; closeModal(); renderGroups(); drawComparison();
    }
    async function openWork(group, id) {
      if (destroyed || !validGroup(group) || !validWork(id)) return;
      closeModal();
      modalGroup = group;
      const sequence = modalSequence;
      const body = el('div', { class: 'modal-body helper-gallery-dialog-body' }, el('p', {}, '正在读取作品…'));
      const heading = el('h3', { class: 'modal-title' }, '作品详情');
      const close = button('关闭', closeModal);
      modal = el('dialog', { class: 'modal-content helper-gallery-dialog', 'aria-label': '作品详情' }, el('div', { class: 'modal-header' }, heading, close), body);
      modal.addEventListener('cancel', closeModal); document.body.append(modal); modal.showModal();
      try {
        const work = await request(`/gallery/${group}/works/${encodeURIComponent(id)}`);
        if (!active() || !modal || modalSequence !== sequence) return;
        if (!work || str(work.id) !== id) throw new Error('作品身份响应不完整，已停止展示。');
        heading.textContent = str(work.title) || '未命名作品';
        body.replaceChildren(el('div', { class: 'helper-gallery-large' }, artwork(work, true)), el('p', { class: 'helper-gallery-detail-meta' }, `${modelSettings(work.model, work.reasoning_effort) || '未知模型'} · ${formatDate(work.created_at)} · ${latency(work.latency_ms)}`), el('h4', {}, '生成提示词'), el('pre', { class: 'helper-gallery-prompt' }, str(work.prompt) || '暂无提示词记录'));
      } catch (failure) { if (modal && modalSequence === sequence) body.replaceChildren(el('p', { class: 'helper-gallery-error', role: 'alert' }, str(failure?.message || '作品读取失败。'))); }
    }
    async function loadGroup() {
      if (!active() || !validGroup(selected)) return;
      const group = selected, sequence = ++loadSequence;
      refreshButton.disabled = true; error.hidden = true;
      try {
        const data = await request(`/gallery/${group}`);
        if (!active() || sequence !== loadSequence || selected !== group) return;
        if (!data || !data.group || str(data.group.group_id) !== group || !data.stats || !Array.isArray(data.works)) throw new Error('作品列表响应不完整，请稍后重试。');
        draw(data);
      } catch (failure) { if (active() && sequence === loadSequence) { if (!payload) empty.hidden = true; showError(failure); } }
      finally { if (active() && sequence === loadSequence) refreshButton.disabled = false; }
    }
    async function selectGroup(value) {
      const id = validGroup(value);
      if (!id || destroyed || !catalog.some(group => str(group.group_id) === id)) return;
      selected = id; selectionInitialized = true; closeModal(); renderGroups();
      payload = null; info.textContent = ''; shared.hidden = true; shared.replaceChildren(); stats.replaceChildren(); works.replaceChildren(); empty.hidden = false; empty.textContent = '正在读取作品…'; await loadGroup();
    }
    function refresh() {
      if (destroyed) return Promise.resolve();
      if (refreshPromise) return refreshPromise;
      refreshButton.disabled = true;
      refreshPromise = (async () => {
        try {
          const [directoryResult, comparisonResult] = await Promise.allSettled([request('/gallery'), request('/gallery/comparison')]);
          if (!active()) return;
          if (directoryResult.status !== 'fulfilled') throw directoryResult.reason;
          const directory = directoryResult.value;
          if (!directory || !Array.isArray(directory.groups)) throw new Error('分组列表响应不完整。');
          catalog = directory.groups.filter(group => group && validGroup(group.group_id));
          comparison = comparisonResult.status === 'fulfilled' && comparisonResult.value && typeof comparisonResult.value === 'object' ? comparisonResult.value : null;
          if (comparison?.round && (!Array.isArray(comparison.round.items) || !comparison.round.items.some(item => item && validGroup(item.group_id)))) comparison = { ...comparison, round: null };
          if (modalGroup && !catalog.some(group => str(group.group_id) === modalGroup) && !comparison?.round?.items?.some(item => str(item?.group_id) === modalGroup)) closeModal();
          if (typeof directory.timezone === 'string' && directory.timezone) timezone = directory.timezone;
          if (!selectionInitialized) { selected = comparison?.round ? COMPARISON : catalog.length ? str(catalog[0].group_id) : null; selectionInitialized = true; }
          if ((selected === COMPARISON && !hasComparison()) || (selected !== COMPARISON && !catalog.some(group => str(group.group_id) === selected))) { closeModal(); payload = null; shared.hidden = true; shared.replaceChildren(); stats.replaceChildren(); works.replaceChildren(); selected = catalog.length ? str(catalog[0].group_id) : null; }
          renderGroups();
          if (selected === COMPARISON) { drawComparison(); return; }
          if (!selected) {
            payload = null; info.textContent = ''; shared.hidden = true; shared.replaceChildren(); stats.replaceChildren(); works.replaceChildren();
            empty.hidden = false;
            empty.replaceChildren(el('p', {}, settingsUrl ? '尚未配置作品分组。' : '当前账号暂无可查看的作品分组。'), settingsUrl ? el('a', { href: settingsUrl, class: 'btn btn-secondary btn-sm' }, '配置作品分组') : null);
            error.hidden = true;
            return;
          }
          await loadGroup();
        } catch (failure) { if (active()) { empty.hidden = true; showError(failure); } }
        finally { if (active()) refreshButton.disabled = false; refreshPromise = null; }
      })();
      return refreshPromise;
    }
    function destroy() { if (destroyed) return; destroyed = true; loadSequence++; closeModal(); clearInterval(poll); clearInterval(clock); observer.disconnect(); window.removeEventListener('pagehide', destroy); }
    const observer = new MutationObserver(() => { if (container.isConnected) connected = true; else if (connected) destroy(); });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    const poll = setInterval(() => { if (!container.isConnected && connected) destroy(); else if (!document.hidden && container.isConnected) refresh(); }, 30000);
    const clock = setInterval(() => { if (container.isConnected) updateCountdown(); }, 1000);
    window.addEventListener('pagehide', destroy);
    const ready = refresh();
    return { refresh, destroy, selectGroup, ready };
  };
})();

/* Read-only generated-work gallery. Model markup is never inserted into the
 * application document. Previews run in opaque, script-free sandboxed frames. */
'use strict';
(() => {
  const PREFIX = '<!doctype html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; script-src \'none\'; frame-src \'none\'; connect-src \'none\'; object-src \'none\'; form-action \'none\'; base-uri \'none\'"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;min-height:100%;max-width:100%;overflow-x:hidden}body{font-family:system-ui,sans-serif}img,svg{max-width:100%}body>svg:only-child{display:block;width:100%;height:100vh}</style></head><body>';
  const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
  const EFFORT_LABELS = new Map(Object.entries({ default: '模型默认', none: '关闭', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高' }));
  const STATUS_LABELS = new Map(Object.entries({ queued: '排队中', running: '生成中', success: '已完成', partial: '部分完成', failed: '失败', interrupted: '已中断' }));
  const PLATFORMS = new Set(['anthropic', 'openai', 'gemini', 'antigravity', 'grok', 'kimi', 'zhipu', 'deepseek', 'minimax', 'composite']);
  const platformClass = value => PLATFORMS.has(str(value).toLowerCase()) ? str(value).toLowerCase() : 'default';
  const str = value => value === undefined || value === null ? '' : String(value);
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
    if (!content) return el('div', { class: 'helper-gallery-placeholder' }, work.status === 'failed' ? '失败' : work.status === 'running' ? '生成中' : work.status === 'interrupted' ? '中断' : large ? '暂无作品内容' : '查看');
    if (['image', 'raster', 'gif'].includes(work.kind)) {
      const source = imageSource(work);
      if (!source) return el('div', { class: 'helper-gallery-placeholder' }, '格式不支持');
      const img = el('img', { src: source, alt: str(work.title) || '模型生成作品', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
      img.addEventListener('error', () => img.replaceWith(el('div', { class: 'helper-gallery-placeholder' }, '图片无法显示')));
      return img;
    }
    if (work.kind === 'text') return el('pre', { class: 'helper-gallery-text-artwork' }, content);
    if (!['html', 'svg'].includes(work.kind)) return el('div', { class: 'helper-gallery-placeholder' }, '无预览');
    if (content.length > (large ? 512 : 128) * 1024) return el('div', { class: 'helper-gallery-placeholder' }, large ? '作品超出预览大小限制' : '点击查看');
    // The server removes these tags. Reject legacy navigation/embedded
    // documents before they are put into a sandboxed preview.
    if (/<\s*(?:meta|base|iframe|frame|frameset|object|embed)\b/i.test(content)) return el('div', { class: 'helper-gallery-placeholder' }, '内容不支持');
    const frame = el('iframe', { class: 'helper-gallery-artwork', sandbox: '', title: str(work.title) || '模型生成作品', tabindex: '-1', referrerpolicy: 'no-referrer', inert: '', loading: large ? 'eager' : 'lazy' });
    const display = value => { frame.srcdoc = PREFIX + value + '</body></html>'; };
    if (/<\s*img\b/i.test(content)) {
      void prepareInlineImages(content).then(display, () => display('<p>作品中的图片无法显示。</p>'));
    } else display(content);
    return frame;
  }
  window.helperGallery = function helperGallery(container, { request, timezone = 'Asia/Shanghai', settingsUrl } = {}) {
    if (!(container instanceof Element) || typeof request !== 'function') throw new Error('Gallery requires a container and a request function');
    let destroyed = false, connected = container.isConnected, refreshPromise = null, modal = null, modalGroup = null, modalSequence = 0, cardSequence = 0, feedKey = null, nextAt = null;
    const root = el('section', { class: 'helper-gallery', 'aria-label': '模型生成作品' });
    const next = el('p', { class: 'helper-gallery-info', hidden: true });
    const error = el('div', { class: 'helper-gallery-error', role: 'alert', hidden: true });
    const works = el('div', { class: 'helper-gallery-list' });
    const empty = el('div', { class: 'helper-gallery-empty', role: 'status' }, '');
    root.append(next, error, works, empty);
    container.replaceChildren(root);
    function active() { return !destroyed && (!connected || container.isConnected); }
    function formatDate(value) {
      const date = new Date(value);
      if (!value || !Number.isFinite(date.getTime())) return '时间未知';
      try { return new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(date); } catch (_) { return date.toLocaleString('zh-CN', { hour12: false }); }
    }
    function formatShortTime(value) {
      const date = new Date(value);
      if (!value || !Number.isFinite(date.getTime())) return '历史';
      try { return new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date); } catch (_) { return date.toLocaleString('zh-CN', { hour12: false }); }
    }
    function latency(value) { return value !== null && value !== undefined && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(1)} 秒` : `${Math.round(Number(value))} ms` : '—'; }
    function statusLabel(value) { return STATUS_LABELS.get(value) || '等待作品'; }
    function statusBadge(status) { return el('span', { class: `pill ${status === 'success' ? 'green' : ['failed', 'interrupted'].includes(status) ? 'red' : 'gray'}` }, statusLabel(status)); }
    function closeModal() { modalSequence++; modalGroup = null; if (modal) { if (modal.open) modal.close(); modal.remove(); modal = null; } }
    function updateCountdown() {
      const due = Date.parse(nextAt || '');
      next.hidden = !Number.isFinite(due);
      if (next.hidden) { next.textContent = ''; return; }
      const seconds = Math.ceil((due - Date.now()) / 1000);
      if (seconds <= 0) { next.textContent = '下次生成：等待生成'; return; }
      const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60;
      next.textContent = `下次生成：${hours ? `${hours}时 ` : ''}${String(minutes).padStart(2, '0')}分 ${String(rest).padStart(2, '0')}秒`;
    }
    function makeThumb(group, work, primary, siblings) {
      const id = validWork(work?.id);
      const title = str(work?.title) || str(work?.model) || (primary ? '最新作品' : '历史作品');
      const status = work?.status;
      const stage = el('div', { class: 'helper-gallery-thumb-stage' }, id ? artwork(work, false) : el('div', { class: 'helper-gallery-placeholder' }, statusLabel(status)));
      const cls = primary ? 'helper-gallery-thumb helper-gallery-thumb-primary' : 'helper-gallery-thumb helper-gallery-thumb-mini';
      if (!id) return el('div', { class: `${cls} helper-gallery-pending`, 'aria-label': statusLabel(status) }, stage);
      const label = primary ? `查看作品：${title}` : `查看历史作品：${title}`;
      const trigger = el('button', {
        type: 'button',
        class: cls,
        'aria-label': label,
        onclick: () => openWork(group, id, siblings),
      }, stage);
      return trigger;
    }
    function workRow(item) {
      const group = validGroup(item.group_id);
      const work = item.work && typeof item.work === 'object' ? item.work : null;
      const id = validWork(work?.id);
      const model = str(work?.model || item.model) || '未指定模型';
      const effort = work?.reasoning_effort ?? item.reasoning_effort;
      const source = str(item.group_name) || str(item.name) || `分组 #${group}`;
      const status = item.status || work?.status;
      const title = str(work?.title) || model;
      const history = (Array.isArray(item.history) ? item.history : [])
        .filter(entry => entry && typeof entry === 'object' && validWork(entry.id) && (!entry.group_id || str(entry.group_id) === group))
        .slice(0, 4);
      const siblings = [...(id ? [work] : []), ...history];
      const serial = ++cardSequence, sourceID = `helper-gallery-source-${serial}`, modelID = `helper-gallery-model-${serial}`;
      const metaTime = work?.created_at || item.created_at;
      const metaLatency = work?.latency_ms;
      const statusBadges = [
        ['queued', 'running'].includes(status) ? statusBadge(status) : null,
        ['failed', 'interrupted'].includes(status) ? statusBadge(status) : null,
      ];
      const info = el('div', { class: 'helper-gallery-row-info' },
        el('div', { class: 'helper-gallery-row-head' },
          el('span', { class: `helper-group-badge helper-group-${platformClass(item.platform)}`, id: sourceID }, source),
          el('h3', { class: 'helper-gallery-model', id: modelID }, modelSettings(model, effort)),
        ),
        el('div', { class: 'helper-gallery-row-status' },
          statusBadges,
          el('div', { class: 'helper-gallery-meta' },
            metaTime ? el('time', { datetime: str(metaTime) }, formatDate(metaTime)) : null,
            el('span', {}, `耗时 ${latency(metaLatency)}`),
            id && work?.title ? el('span', { class: 'helper-gallery-work-title' }, title) : null,
          ),
        ),
        work?.error ? el('p', { class: 'helper-gallery-card-error' }, str(work.error)) : null,
      );
      const thumbs = el('div', { class: 'helper-gallery-thumbs', 'aria-label': `${source} 作品缩略图` },
        makeThumb(group, work ? { ...work, status } : { status }, true, siblings),
        history.length ? el('div', { class: 'helper-gallery-history', 'aria-label': '最近历史作品' }, history.map(entry => makeThumb(group, entry, false, siblings))) : null,
      );
      const row = el('article', {
        class: `card helper-gallery-work${id ? '' : ' helper-gallery-pending'}`,
        'data-group-id': group,
        'aria-describedby': `${sourceID} ${modelID}`,
        tabindex: '-1',
      }, info, thumbs);
      return row;
    }
    function draw(data) {
      if (typeof data.timezone === 'string' && data.timezone) timezone = data.timezone;
      const rawItems = data.items.filter(item => item && validGroup(item.group_id) && (!item.work?.group_id || str(item.work.group_id) === str(item.group_id)));
      const items = rawItems;
      if (modalGroup && !items.some(item => str(item.group_id) === modalGroup)) closeModal();
      nextAt = data.next_at; updateCountdown(); error.hidden = true;
      const key = JSON.stringify([timezone, items]);
      if (key === feedKey) return;
      feedKey = key;
      works.replaceChildren(...items.map(workRow));
      empty.hidden = items.length > 0;
      empty.replaceChildren(el('p', {}, '暂无参测结果'), settingsUrl ? el('a', { href: settingsUrl, class: 'btn btn-secondary btn-sm' }, '配置参测来源') : null);
    }
    async function openWork(group, id, siblings = []) {
      if (destroyed || !validGroup(group) || !validWork(id)) return;
      closeModal(); modalGroup = group;
      const sequence = modalSequence;
      const body = el('div', { class: 'modal-body helper-gallery-dialog-body' }, el('p', {}, '…'));
      const heading = el('h3', { class: 'modal-title' }, '作品详情');
      const switcher = Array.isArray(siblings) && siblings.length > 1
        ? el('div', { class: 'helper-gallery-switcher', role: 'group', 'aria-label': '切换同模型历史作品' },
            siblings.map((entry, idx) => {
              const entryId = validWork(entry?.id);
              if (!entryId) return null;
              const activeEntry = entryId === id;
              const label = idx === 0 ? '最新' : formatShortTime(entry.created_at);
              return el('button', {
                type: 'button',
                class: `btn btn-sm ${activeEntry ? 'btn-primary' : 'btn-secondary'}`,
                'aria-pressed': activeEntry ? 'true' : 'false',
                onclick: activeEntry ? null : () => openWork(group, entryId, siblings),
              }, label);
            }))
        : null;
      modal = el('dialog', { class: 'modal-content helper-gallery-dialog', 'aria-label': '作品详情' }, el('div', { class: 'modal-header' }, heading, button('关闭', closeModal)), body);
      modal.addEventListener('cancel', closeModal); document.body.append(modal); modal.showModal();
      try {
        const work = await request(`/gallery/${group}/works/${encodeURIComponent(id)}`);
        if (!active() || !modal || modalSequence !== sequence) return;
        if (!work || str(work.id) !== id || (work.group_id != null && str(work.group_id) !== group)) throw new Error('作品身份响应不完整，已停止展示。');
        heading.textContent = str(work.title) || '未命名作品';
        body.replaceChildren(
          switcher,
          el('div', { class: 'helper-gallery-large' }, artwork(work, true)),
          el('div', { class: 'helper-gallery-detail-meta' },
            el('span', {}, `${modelSettings(work.model, work.reasoning_effort) || '未知模型'} · ${formatDate(work.created_at)} · ${latency(work.latency_ms)}`),
          ),
          el('h4', {}, '生成提示词'),
          el('pre', { class: 'helper-gallery-prompt' }, str(work.prompt) || '暂无提示词记录'),
        );
      } catch (failure) { if (modal && modalSequence === sequence) body.replaceChildren(switcher, el('p', { class: 'helper-gallery-error', role: 'alert' }, str(failure?.message || '作品读取失败。'))); }
    }
    function focusSource(value) {
      const id = validGroup(value);
      if (!id || destroyed) return false;
      const target = [...works.children].find(card => card.dataset.groupId === id);
      if (!target) return false;
      target.scrollIntoView({ block: 'nearest' }); target.focus({ preventScroll: true }); return true;
    }
    function refresh() {
      if (destroyed) return Promise.resolve();
      if (refreshPromise) return refreshPromise;
      refreshPromise = (async () => {
        try {
          const data = await request('/gallery/feed');
          if (!active()) return;
          if (!data || !Array.isArray(data.items)) throw new Error('作品列表响应不完整，请稍后重试。');
          draw(data);
        } catch (failure) {
          if (active()) { closeModal(); feedKey = null; nextAt = null; works.replaceChildren(); next.hidden = true; empty.hidden = true; error.textContent = str(failure?.message || '作品读取失败，请稍后重试。'); error.hidden = false; }
        } finally { refreshPromise = null; }
      })();
      return refreshPromise;
    }
    function destroy() { if (destroyed) return; destroyed = true; closeModal(); clearInterval(poll); clearInterval(clock); observer.disconnect(); window.removeEventListener('pagehide', destroy); }
    const observer = new MutationObserver(() => { if (container.isConnected) connected = true; else if (connected) destroy(); });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    const poll = setInterval(() => { if (!container.isConnected && connected) destroy(); else if (!document.hidden && container.isConnected) refresh(); }, 30000);
    const clock = setInterval(() => { if (container.isConnected) updateCountdown(); }, 1000);
    window.addEventListener('pagehide', destroy);
    const ready = refresh();
    return { refresh, destroy, focusSource, ready };
  };
})();

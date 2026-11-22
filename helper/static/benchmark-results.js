/* Shared, inert model self-test result view. All model text is plain text. */
'use strict';
(() => {
  const TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
  const IMAGE_LIMIT = 2 * 1024 * 1024, ROUND_LIMIT = 8 * 1024 * 1024, MEDIA_LIMIT = 6;
  function el(tag, attrs = {}, ...children) {
    const item = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null) continue;
      if (key === 'class') item.className = value;
      else if (key === 'disabled' || key === 'hidden') item[key] = value;
      else if (key.startsWith('on')) item.addEventListener(key.slice(2), value);
      else item.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child !== undefined && child !== null && child !== false) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return item;
  }
  const text = value => value === undefined || value === null ? '' : String(value);
  const numeric = value => Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
  const duration = value => value >= 1000 ? `${(value / 1000).toFixed(2)} 秒` : `${Math.round(value)} ms`;
  function signature(bytes, mime) {
    if (mime === 'image/png') return bytes.slice(0, 8) === '\x89PNG\r\n\x1a\n';
    if (mime === 'image/jpeg') return bytes.slice(0, 3) === '\xff\xd8\xff';
    if (mime === 'image/gif') return ['GIF87a', 'GIF89a'].includes(bytes.slice(0, 6));
    return mime === 'image/webp' && bytes.slice(0, 4) === 'RIFF' && bytes.slice(8, 12) === 'WEBP';
  }
  function mediaSource(item, budget) {
    if (!item || typeof item !== 'object' || typeof item.src !== 'string' || item.src.length > 3 * 1024 * 1024) return null;
    const hint = text(item.mime).toLowerCase().split(';', 1)[0].trim();
    if (hint && !TYPES.has(hint)) return null;
    const alt = text(item.alt).slice(0, 2000) || '模型返回的图片';
    const data = item.src.match(/^data:(image\/(?:png|jpeg|webp|gif));base64,([a-z0-9+/]+={0,2})$/i);
    if (data) {
      const mime = data[1].toLowerCase(), encoded = data[2];
      if (hint && hint !== mime) return null;
      const size = encoded.length * 3 / 4 - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0);
      if (encoded.length % 4 || size > IMAGE_LIMIT || budget.bytes + size > ROUND_LIMIT) return null;
      let bytes; try { bytes = atob(encoded); } catch (_) { return null; }
      if (!signature(bytes, mime)) return null;
      budget.bytes += size;
      return { src: item.src, mime, alt, remote: false };
    }
    if (item.src.length > 4096 || !/^https:\/\//i.test(item.src) || item.src.includes('\\') || /[\u0000-\u0020\u007f]/.test(item.src)) return null;
    try {
      const url = new URL(item.src);
      let path; try { path = decodeURIComponent(url.pathname); } catch (_) { return null; }
      if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || /\.(?:svgz?|html?|js|mjs|xml)$/i.test(path)) return null;
      return { src: url.href, mime: hint, alt, remote: true, host: url.hostname };
    } catch (_) { return null; }
  }
  function image(source, loaded, failed) {
    const img = el('img', { alt: source.alt, title: '点击展开或收起原图', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
    if (loaded) img.addEventListener('load', loaded, { once: true });
    if (failed) img.addEventListener('error', failed, { once: true });
    img.addEventListener('click', () => {
      const wrap = img.closest('.helper-benchmark-image');
      if (wrap) wrap.classList.toggle('is-expanded');
    });
    img.src = source.src;
    return img;
  }
  function mediaView(source) {
    const content = el('div', { class: 'helper-benchmark-image' });
    const caption = el('figcaption', {}, source.alt, source.mime === 'image/gif' ? el('span', { class: 'helper-benchmark-format' }, 'GIF 动图') : null);
    if (!source.remote) content.append(image(source, undefined, () => {
      content.replaceChildren(el('p', { class: 'helper-benchmark-media-status', role: 'status' }, '图片无法显示。'));
    }));
    else {
      const status = el('p', { class: 'helper-benchmark-media-status', role: 'status' }, `外部图片 · ${source.host}。点击后才会向图片地址发起请求。`);
      const load = el('button', { type: 'button', class: 'btn btn-secondary btn-sm' }, '加载外部图片');
      load.addEventListener('click', () => {
        load.disabled = true; status.textContent = '正在加载外部图片…';
        const img = image(source, () => { status.textContent = `外部图片已加载 · ${source.host}`; load.hidden = true; }, () => {
          content.replaceChildren(); status.textContent = '图片加载失败，可检查地址后重试。'; load.textContent = '重试加载'; load.disabled = false; load.hidden = false;
        });
        content.replaceChildren(img);
      });
      return el('figure', { class: 'helper-benchmark-media helper-benchmark-remote' }, status, load, content, caption);
    }
    return el('figure', { class: 'helper-benchmark-media' }, content, caption);
  }
  function stateOf(item, media) {
    if (text(item.error).trim()) return 'error';
    if (item.scored === false || (!text(item.answer).trim() && media.length)) return 'ungraded';
    return item.passed === true ? 'passed' : 'failed';
  }
  const labels = { passed: ['正确', 'green'], failed: ['错误', 'red'], ungraded: ['未判分', ''], error: ['请求失败', 'red'] };
  window.helperBenchmarkResults = function helperBenchmarkResults(data = {}, { groupName, onBack } = {}) {
    const result = data && typeof data === 'object' ? data : {};
    const budget = { bytes: 0 };
    const entries = (Array.isArray(result.results) ? result.results : []).slice(0, 100).filter(item => item && typeof item === 'object').map(item => {
      const original = Array.isArray(item.media) ? item.media : [];
      const media = original.slice(0, MEDIA_LIMIT).map(value => mediaSource(value, budget)).filter(Boolean);
      return { item, media, rejected: original.length - media.length + Math.floor(numeric(item.media_omitted)), status: stateOf(item, media) };
    });
    const counts = { passed: 0, failed: 0, ungraded: 0, error: 0 };
    for (const entry of entries) counts[entry.status]++;
    const total = Math.max(entries.length, Math.floor(numeric(result.total)));
    const completed = Math.min(total, Math.floor(result.completed === undefined ? entries.length : numeric(result.completed)));
    const graded = counts.passed + counts.failed;
    const score = !entries.length ? '暂无结果' : !graded ? counts.ungraded ? '未判分' : '暂无有效评分' : `${counts.passed} / ${graded}`;
    const elapsed = entries.reduce((sum, entry) => sum + numeric(entry.item.latency_ms), 0);
    const title = el('h2', { tabindex: '-1' }, '检测结果');
    const heading = el('div', { class: 'helper-benchmark-heading' }, el('div', {}, title, el('p', {}, text(result.model) || '未指定模型', groupName ? ` · ${text(groupName)}` : '')));
    if (typeof onBack === 'function') heading.append(el('button', { type: 'button', class: 'btn btn-secondary helper-benchmark-back', onclick: onBack }, '返回配置'));
    const summary = el('section', { class: 'card helper-benchmark-summary' }, el('div', { class: 'card-body' }, heading,
      el('div', { class: 'helper-benchmark-metrics' }, [['得分', score], ['已完成', `${completed} / ${total}`], ['累计响应耗时', duration(elapsed)]].map(([name, value]) => el('div', {}, el('span', {}, name), el('strong', {}, value)))),
      el('div', { class: 'helper-benchmark-counts' }, Object.entries(labels).map(([key, [name, color]]) => el('span', { class: `pill ${color}` }, `${name} ${counts[key]}`))),
      el('p', { class: 'helper-benchmark-note' }, '固定题集仅供基本能力自检。图片及动图可以展示；仅返回媒体的题目标记为未判分，不算作答错。')));
    const view = el('section', { class: 'helper-benchmark-results', 'aria-label': '检测结果详情' }, summary);
    if (!entries.length) view.append(el('section', { class: 'card' }, el('div', { class: 'card-body helper-benchmark-empty' }, '没有可展示的检测结果。返回配置后可重新运行。')));
    for (const [index, entry] of entries.entries()) {
      const { item, media, status, rejected } = entry;
      const [label, color] = labels[status];
      const response = el('div', { class: 'helper-benchmark-response' }, el('h4', {}, '模型回答'));
      const answer = text(item.answer);
      if (answer) response.append(el('pre', { class: 'helper-benchmark-text' }, answer));
      else response.append(el('p', { class: 'helper-benchmark-note' }, media.length ? '此题返回媒体内容。' : '暂无文本回答。'));
      if (text(item.error)) response.append(el('p', { class: 'helper-benchmark-error', role: 'alert' }, text(item.error)));
      if (media.length) response.append(el('div', { class: 'helper-benchmark-gallery' }, media.map(mediaView)));
      if (rejected) response.append(el('p', { class: 'helper-benchmark-note' }, `已忽略 ${rejected} 项不支持或超出限制的媒体内容。仅展示 PNG、JPEG、WebP、GIF 及无 URL 凭据的 HTTPS 图片。`));
      view.append(el('article', { class: 'card helper-benchmark-question', 'aria-label': `第 ${index + 1} 题` },
        el('div', { class: 'card-header' }, el('h3', {}, `第 ${index + 1} 题`), el('div', { class: 'helper-benchmark-question-meta' }, el('span', { class: 'helper-benchmark-note' }, duration(numeric(item.latency_ms))), el('span', { class: `pill ${color}` }, label))),
        el('div', { class: 'card-body' }, el('h4', {}, '题目'), el('pre', { class: 'helper-benchmark-text helper-benchmark-prompt' }, text(item.question) || '未提供题目'),
          el('div', { class: 'helper-benchmark-answer-grid' }, el('div', {}, el('h4', {}, '期望答案'), el('pre', { class: 'helper-benchmark-text' }, text(item.expected) || '未提供文本评分标准')), response))));
    }
    return view;
  };
})();

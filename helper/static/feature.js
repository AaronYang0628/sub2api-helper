'use strict';
// Tokens supplied by sub2api's custom-page URL are removed before any API call.
const params = new URLSearchParams(location.search);
const requestedTheme = params.get('theme');
if (requestedTheme === 'dark' || requestedTheme === 'light') { document.documentElement.dataset.theme = requestedTheme; document.documentElement.classList.toggle('dark', requestedTheme === 'dark'); }
let loginToken = params.get('token') || '';
history.replaceState(null, '', location.pathname);
let portalSession = '';
const featureMain = document.querySelector('#feature-main');
const feature = location.pathname.split('/').filter(Boolean).pop();
const titles = { benchmark: '智商检测', pricing: '模型价格统计', ranking: '用户用量排名' };
function node(tag, attrs = {}, ...children) {
  const result = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') result.className = value;
    else if (key === 'value' || key === 'disabled') result[key] = value;
    else if (key.startsWith('on')) result.addEventListener(key.slice(2), value);
    else result.setAttribute(key, value === true ? '' : value);
  }
  children.flat(Infinity).forEach(child => { if (child !== undefined && child !== null && child !== false) result.append(child instanceof Node ? child : document.createTextNode(String(child))); });
  return result;
}
async function request(endpoint, method = 'GET', data) {
  const response = await fetch(`/_helper/api/portal${endpoint}`, { method, credentials: 'omit', redirect: 'error', headers: { ...(portalSession ? { 'X-Portal-Session': portalSession } : {}), ...(data !== undefined ? { 'Content-Type': 'application/json' } : {}) }, ...(data !== undefined ? { body: JSON.stringify(data) } : {}) });
  let body; try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body?.detail || body?.message || `请求失败 (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body;
}
const PLATFORMS = new Set(['anthropic', 'openai', 'gemini', 'antigravity', 'grok', 'kimi', 'zhipu', 'deepseek', 'minimax', 'composite']);
const platformClass = value => PLATFORMS.has(String(value || '').toLowerCase()) ? String(value).toLowerCase() : 'default';
const groupBadge = group => node('span', { class: `helper-group-badge helper-group-${platformClass(group?.platform)}` }, group?.name || `分组 #${group?.id}`);
const num = value => Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const usd = (value, digits = 4) => `$${Number(value || 0).toFixed(digits)}`;
const unitPrice = value => value === null || value === undefined ? '—' : `$${Number(Number(value).toFixed(4))}`;
const isoDay = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
function presetRange(kind) {
  const end = new Date(), start = new Date(end);
  if (kind === 'month') start.setDate(1); else start.setDate(end.getDate() - (kind === '7' ? 6 : 29));
  return { start_date: isoDay(start), end_date: isoDay(end) };
}
const PRICE_ROWS = [['input', '输入', 'input_tokens'], ['output', '输出', 'output_tokens'], ['cache_read', '缓存读取', 'cache_read_tokens'], ['cache_write', '缓存写入', 'cache_write_tokens']];
function modelCard(row) {
  const missing = !row.official;
  const diff = row.multiplier === null || row.multiplier === undefined ? null : row.multiplier - 1;
  const rows = PRICE_ROWS.filter(([, , tokens]) => Number(row[tokens]) > 0).map(([key, label, tokens]) => node('tr', {},
    node('td', {}, label),
    node('td', { class: 'fv-num' }, num(row[tokens])),
    node('td', { class: 'fv-num' }, unitPrice(row.official?.[key])),
    node('td', { class: 'fv-num fv-strong' }, unitPrice(row.actual?.[key])),
    node('td', { class: `fv-num ${diff === null || missing ? '' : diff < -0.0005 ? 'fv-down' : diff > 0.0005 ? 'fv-up' : ''}` },
      diff === null || missing || row.official?.[key] == null ? '—' : Math.abs(diff) <= 0.0005 ? '持平' : `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}%`)));
  return node('article', { class: 'card fv-model' },
    node('header', { class: 'fv-model-head' },
      node('h3', {}, row.model),
      node('span', { class: 'fv-muted' }, `${num(row.requests)} 次请求 · ${num(row.total_tokens)} Token · 实付 ${usd(row.actual_cost)}`),
      row.multiplier !== null && row.multiplier !== undefined ? node('span', { class: 'pill blue' }, `倍率 ×${Number(row.multiplier.toFixed(4))}`) : null,
      missing ? node('span', { class: 'pill gray' }, '未收录官方价') : null),
    rows.length ? node('div', { class: 'table-wrap' }, node('table', { class: 'table fv-table' },
      node('thead', {}, node('tr', {}, ['类型', 'Token 数', '官方价', '实际价', '对比'].map((h, i) => node('th', { class: i ? 'fv-num' : '' }, h)))),
      node('tbody', {}, rows))) : node('p', { class: 'fv-muted' }, '本周期无 Token 用量'));
}
async function pricing() {
  const state = { group_id: '', range: '30', ...presetRange('30') };
  const results = node('div', { class: 'fv-models' });
  const controls = node('div', { class: 'fv-toolbar' });
  const root = node('section', { class: 'fv' }, controls, results);
  featureMain.replaceChildren(root);
  let sequence = 0;
  function drawControls(groups) {
    const groupSelect = node('select', { class: 'fv-input', 'aria-label': '分组', onchange: event => { state.group_id = event.target.value; load(); } },
      node('option', { value: '' }, '全部分组'),
      groups.map(group => node('option', { value: String(group.id) }, group.name || `分组 #${group.id}`)));
    groupSelect.value = state.group_id;
    const rangeSelect = node('select', { class: 'fv-input', 'aria-label': '时间段', onchange: event => {
      state.range = event.target.value;
      if (state.range !== 'custom') Object.assign(state, presetRange(state.range));
      drawControls(groups); load();
    } }, [['7', '近 7 天'], ['30', '近 30 天'], ['month', '本月'], ['custom', '自定义']].map(([value, label]) => node('option', { value }, label)));
    rangeSelect.value = state.range;
    const date = key => node('input', { type: 'date', class: 'fv-input', 'aria-label': key === 'start_date' ? '开始日期' : '结束日期', value: state[key], onchange: event => { if (event.target.value) { state[key] = event.target.value; load(); } } });
    const current = groups.find(group => String(group.id) === state.group_id);
    controls.replaceChildren(...[groupSelect, current ? groupBadge(current) : null, rangeSelect, ...(state.range === 'custom' ? [date('start_date'), node('span', { class: 'fv-muted' }, '至'), date('end_date')] : [])].filter(Boolean));
  }
  async function load() {
    const current = ++sequence;
    const query = new URLSearchParams({ start_date: state.start_date, end_date: state.end_date, ...(state.group_id ? { group_id: state.group_id } : {}) });
    try {
      const data = await request(`/models?${query}`);
      if (current !== sequence) return;
      drawControls(data.groups || []);
      results.replaceChildren(...(data.models.length ? [...data.models.map(modelCard), node('p', { class: 'fv-muted fv-foot' }, '单位：USD / 百万 Token。实际价 = 官方价 × 实际倍率（实付 ÷ 标准计费）。')]
        : [node('p', { class: 'fv-muted' }, '当前条件下暂无使用记录')]));
    } catch (error) { if (current === sequence) results.replaceChildren(node('p', { class: 'fv-error', role: 'alert' }, error.message)); }
  }
  drawControls([]);
  await load();
}
// Ranking mirrors upstream's usage table (components/admin/usage/UserTokenRanking.vue) plus one reward column.
// Every Tailwind class stays in a full literal so the style build keeps it.
const RK_CARD = 'card mb-0';
const RK_TOOLBAR = 'flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-dark-700/50 sm:px-6';
const RK_NOTE = 'text-xs text-gray-400 dark:text-gray-500';
const RK_TOOLS = 'flex items-center gap-3';
const RK_LIMIT = 'w-28';
// block w-full makes the native control fill its box like upstream's select trigger.
const RK_SELECT = 'block w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-900 dark:border-dark-600 dark:bg-dark-800 dark:text-gray-100';
const RK_SCROLL = 'overflow-x-auto';
const RK_TABLE = 'w-full min-w-max divide-y divide-gray-200 dark:divide-dark-700';
const RK_HEAD = 'bg-gray-50 dark:bg-dark-800';
const RK_TH_RANK = 'w-16 px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-dark-400 sm:px-6';
const RK_TH_USER = 'px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-dark-400';
const RK_TH_REWARD = 'px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-dark-400 whitespace-nowrap';
// The sortable th keeps the upstream typography; its button fills the cell and owns the colour and hover.
// Preflight resets text-transform on buttons, so the button repeats uppercase.
const RK_TH_SORT = 'p-0 text-xs font-medium uppercase tracking-wider';
const RK_SORT = 'block w-full cursor-pointer select-none whitespace-nowrap px-4 py-3 text-right text-xs font-medium uppercase tracking-wider transition-colors hover:bg-gray-100 dark:hover:bg-dark-700';
const RK_SORT_ON = 'text-primary-600 dark:text-primary-400';
const RK_SORT_OFF = 'text-gray-500 dark:text-dark-400';
const RK_BODY = 'divide-y divide-gray-200 bg-white dark:divide-dark-700 dark:bg-dark-900';
const RK_ROW = 'transition-colors hover:bg-gray-50 dark:hover:bg-dark-700/40';
const RK_RANK_CELL = 'px-4 py-3 sm:px-6';
const RK_MEDAL = 'inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold';
const RK_MEDALS = ['bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-400', 'bg-gray-200 text-gray-600 dark:bg-gray-500/20 dark:text-gray-300', 'bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-400'];
const RK_PLAIN = 'inline-block w-6 text-center text-sm tabular-nums text-gray-400';
const RK_USER = 'max-w-[260px] truncate px-4 py-3 text-sm font-medium text-gray-700 dark:text-gray-200';
const RK_ME = 'badge badge-primary ml-1';
const RK_USER_ID = 'ml-1 font-normal text-gray-400 dark:text-gray-500';
const RK_NUM = 'whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-500 dark:text-gray-400';
const RK_TOTAL = 'whitespace-nowrap px-4 py-3 text-right text-sm font-medium tabular-nums text-gray-900 dark:text-gray-100';
const RK_COST = 'whitespace-nowrap px-4 py-3 text-right text-sm font-medium tabular-nums text-green-600 dark:text-green-400';
const RK_REWARD = 'whitespace-nowrap px-4 py-3 text-left text-sm';
const RK_PRIZE = 'badge badge-warning';
const RK_NONE = 'text-gray-400 dark:text-gray-500';
const RK_EMPTY = 'py-12 text-center text-sm text-gray-400';
const RK_LOADING = 'py-12 text-center';
const RK_COLUMNS = [['requests', '请求数'], ['input_tokens', '输入 Token'], ['output_tokens', '输出 Token'], ['cache_tokens', '缓存 Token'], ['total_tokens', '总 Token'], ['actual_cost', '费用']];
const RK_LIMITS = [20, 50, 100, 200];
const RK_SPAN = RK_COLUMNS.length + 3;
// Upstream formatCompactNumber: 950, 1.2K, 18.3M, 1.1B.
const compact = value => { const n = Number(value) || 0; const [scale, unit] = n >= 1e9 ? [1e9, 'B'] : n >= 1e6 ? [1e6, 'M'] : n >= 1e3 ? [1e3, 'K'] : [1, '']; return unit ? (n / scale).toFixed(1) + unit : String(n); };
// zh-CN with numeric fields prints "11/30", so the Chinese order 11月30日 is assembled from the parts.
function settlementText(rewards) {
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', { timeZone: rewards.timezone, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).formatToParts(new Date(rewards.settlement_at)).map(part => [part.type, part.value]));
    return `${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}:${parts.second}`;
  } catch (_) { return ''; }
}
function rankingSubtitle(data) {
  if (!data) return '';
  // The month belongs to the window, not to the browser clock: start_date read in the window's zone.
  let month = Number(String(data.start_date).slice(5, 7));
  try { month = Number(new Intl.DateTimeFormat('en-US', { timeZone: data.timezone, month: 'numeric' }).format(new Date(`${data.start_date}T12:00:00Z`))); } catch (_) { /* keep the calendar month of start_date */ }
  const text = `${month || ''} 月用量排行`;
  if (!data.rewards) return text;
  const settlement = settlementText(data.rewards);
  // The cutoff ends the statistics; it never promises a payout at that second.
  return text + ' · ' + (settlement ? `统计至 ${settlement}，` : '') + (data.rewards.mode === 'approval' ? '审核后发放' : '月结后自动发放');
}
function rankingRow(item) {
  const rank = Number(item.rank) || 0;
  // A row the API marks tentative has no settled order yet (the upstream list was cut inside a tie):
  // plain number instead of a medal, and the reward cell says so instead of showing a prize.
  const medal = item.tentative ? '' : RK_MEDALS[rank - 1];
  return node('tr', { class: RK_ROW },
    node('td', { class: RK_RANK_CELL }, medal ? node('span', { class: `${RK_MEDAL} ${medal}` }, String(rank))
      : node('span', { class: RK_PLAIN, title: item.tentative ? '榜单可能不完整，该名次暂定' : undefined }, String(rank))),
    node('td', { class: RK_USER, title: item.label },
      item.label,
      item.is_me ? node('span', { class: RK_ME }, '我') : null,
      item.user_id ? node('span', { class: RK_USER_ID }, `#${item.user_id}`) : null),
    node('td', { class: RK_NUM }, Number(item.requests).toLocaleString()),
    node('td', { class: RK_NUM }, compact(item.input_tokens)),
    node('td', { class: RK_NUM }, compact(item.output_tokens)),
    node('td', { class: RK_NUM }, compact(item.cache_tokens)),
    node('td', { class: RK_TOTAL }, compact(item.total_tokens)),
    node('td', { class: RK_COST }, `$${Number(item.actual_cost).toFixed(4)}`),
    // The API sends the rule amount ready to show (30.00, 0.50, 0.004): a sub-cent prize must not read as $0.00.
    node('td', { class: RK_REWARD }, item.reward == null ? node('span', { class: RK_NONE }, item.tentative ? '暂定' : '—') : node('span', { class: RK_PRIZE }, `$${item.reward}`)));
}
// data === null draws the upstream loading row.
function rankingTable(data) {
  const rows = Array.isArray(data?.ranking) ? data.ranking : [];
  const state = { sort: 'actual_cost', limit: 50 };
  const head = node('thead', { class: RK_HEAD }), body = node('tbody', { class: RK_BODY });
  function draw() {
    // Sorting only reorders rows on screen: rank and reward stay those of the official settlement order.
    const amount = (item, key) => Number(item[key]) || 0;
    const sorted = state.sort === 'actual_cost' ? rows : [...rows].sort((a, b) => amount(b, state.sort) - amount(a, state.sort) || amount(b, 'actual_cost') - amount(a, 'actual_cost') || amount(b, 'total_tokens') - amount(a, 'total_tokens') || a.rank - b.rank);
    const placeholder = (classes, content) => node('tr', {}, node('td', { colspan: RK_SPAN, class: classes }, content));
    head.replaceChildren(node('tr', {},
      node('th', { class: RK_TH_RANK }, '#'),
      node('th', { class: RK_TH_USER }, '用户'),
      RK_COLUMNS.map(([key, label]) => node('th', { class: RK_TH_SORT, 'aria-sort': key === state.sort ? 'descending' : undefined },
        node('button', { type: 'button', class: `${RK_SORT} ${key === state.sort ? RK_SORT_ON : RK_SORT_OFF}`, onclick: () => { if (key !== state.sort) { state.sort = key; draw(); } } },
          label, key === state.sort ? [' ', node('span', { 'aria-hidden': 'true' }, '↓')] : null))),
      node('th', { class: RK_TH_REWARD }, '预计奖励')));
    body.replaceChildren(...(data ? rows.length ? sorted.slice(0, state.limit).map(rankingRow)
      : [placeholder(RK_EMPTY, '暂无数据')] : [placeholder(RK_LOADING, node('span', { class: 'spinner', role: 'status', 'aria-label': '加载中' }))]));
  }
  const limit = node('select', { class: RK_SELECT, 'aria-label': '显示数量', onchange: event => { state.limit = Number(event.target.value); draw(); } }, RK_LIMITS.map(value => node('option', { value: String(value) }, `Top ${value}`)));
  limit.value = String(state.limit);
  draw();
  return node('section', { class: RK_CARD },
    node('div', { class: RK_TOOLBAR },
      node('p', { class: RK_NOTE }, rankingSubtitle(data)),
      node('div', { class: RK_TOOLS },
        // A capped upstream list is cut before ineligible accounts are dropped, so only the listed rows are
        // counted - and the caveat shows even when none of the cut rows is eligible.
        data && (rows.length || data.truncated) ? node('span', { class: RK_NOTE }, data.truncated ? `已列出 ${rows.length} 位用户，榜单可能不完整` : `共 ${rows.length} 位用户`) : null,
        node('div', { class: RK_LIMIT }, limit))),
    node('div', { class: RK_SCROLL }, node('table', { class: RK_TABLE }, head, body)));
}
async function ranking() {
  featureMain.replaceChildren(rankingTable(null));
  featureMain.replaceChildren(rankingTable(await request('/ranking')));
}
async function benchmark(role) {
  const gallery = node('div'); featureMain.append(gallery);
  const controller = helperGallery(gallery, { request, settingsUrl: role === 'admin' ? '/_helper/setup#analytics' : undefined });
  await controller.ready;
}
async function initialize() {
  document.title = `${titles[feature] || '用户工具'} · sub2api Helper`;
  featureMain.setAttribute('aria-label', titles[feature] || '用户工具');
  try {
    if (!titles[feature]) throw new Error('此功能页面不存在。');
    if (!loginToken) throw new Error('请从 sub2api 的自定义菜单进入此页面，以验证你的登录身份。');
    const session = await request('/session', 'POST', { token: loginToken }); loginToken = '';
    portalSession = session.session;
    if (!portalSession) throw new Error('登录身份验证失败，请从 sub2api 菜单重新打开。');
    const modules = await request('/modules');
    if (!modules[feature]) throw new Error('此功能尚未上线，或已被管理员关闭。');
    if (feature === 'benchmark') { featureMain.replaceChildren(); await benchmark(session.role); return; }
    if (feature === 'pricing') await pricing(); else await ranking();
  } catch (err) {
    loginToken = '';
    featureMain.replaceChildren(node('div', { class: 'feature-context', role: 'alert' }, node('p', {}, err.message)));
  }
}
initialize();

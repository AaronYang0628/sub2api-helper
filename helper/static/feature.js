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
const RANK_CLASS = ['', 'gold', 'silver', 'bronze'];
const RANK_NAMES = ['', '冠军', '亚军', '季军'];
function settlementText(rewards) {
  const date = new Date(rewards.settlement_at);
  try { return new Intl.DateTimeFormat('zh-CN', { timeZone: rewards.timezone, year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date); } catch (_) { return rewards.settlement_at; }
}
function rewardPanel(data) {
  const rewards = data.rewards;
  return node('section', { class: 'card fv-rewards' },
    node('h3', {}, '月度奖项设置'),
    node('div', { class: 'fv-prizes' }, rewards.rewards.map(item => node('div', { class: `fv-prize fv-${RANK_CLASS[item.rank]}` },
      node('span', { class: 'fv-prize-rank' }, `第 ${item.rank} 名 · ${RANK_NAMES[item.rank]}`),
      node('strong', {}, Number(item.amount) > 0 ? `${usd(item.amount, 2)} 余额` : '无奖励')))),
    node('ul', { class: 'fv-rules' },
      node('li', {}, node('b', {}, '评选周期'), `自然月 ${data.start_date.slice(0, 7)}（${rewards.timezone}），当前统计至 ${data.end_date}`),
      node('li', {}, node('b', {}, '结算时间'), `${settlementText(rewards)}（${rewards.timezone}）结算本月排名，${rewards.mode === 'approval' ? '经管理员审核后发放' : '自动发放'}`),
      node('li', {}, node('b', {}, '评选规则'), '仅统计状态正常的普通用户，按当月实际费用从高到低排名；费用相同时依次比较 Token 数与用户 ID'),
      node('li', {}, node('b', {}, '发放方式'), '奖励直接入账到用户余额')));
}
function rankingTable(data) {
  const prizes = new Map((data.rewards?.rewards || []).map(item => [item.rank, item.amount]));
  const hasPrize = prizes.size > 0;
  const head = ['排名', '用户', '实际费用（USD）', '请求数', 'Token', ...(hasPrize ? ['奖励'] : [])];
  const rows = data.ranking.map(item => {
    const tier = item.rank <= 3 ? RANK_CLASS[item.rank] : '';
    const amount = prizes.get(item.rank);
    return node('tr', { class: tier ? `fv-row-${tier}` : '' },
      node('td', {}, tier ? node('span', { class: `fv-medal fv-${tier}`, title: RANK_NAMES[item.rank] }, String(item.rank)) : node('span', { class: 'fv-rank-plain' }, String(item.rank))),
      node('td', {}, item.label, item.is_me ? node('span', { class: 'pill blue fv-me' }, '我') : null),
      node('td', { class: 'fv-num' }, usd(item.actual_cost)),
      node('td', { class: 'fv-num' }, num(item.requests)),
      node('td', { class: 'fv-num' }, num(item.tokens)),
      ...(hasPrize ? [node('td', { class: 'fv-num fv-strong' }, tier && Number(amount) > 0 ? `${usd(amount, 2)}` : '—')] : []));
  });
  return node('div', { class: 'card fv-ranking' },
    node('div', { class: 'fv-ranking-period' }, `本月排名 ${data.start_date} — ${data.end_date}`),
    rows.length ? node('div', { class: 'table-wrap' }, node('table', { class: 'table fv-table' },
      node('thead', {}, node('tr', {}, head.map((h, i) => node('th', { class: i >= 2 ? 'fv-num' : '' }, h)))), node('tbody', {}, rows)))
      : node('p', { class: 'fv-muted' }, '本月暂无使用记录'));
}
async function ranking() {
  const data = await request('/ranking');
  featureMain.replaceChildren(node('section', { class: 'fv' }, ...[data.rewards ? rewardPanel(data) : null, rankingTable(data)].filter(Boolean)));
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

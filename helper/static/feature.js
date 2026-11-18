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
function table(headers, rows) { return node('div', { class: 'table-wrap' }, node('table', { class: 'table' }, node('thead', {}, node('tr', {}, headers.map(h => node('th', {}, h)))), node('tbody', {}, rows))); }
function card(title, subtitle, body) { return node('section', { class: 'card' }, node('div', { class: 'card-header' }, node('div', {}, node('h2', {}, title), subtitle ? node('p', {}, subtitle) : null)), body); }
function metricTable(data, ranking) {
  let rows = [];
  if (Array.isArray(data)) rows = data;
  else for (const key of ranking ? ['ranking', 'users', 'items'] : ['models', 'items', 'stats']) { if (Array.isArray(data?.[key])) { rows = data[key]; break; } }
  if (!rows.length) return node('div', { class: 'empty' }, helperIcon('inbox', 'empty-icon'), node('strong', {}, '暂无使用记录'), node('p', {}, '当前统计周期内没有可展示的数据。'));
  const labels = { rank: '排名', username: '用户', model: '模型', model_name: '模型', name: '名称', requests: '请求数', request_count: '请求数', tokens: 'Token', input_tokens: '输入 Token', output_tokens: '输出 Token', total_tokens: '总 Token', actual_cost: '实际费用（USD）', cost: '费用（USD）', total_cost: '费用（USD）', percentage: '占比' };
  // Display only known metric keys; never accidentally surface future upstream identity fields.
  const keys = Object.keys(rows[0]).filter(key => Object.hasOwn(labels, key) && typeof rows[0][key] !== 'object');
  return table(keys.map(key => labels[key]), rows.map(item => node('tr', {}, keys.map(key => {
    const value = item[key];
    return node('td', {}, typeof value === 'number' ? /cost/.test(key) ? `$${value.toFixed(4)}` : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : value ?? '—');
  }))));
}
async function benchmark(role) {
  const gallery = node('div'); featureMain.append(gallery);
  const controller = helperGallery(gallery, { request, settingsUrl: role === 'admin' ? '/_helper/setup#analytics' : undefined });
  await controller.ready;
}
async function initialize() {
  document.title = `${titles[feature] || '用户工具'} · sub2api Helper`;
  try {
    if (!titles[feature]) throw new Error('此功能页面不存在。');
    if (!loginToken) throw new Error('请从 sub2api 的自定义菜单进入此页面，以验证你的登录身份。');
    const session = await request('/session', 'POST', { token: loginToken }); loginToken = '';
    portalSession = session.session;
    if (!portalSession) throw new Error('登录身份验证失败，请从 sub2api 菜单重新打开。');
    const modules = await request('/modules');
    if (!modules[feature]) throw new Error('此功能尚未上线，或已被管理员关闭。');
    const anonymous = session.role !== 'admin';
    const subtitle = feature === 'benchmark' ? null : feature === 'pricing' ? '查看模型请求、Token 使用量与费用。' : anonymous ? '匿名展示用户用量。' : '按用户查看用量与费用。';
    featureMain.replaceChildren(node('div', { class: 'page-head' }, node('div', {}, node('h1', {}, titles[feature]), subtitle ? node('p', {}, subtitle) : null)));
    if (feature === 'benchmark') { await benchmark(session.role); return; }
    const data = await request(feature === 'pricing' ? '/models' : '/ranking');
    featureMain.append(card(feature === 'pricing' ? '我的模型使用统计' : '用户用量榜单', data.start_date && data.end_date ? `${data.start_date} — ${data.end_date}` : '当前统计周期', metricTable(data, feature === 'ranking')));
  } catch (err) {
    loginToken = ''; featureMain.replaceChildren(node('section', { class: 'card feature-login' }, node('h1', {}, '暂时无法打开此功能'), node('p', {}, err.message), node('p', { class: 'tiny' }, '如果刚刚登录或更新配置，请回到 sub2api 后重新打开菜单。')));
  }
}
initialize();

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
function note(text, kind = '') { return node('div', { class: `notice ${kind}` }, helperIcon('infoCircle', 'notice-icon'), node('span', {}, text)); }
function field(label, input, help) { return node('label', { class: 'field' }, node('span', {}, label), input, help ? node('small', {}, help) : null); }
async function request(endpoint, method = 'GET', data) {
  const response = await fetch(`/api/portal${endpoint}`, { method, credentials: 'omit', headers: { ...(portalSession ? { 'X-Portal-Session': portalSession } : {}), ...(data !== undefined ? { 'Content-Type': 'application/json' } : {}) }, ...(data !== undefined ? { body: JSON.stringify(data) } : {}) });
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
async function benchmark() {
  const model = node('input', { name: 'model', type: 'text', required: true, placeholder: '例如 gpt-4.1-mini', maxlength: 150 });
  const group = node('select', { name: 'group_id', required: true });
  const key = node('select', { name: 'key_id', required: true });
  const format = node('select', { name: 'api_format' }, node('option', { value: 'chat' }, 'Chat Completions'), node('option', { value: 'responses' }, 'Responses'));
  const resultView = node('div', { hidden: true }); let formPane; const error = node('div', { class: 'form-error', role: 'alert' });
  const submit = node('button', { type: 'submit', class: 'button primary', disabled: true }, '开始检测');
  let keys = [];
  function updateKeys() {
    const matches = keys.filter(item => String(item.group_id) === group.value);
    key.replaceChildren(...matches.map(item => node('option', { value: item.id }, `${item.name || 'API Key'} · #${item.id}`)));
    submit.disabled = !matches.length;
  }
  group.addEventListener('change', updateKeys);
  async function loadOptions() {
    submit.disabled = true; error.textContent = '';
    try {
      const options = await request('/benchmark/options');
      keys = (options.keys || []).filter(item => item.group_id !== undefined && item.group_id !== null);
      const groups = [...new Map(keys.map(item => [item.group_id, item])).values()];
      group.replaceChildren(...groups.map(item => node('option', { value: item.group_id }, item.group_name || `分组 #${item.group_id}`)));
      if (!groups.length) { group.append(node('option', { value: '' }, '暂无可用的分组 API Key')); error.textContent = '你还没有可用的分组 API Key，请先在 sub2api 中创建后再刷新。'; }
      updateKeys();
    } catch (err) { error.textContent = err.message; }
  }
  const form = node('form', { class: 'benchmark-form', onsubmit: async event => {
    event.preventDefault(); if (submit.disabled) return; if (!confirm('检测将使用所选分组的 API Key 调用模型并消耗你的额度。确认开始？')) return;
    submit.disabled = true; error.textContent = ''; formPane.hidden = true; resultView.hidden = false;
    resultView.replaceChildren(node('div', { class: 'loading' }, node('span', { class: 'spinner' }), '正在运行检测，请稍候…'));
    try {
      const data = await request('/benchmark', 'POST', { model: model.value.trim(), key_id: Number(key.value), group_id: Number(group.value), api_format: format.value });
      const result = helperBenchmarkResults(data, { groupName: group.selectedOptions[0]?.textContent, onBack: () => {
        resultView.replaceChildren(); resultView.hidden = true; formPane.hidden = false; model.focus();
      } });
      resultView.replaceChildren(result); result.querySelector('h2')?.focus();
    } catch (err) { resultView.replaceChildren(); resultView.hidden = true; formPane.hidden = false; error.textContent = err.message; } finally { submit.disabled = !key.value; }
  } }, field('模型名称', model), field('接口格式', format), field('检测分组', group), field('调用 API Key', key, '直接使用你已有的分组 Key，密钥内容不会传到浏览器。'), node('div', { class: 'wide button-group' }, submit, node('button', { type: 'button', class: 'button', onclick: loadOptions }, '刷新可用分组'), error));
  formPane = node('div', {}, note('检测将运行固定题目，实际调用模型并消耗所选 API Key 的额度。成绩仅供参考，不能作为全面的模型能力评估。', 'warning'), card('运行模型检测', '选择分组、模型与接口格式。', node('div', { class: 'card-body' }, form)));
  featureMain.append(formPane, resultView);
  await loadOptions();
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
    featureMain.replaceChildren(node('div', { class: 'page-head' }, node('div', {}, node('h1', {}, titles[feature]), node('p', {}, feature === 'benchmark' ? '让模型用实际回答展示能力。' : feature === 'pricing' ? '查看自己的模型请求、Token 使用量与费用。' : '匿名展示使用情况，不公开用户邮箱和账号身份。'))));
    if (feature === 'benchmark') { await benchmark(); return; }
    const data = await request(feature === 'pricing' ? '/models' : '/ranking');
    if (feature === 'ranking') featureMain.append(note('用户身份已匿名处理，榜单仅展示排名和用量。'));
    featureMain.append(card(feature === 'pricing' ? '我的模型使用统计' : '用户用量榜单', data.start_date && data.end_date ? `${data.start_date} — ${data.end_date}` : '当前统计周期', metricTable(data, feature === 'ranking')));
  } catch (err) {
    loginToken = ''; featureMain.replaceChildren(node('section', { class: 'card feature-login' }, node('h1', {}, '暂时无法打开此功能'), node('p', {}, err.message), node('p', { class: 'tiny' }, '如果刚刚登录或更新配置，请回到 sub2api 后重新打开菜单。')));
  }
}
initialize();

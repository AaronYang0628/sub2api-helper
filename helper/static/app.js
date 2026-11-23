'use strict';
const ADMIN_API = '/_helper/api/admin';
const launchParams = new URLSearchParams(location.search);
const sourceHostHint = launchParams.get('src_host') || '';
const requestedTheme = launchParams.get('theme');
if (requestedTheme === 'dark' || requestedTheme === 'light') { document.documentElement.dataset.theme = requestedTheme; document.documentElement.classList.toggle('dark', requestedTheme === 'dark'); }
// Embedded user JWTs are never used to authenticate management operations.
history.replaceState(null, '', location.pathname + location.hash);
const isNativeConsole = location.pathname.endsWith('/console');
const SESSION_KEY = isNativeConsole ? 'helper_native_console_session' : 'helper_setup_session';
function readSession() { try { return sessionStorage.getItem(SESSION_KEY) || ''; } catch (_) { return ''; } }
function saveSession(value) { try { if (value) sessionStorage.setItem(SESSION_KEY, value); else sessionStorage.removeItem(SESSION_KEY); } catch (_) { /* Browser may block storage in an iframe; memory still works. */ } }
const $ = (selector, root = document) => root.querySelector(selector);
const main = $('#main');
const editor = $('#editor');
const dayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const views = [
  ['rules', '规则', 'clock'],
  ['modules', '功能与菜单', 'grid'],
  ['analytics', '智商检测', 'chartBar'],
  ['rewards', '排名与奖励', 'trophy'],
  ['system', '运行与系统', 'shield']
];
let token = readSession();
let configured = false;
let authorized = Boolean(token);
let sessionChecked = isNativeConsole;
let showCredentialForm = !authorized;
let state = { status: {}, accounts: [], users: [], schedules: [], rules: [], emailTemplates: [], automations: [], modules: {} };
let generation = 0;
let toastTimer;
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'checked' || key === 'disabled' || key === 'value') node[key] = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  });
  children.flat(Infinity).forEach(child => {
    if (child !== undefined && child !== null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}
function button(text, action, kind = '', attrs = {}) {
  const symbols = { '↻': 'refresh', '＋': 'plus', '✉': 'mail', '✕': 'x' };
  const first = typeof text === 'string' ? text.trim()[0] : '';
  const iconName = symbols[first];
  return el('button', { type: 'button', class: `button ${kind}`, onclick: action, ...attrs }, iconName ? helperIcon(iconName) : null, typeof text === 'string' ? text.replace(/^[↻＋✉✕]\s*/, '').replace(/\s*→$/, '') : text);
}
function pill(text, kind = '') { return el('span', { class: `pill ${kind}` }, text); }
function notice(text, kind = '') { return el('div', { class: `notice ${kind}` }, helperIcon(kind === 'error' ? 'exclamationCircle' : 'infoCircle', 'notice-icon'), el('span', {}, text)); }
function empty(title, text = '') { return el('div', { class: 'empty' }, helperIcon('inbox', 'empty-icon'), el('strong', {}, title), el('p', {}, text)); }
function fmtTime(value) { if (!value) return '暂无记录'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', { hour12: false }); }
function autoRuleName(prefix, suffix, limit = 100) {
  const cleanPrefix = String(prefix || '').trim();
  if ((cleanPrefix + suffix).length <= limit) return cleanPrefix + suffix;
  return cleanPrefix.slice(0, Math.max(1, limit - suffix.length - 1)).trimEnd() + '…' + suffix;
}
function errorText(error) { return error?.message || String(error); }
function toast(message, error = false) {
  const target = $('#toast');
  const dismiss = () => { target.hidden = true; clearTimeout(toastTimer); };
  target.replaceChildren(el('div', { class: 'toast-row' }, helperIcon(error ? 'exclamationCircle' : 'checkCircle'), el('p', { class: 'toast-message' }, message), el('button', { type: 'button', class: 'toast-close', 'aria-label': '关闭通知', onclick: dismiss }, helperIcon('x'))));
  target.className = `toast ${error ? 'toast-error' : 'toast-success'}`; target.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(dismiss, error ? 6500 : 3500);
}
async function api(path, method = 'GET', body) {
  const headers = {};
  if (!isNativeConsole) headers['X-Helper-UI'] = '1';
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${ADMIN_API}${path}`, {
    method,
    credentials: isNativeConsole ? 'omit' : 'same-origin',
    redirect: 'error',
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {})
  });
  let data; try { data = await response.json(); } catch (_) { data = null; }
  if (!response.ok) {
    if (response.status === 401) {
      token = '';
      saveSession('');
      authorized = false;
      showCredentialForm = true;
      setConnectionState(false);
    }
    const detail = data?.detail || data?.message || `请求失败 (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}
async function run(action, message, reload = true) {
  try { await action(); if (message) toast(message); if (reload) await render(); }
  catch (error) { toast(errorText(error), true); }
}
function pageHead(title, desc, actions) {
  if (isNativeConsole) {
    main.setAttribute('aria-label', title);
    return actions ? el('div', { class: 'page-head page-head-compact' }, actions) : null;
  }
  main.removeAttribute('aria-label');
  return el('div', { class: 'page-head' }, el('div', {}, el('h1', {}, title), el('p', {}, desc)), actions);
}
function card(title, subtitle, content, action) { return el('section', { class: 'card' }, title ? el('div', { class: 'card-header' }, el('div', {}, el('h2', {}, title), subtitle ? el('p', {}, subtitle) : null), action) : null, content); }
function table(headers, rows) {
  return el('div', { class: 'table-wrap' }, el('table', { class: 'table' }, el('thead', {}, el('tr', {}, headers.map(h => el('th', {}, h)))), el('tbody', {}, rows)));
}
function row(cells) { return el('tr', {}, cells.map(cell => el('td', {}, cell))); }
function nameCell(name, secondary) { return el('div', {}, el('span', { class: 'name' }, name || '—'), secondary ? el('span', { class: 'secondary' }, secondary) : null); }
function switcher(checked, action, label = '') {
  const caption = el('span', {}, label);
  const values = ['已启用', '已暂停'].includes(label) ? ['已暂停', '已启用'] : ['开启', '关闭'].includes(label) ? ['关闭', '开启'] : label === '启用' ? ['启用', '已启用'] : null;
  const input = el('input', { type: 'checkbox', class: 'helper-switch-input', checked, 'aria-label': label || '启用', onchange: async event => {
    input.disabled = true;
    try { await action(event.target.checked); if (values) { caption.textContent = values[Number(input.checked)]; input.setAttribute('aria-label', caption.textContent); } }
    catch (error) { input.checked = !input.checked; toast(errorText(error), true); }
    finally { input.disabled = false; }
  } });
  return el('label', { class: 'helper-switch' }, input, el('span', { class: 'switch', 'aria-hidden': 'true' }, el('span', { class: 'switch-thumb' })), caption);
}
function field(label, input, help) { return el('label', { class: 'field' }, el('span', {}, label), input, help ? el('small', {}, help) : null); }
function input(name, value = '', type = 'text', extra = {}) { return el('input', { name, value, type, ...extra }); }
function textArea(name, value = '', extra = {}) { return el('textarea', { name, rows: 4, value, ...extra }); }
function modal(title, content, save, submitText = '保存') {
  const form = el('form'); const feedback = el('div', { class: 'form-error', role: 'alert' });
  const submit = el('button', { class: 'button primary', type: 'submit' }, submitText);
  form.append(el('div', { class: 'dialog-header' }, el('h2', {}, title), button('✕', () => editor.close(), 'ghost', { 'aria-label': '关闭' })), el('div', { class: 'dialog-body' }, content, feedback), el('div', { class: 'dialog-footer' }, button('取消', () => editor.close()), submit));
  form.addEventListener('submit', async event => {
    event.preventDefault(); feedback.textContent = ''; submit.disabled = true;
    try { await save(form); editor.close(); await render(); } catch (error) { feedback.textContent = errorText(error); } finally { submit.disabled = false; }
  });
  editor.replaceChildren(form); editor.showModal();
}
function setConnectionState(connected) {
  const summary = $('#connection-summary');
  const form = $('#connection-form');
  const badge = $('#connection-badge');
  const urlInput = $('#connection-url');
  const keyInput = $('#connection-key');
  const connectBtn = $('#connect');
  const disconnectBtn = $('#disconnect');
  const toggleBtn = $('#toggle-connection-form');
  const summaryUrl = $('#connection-summary-url');
  if (connected) {
    if (summary) summary.hidden = false;
    if (summaryUrl) summaryUrl.textContent = urlInput.value || state.status.upstream_url || '';
    if (form) form.hidden = !showCredentialForm;
    if (toggleBtn) toggleBtn.textContent = showCredentialForm ? '收起表单' : '更换 Key';
    badge.textContent = '已连接';
    badge.className = 'pill green';
    disconnectBtn.hidden = false;
    urlInput.readOnly = true;
    connectBtn.textContent = '更新管理员 Key';
    keyInput.placeholder = '输入新的管理员 API Key 以验证或更换';
  } else if (configured) {
    if (summary) summary.hidden = true;
    if (form) form.hidden = false;
    badge.textContent = '已绑定 · 待验证';
    badge.className = 'pill amber';
    disconnectBtn.hidden = true;
    urlInput.readOnly = true;
    connectBtn.textContent = '验证管理员身份';
    keyInput.placeholder = '服务端已加密保存 Key；输入管理员 Key 验证身份或更新';
  } else {
    if (summary) summary.hidden = true;
    if (form) form.hidden = false;
    badge.textContent = '未连接';
    badge.className = 'pill';
    disconnectBtn.hidden = true;
    urlInput.readOnly = false;
    connectBtn.textContent = '连接服务';
    keyInput.placeholder = '输入 sub2api 管理员 API Key';
  }
}
function disconnected() {
  setConnectionState(false);
  if (configured) {
    main.replaceChildren(el('section', { class: 'card connection-empty' },
      el('div', { class: 'connection-empty-symbol' }, helperIcon('shield')),
      el('h1', {}, '服务已绑定，请验证管理员身份'),
      el('p', {}, '管理员 API Key 已在服务端加密保存，已启用的调度与后台任务仍在正常运行。为保护管理权限，当前浏览器会话需要验证管理员 Key 后继续操作。'),
      el('div', { class: 'connection-empty-notes' },
        el('span', {}, helperIcon('clock'), '后台任务持续运行'),
        el('span', {}, helperIcon('shield'), 'Key 服务端加密保存'),
        el('span', {}, helperIcon('grid'), '不在浏览器保存明文'))));
    return;
  }
  main.replaceChildren(el('section', { class: 'card connection-empty' },
    el('div', { class: 'connection-empty-symbol' }, helperIcon('link')),
    el('h1', {}, '连接你的 sub2api'),
    el('p', {}, '在上方填写服务地址和管理员 API Key，开始配置账号调度、用户维护及自定义功能。'),
    el('div', { class: 'connection-empty-notes' },
      el('span', {}, helperIcon('clock'), '跨夜账号调度'),
      el('span', {}, helperIcon('mail'), '用户邮件提醒'),
      el('span', {}, helperIcon('grid'), '功能菜单管理'))));
}
async function initializeConnection() {
  const feedback = $('#connection-feedback');
  let loadFailed = false;
  try {
    const response = await fetch(`${ADMIN_API}/connection`, { credentials: isNativeConsole ? 'omit' : 'same-origin', redirect: 'error' });
    const info = await response.json();
    if (!response.ok) throw new Error(typeof info.detail === 'string' ? info.detail : '连接信息加载失败');
    configured = Boolean(info.configured);
    $('#connection-url').value = info.upstream_url || sourceHostHint;
  } catch (error) {
    loadFailed = true;
    $('#connection-url').value = sourceHostHint;
    feedback.textContent = `连接信息暂时无法读取：${errorText(error)}。可以在上方手动填写服务地址。`;
    feedback.className = 'connection-feedback error';
  }
  if (!isNativeConsole && !token && !configured) sessionChecked = true;
  await render();
  if (authorized) {
    feedback.textContent = '已恢复管理员会话。API Key 已在服务端加密保存，无需重复输入。';
    feedback.className = 'connection-feedback connected';
  } else if (!loadFailed && configured) {
    feedback.textContent = '服务已绑定且 Key 已在服务端加密保存；输入管理员 Key 验证身份后即可管理。';
    feedback.className = 'connection-feedback warning';
  }
}
$('#connection-form').addEventListener('submit', async event => {
  event.preventDefault(); const submit = $('#connect'); const feedback = $('#connection-feedback');
  submit.disabled = true; feedback.textContent = '正在验证管理员 Key…'; feedback.className = 'connection-feedback';
  try {
    const response = await fetch(`${ADMIN_API}/connection`, {
      method: 'POST',
      credentials: isNativeConsole ? 'omit' : 'same-origin',
      redirect: 'error',
      headers: { 'Content-Type': 'application/json', ...(!isNativeConsole ? { 'X-Helper-UI': '1' } : {}) },
      body: JSON.stringify({ url: $('#connection-url').value.trim(), admin_key: $('#connection-key').value.trim(), helper_url: location.origin })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || `连接失败 (${response.status})`));
    if (!data.session) throw new Error('服务未返回有效会话，请重试。');
    token = data.session; saveSession(token); configured = true; authorized = true; sessionChecked = true; showCredentialForm = false;
    $('#connection-key').value = '';
    if (data.upstream_url) $('#connection-url').value = data.upstream_url;
    feedback.textContent = data.warning || '管理员身份验证成功。API Key 已在服务端加密保存并从输入框清除。';
    feedback.className = `connection-feedback ${data.warning ? 'warning' : 'connected'}`;
    await render();
  } catch (error) { feedback.textContent = errorText(error); feedback.className = 'connection-feedback error'; }
  finally { submit.disabled = false; }
});
$('#toggle-connection-form')?.addEventListener('click', () => {
  showCredentialForm = !showCredentialForm;
  setConnectionState(authorized);
  if (showCredentialForm) $('#connection-key')?.focus();
});
async function overview() {
  const results = await Promise.allSettled(['/accounts', '/schedules', '/rules', '/modules', '/automations'].map(path => api(path)));
  results.forEach((res, i) => { if (res.status === 'fulfilled') state[['accounts', 'schedules', 'rules', 'modules', 'automations'][i]] = res.value; });
  const failed = results.filter(r => r.status === 'rejected');
  const stats = [
    ['管理账号', Array.isArray(state.accounts) ? state.accounts.length : '—', 'database', '来自 sub2api 的账号'],
    ['调度中', Array.isArray(state.accounts) ? state.accounts.filter(a => a.schedulable).length : '—', 'play', '账号当前参与请求调度'],
    ['定时任务', state.schedules.filter(s => s.enabled).length, 'clock', '已启用的时间窗口'],
    ['自动化规则', state.rules.filter(r => r.enabled).length + state.automations.filter(r => r.enabled).length, 'cog', '已启用的自动化与提醒规则']
  ];
  const details = [ ['服务地址', state.status.upstream_url || '—'], ['默认时区', state.status.timezone || 'Asia/Shanghai'], ['邮件服务', state.status.mail_configured ? pill('已配置', 'green') : pill('未配置', 'amber')], ['外部访问地址', state.status.public_url || '未设置'], ['调度服务', pill(state.status.engine?.running ? '运行中' : '未运行', state.status.engine?.running ? 'green' : 'amber')], ['最近检查', fmtTime(state.status.engine?.last_tick_at)], ['Helper 版本', state.status.version || '—'] ];
  return [pageHead('工作空间概览', '账号调度、用户维护与内置功能，集中管理。', button('↻  刷新状态', () => render())),
    failed.length ? notice(`部分上游数据未能加载：${failed.map(x => errorText(x.reason)).join('；')}`, 'warning') : null, state.status.engine?.last_error ? notice(`后台任务最近一次错误：${state.status.engine.last_error}。请查看执行记录。`, 'warning') : null,
    el('div', { class: 'stats-grid' }, stats.map(([label, value, icon, desc]) => el('div', { class: 'stat-card' }, el('div', { class: 'stat-icon stat-icon-primary' }, helperIcon(icon)), el('div', { class: 'stat-content' }, el('div', { class: 'stat-label' }, label), el('div', { class: 'stat-value' }, value), el('div', { class: 'stat-caption' }, desc))))),
    el('div', { class: 'overview-grid' }, card('连接与环境', '密钥在服务端配置，仅展示连接信息', el('div', { class: 'card-body' }, details.map(([k, v]) => el('div', { class: 'detail-row' }, el('span', { class: 'muted' }, k), el('span', { class: 'value' }, v))))), card('开始管理', '为常见工作流配置自动化', el('div', { class: 'card-body quick-actions' }, [ ['schedules', 'clock', '设置账号免费时段', '例如每天 23:00 — 次日 09:00'], ['rules', 'cog', '维护不活跃用户', '先发送提醒邮件，成功后再禁用'], ['modules', 'grid', '上线用户功能', '自动同步到 sub2api 自定义菜单'] ].map(([href, icon, title, desc]) => el('a', { class: 'quick-action', href: `#${href}` }, el('span', { class: 'big-icon' }, helperIcon(icon)), el('div', {}, el('strong', {}, title), el('small', {}, desc)), helperIcon('chevronRight', 'arrow'))))))];
}
async function accountsPage() {
  state.accounts = await api('/accounts');
  const tbody = el('div');
  function paint(query = '') {
    const entries = state.accounts.filter(a => `${a.name} ${a.id} ${a.platform}`.toLowerCase().includes(query.toLowerCase()));
    tbody.replaceChildren(entries.length ? table(['账号', '平台', '状态', '参与调度', '分组'], entries.map(a => row([
      nameCell(a.name, `ID ${a.id}`), pill(a.platform || '—', 'blue'), pill(a.status || '—', a.status === 'active' ? 'green' : 'red'),
      switcher(a.schedulable, async value => {
        if (!confirm(`确认${value ? '开启' : '关闭'}「${a.name}」的调度？如果由定时规则管理，请先停用规则。`)) throw new Error('已取消操作');
        await api(`/accounts/${a.id}/schedulable`, 'POST', { schedulable: value }); a.schedulable = value; toast('调度状态已更新');
      }, a.schedulable ? '开启' : '关闭'), (a.group_ids || []).join(', ') || '—'
    ]))) : empty('没有匹配的账号', '调整搜索条件，或先在 sub2api 中添加账号。'));
  }
  paint();
  const search = input('search', '', 'search', { placeholder: '搜索账号名称、ID 或平台', oninput: e => paint(e.target.value) });
  return [pageHead('账号管理（备用入口）', '未开启原站页内增强时，可在此直接控制账号是否参与请求调度。', el('div', { class: 'button-group' }, el('a', { href: '#system', class: 'button ghost' }, '← 返回运行与系统'), button('↻  刷新', () => render()))), notice('手动切换仅修改“参与调度”状态。被启用的定时规则管理的账号，需要先停用规则才能手动切换。'), card('', '', [el('div', { class: 'toolbar' }, el('div', { class: 'search' }, search), el('span', { class: 'count' }, `共 ${state.accounts.length} 个账号`)), tbody])];
}
async function editSchedule(item = {}) {
  try { state.accounts = await api('/accounts'); } catch (error) { toast(errorText(error), true); return; }
  const defaultZone = state.status.timezone || 'Asia/Shanghai';
  const startInput = input('start', item.start || '23:00', 'time', { required: true });
  const endInput = input('end', item.end || '09:00', 'time', { required: true });
  const nameInput = input('name', item.name || '', 'text', { maxlength: 100, placeholder: '留空将按所选账号与时段自动生成' });
  const zoneInput = input('timezone', item.timezone || defaultZone, 'text', { required: true });
  const weekdays = el('div', { class: 'checklist' }, dayNames.map((name, i) => el('label', { class: 'check-chip' }, input('weekdays', String(i), 'checkbox', { checked: (item.weekdays || [0, 1, 2, 3, 4, 5, 6]).includes(i) }), name)));
  const knownIds = new Set(state.accounts.map(a => a.id));
  const missingAccounts = (item.account_ids || [])
    .filter(id => !knownIds.has(id))
    .map(id => ({ id, name: `原关联账号 #${id}`, platform: '当前列表未返回' }));
  const selectableAccounts = [...state.accounts, ...missingAccounts];
  const picker = el('div', { class: 'account-picker' }, selectableAccounts.length ? selectableAccounts.map(a => el('label', {}, input('account_ids', String(a.id), 'checkbox', { checked: (item.account_ids || []).includes(a.id) }), `${a.name} · #${a.id} · ${a.platform || ''}`)) : el('span', { class: 'muted tiny' }, '上游暂无账号'));
  const summary = el('p', { class: 'schedule-summary', 'data-schedule-summary': 'true', role: 'status' });
  function updateSummary() {
    const selectedIds = [...picker.querySelectorAll('input:checked')].map(node => Number(node.value));
    const selectedAccounts = selectableAccounts.filter(a => selectedIds.includes(a.id));
    const accountText = !selectedAccounts.length ? '所选账号' : selectedAccounts.length === 1 ? selectedAccounts[0].name : `${selectedAccounts[0].name} 等 ${selectedAccounts.length} 个账号`;
    const selectedDays = [...weekdays.querySelectorAll('input:checked')].map(node => Number(node.value));
    const dayText = selectedDays.length === 7 ? '每天' : selectedDays.length ? selectedDays.map(n => dayNames[n]).join('、') : '未选择星期';
    const startVal = startInput.value || '23:00', endVal = endInput.value || '09:00';
    const overnight = endVal < startVal;
    const tz = zoneInput.value.trim() || defaultZone;
    summary.textContent = `${accountText} ${dayText} ${startVal} 开启、${overnight ? '次日 ' : ''}${endVal} 关闭（${tz}）`;
  }
  picker.addEventListener('change', updateSummary);
  weekdays.addEventListener('change', updateSummary);
  startInput.addEventListener('input', updateSummary);
  endInput.addEventListener('input', updateSummary);
  zoneInput.addEventListener('input', updateSummary);
  updateSummary();
  const moreSettings = el('details', { class: 'more-settings' },
    el('summary', {}, '更多设置'),
    el('div', { class: 'more-settings-body' },
      field('规则名称', nameInput, '留空时自动按所选账号和时段生成名称。'),
      field('执行日期', weekdays, '日期按开启时间所属日计算。周一 23:00 开启，周二 09:00 关闭。'),
      field('自定义时区', zoneInput, '默认使用当前配置的时区（IANA 名称，例如 Asia/Shanghai）。')));
  modal(item.id ? '编辑定时调度' : '新建定时调度', [
    field('管理账号', picker, '同一账号只能由一条已启用的规则管理。'),
    el('div', { class: 'field-row' }, field('开启时间', startInput), field('关闭时间', endInput)),
    summary,
    moreSettings,
    el('label', { class: 'switch' }, input('enabled', 'yes', 'checkbox', { checked: item.enabled ?? true }), '启用此规则'),
    notice('窗口内开启调度，窗口外关闭。保存已启用规则后，会在后台检查时生效。', 'warning')
  ], async form => {
    const data = new FormData(form); const ids = data.getAll('account_ids').map(Number); const days = data.getAll('weekdays').map(Number);
    if (!ids.length) throw new Error('请至少选择一个账号。'); if (!days.length) throw new Error('请至少选择一天。');
    const start = data.get('start'), end = data.get('end');
    const timezone = String(data.get('timezone') || '').trim() || item.timezone || defaultZone;
    const firstAccount = selectableAccounts.find(a => a.id === ids[0]);
    const defaultName = ids.length === 1 && firstAccount
      ? autoRuleName(firstAccount.name, ` ${start}-${end}`)
      : autoRuleName(`定时调度 (${ids.length} 个账号)`, ` ${start}-${end}`);
    const name = String(data.get('name') || '').trim() || item.name || defaultName;
    await api(`/schedules${item.id ? `/${item.id}` : ''}`, item.id ? 'PUT' : 'POST', { name, start, end, timezone, account_ids: ids, weekdays: days, enabled: data.has('enabled') }); toast('定时调度已保存');
  });
}
async function schedulesPage() {
  state.schedules = await api('/schedules');
  const rows = state.schedules.map(s => row([
    nameCell(s.name, `规则 #${s.id}`), el('span', { class: 'code' }, `${s.start} → ${s.end}${s.end < s.start ? '（次日）' : ''}`), s.timezone,
    el('span', { class: 'tiny' }, (s.weekdays || []).map(n => dayNames[n]).join(' / ')), `${s.account_ids.length} 个账号`,
    switcher(s.enabled, async enabled => { await api(`/schedules/${s.id}`, 'PUT', { name: s.name, account_ids: s.account_ids, start: s.start, end: s.end, timezone: s.timezone, weekdays: s.weekdays, enabled }); toast(enabled ? '规则已启用' : '规则已暂停'); }, s.enabled ? '已启用' : '已暂停'),
    el('div', { class: 'button-group' }, button('编辑', () => editSchedule(s), 'link'), button('删除', () => { if (confirm(`删除「${s.name}」？删除后停止管理这些账号，保留账号当前调度状态。`)) run(() => api(`/schedules/${s.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
  ]));
  return [pageHead('定时调度', '按每天的时间窗口自动开关账号调度，支持跨夜与独立时区。', button('＋  新建调度', () => editSchedule(), 'primary')), notice('调度采用持续校正：进程重启后根据当前时间恢复状态。暂停或删除规则会停止管理，保留账号当前状态。'), card('调度规则', `共 ${rows.length} 条规则`, rows.length ? table(['规则', '调度窗口', '时区', '开启日期', '管理范围', '启用状态', '操作'], rows) : empty('还没有定时调度', '添加账号与免费额度时间窗口，Helper 会自动控制调度。'))];
}
function sendEmail(user) {
  if (!state.status.mail_configured) { toast('请先在服务端配置 SMTP，配置完成后重启 Helper。', true); return; }
  modal(`发送邮件 · ${user.email}`, [notice('邮件通过 Helper 配置的 SMTP 发送，不会使用或修改 sub2api 邮件模板。'), field('邮件主题', input('subject', '', 'text', { required: true, maxlength: 200 })), field('邮件正文', textArea('body', '', { required: true, rows: 7 }), '以纯文本邮件发送。')], async form => {
    if (!confirm(`确认向 ${user.email} 发送此邮件？`)) throw new Error('已取消发送。'); const data = new FormData(form);
    await api(`/users/${user.id}/email`, 'POST', { subject: data.get('subject'), body: data.get('body') }); toast('邮件已发送');
  }, '发送邮件');
}
async function usersPage() {
  state.users = await api('/users'); const target = el('div');
  function paint(query = '', onlyActive = true) {
    const users = state.users.filter(u => (!onlyActive || u.status === 'active') && `${u.email} ${u.username} ${u.id}`.toLowerCase().includes(query.toLowerCase()));
    target.replaceChildren(users.length ? table(['用户', '状态', '角色', '最后模型调用', '注册时间', '操作'], users.map(u => row([
      nameCell(u.email, u.username ? `${u.username} · #${u.id}` : `#${u.id}`), pill(u.status, u.status === 'active' ? 'green' : 'red'), pill(u.role || 'user'), fmtTime(u.last_used_at), fmtTime(u.created_at),
      el('div', { class: 'button-group' }, button('✉ 发邮件', () => sendEmail(u), 'small', { disabled: !state.status.mail_configured }), button(u.status === 'active' ? '禁用' : '启用', () => {
        const status = u.status === 'active' ? 'disabled' : 'active'; if (confirm(`确认${status === 'active' ? '启用' : '禁用'}用户 ${u.email}？`)) run(() => api(`/users/${u.id}/status`, 'POST', { status }), '用户状态已更新');
      }, `small ${u.status === 'active' ? 'danger' : ''}`, { disabled: u.role === 'admin' }))
    ]))) : empty('没有匹配的用户', '调整搜索内容或关闭“仅启用用户”。'));
  }
  let query = ''; let active = true;
  const search = input('search', '', 'search', { placeholder: '搜索邮箱、用户名或 ID', oninput: e => { query = e.target.value; paint(query, active); } });
  paint();
  return [pageHead('用户维护（备用入口）', '未开启原站页内增强时，可在此查看用户活跃情况、发送提醒邮件或维护账号状态。', el('div', { class: 'button-group' }, el('a', { href: '#system', class: 'button ghost' }, '← 返回运行与系统'), button('↻  刷新', () => render()))), !state.status.mail_configured ? notice('邮件功能尚未启用：请在服务端配置 SMTP_HOST、SMTP_FROM 等参数。未配置时不会执行邮件提醒及后续自动禁用。', 'warning') : null,
    card('', '', [el('div', { class: 'toolbar' }, el('div', { class: 'search' }, search), switcher(true, async value => { active = value; paint(query, active); }, '仅启用用户'), el('span', { class: 'count' }, `共 ${state.users.length} 位用户`)), target])];
}
function editEmailTemplate(item = {}) {
  modal(item.id ? '编辑邮件模板' : '新建邮件模板', [
    field('模板名称', input('name', item.name || '', 'text', { required: true, maxlength: 80, placeholder: '例如 额度续费提醒' })),
    field('默认邮件主题', input('subject', item.subject || '', 'text', { required: true, maxlength: 200, placeholder: '例如 账户提醒通知' })),
    field('默认邮件正文', textArea('body', item.body || '', { required: true, rows: 6, maxlength: 5000, placeholder: '您好 {username}，...' }), '支持模板变量：{username}、{email}、{last_used_at}。原站用户行「邮件」弹窗可直接选用并预览。')
  ], async form => {
    const data = new FormData(form);
    const payload = { name: String(data.get('name') || '').trim(), subject: String(data.get('subject') || '').trim(), body: String(data.get('body') || '').trim() };
    await api(`/email-templates${item.id ? `/${item.id}` : ''}`, item.id ? 'PUT' : 'POST', payload);
    toast('邮件模板已保存');
  });
}
function editRule(item = {}) {
  const moreSettings = el('details', { class: 'more-settings' },
    el('summary', {}, '更多设置'),
    el('div', { class: 'more-settings-body' },
      field('规则名称', input('name', item.name || '', 'text', { maxlength: 100, placeholder: '留空将按闲置天数自动生成' }))));
  modal(item.id ? '编辑自动化规则' : '新建自动化规则', [
    field('连续未使用天数', input('days', item.days || 30, 'number', { required: true, min: 1, max: 3650 }), '按最后模型调用时间判断；调用时间为空或无法确认时跳过。管理员不参与自动处理。'),
    field('提醒邮件主题', input('subject', item.subject || '账户长期未使用提醒', 'text', { required: true, maxlength: 200 })),
    field('提醒邮件正文', textArea('body', item.body || '您好，您的账户已超过 {days} 天未使用，请及时登录查看。如有疑问请联系管理员。', { required: true, rows: 5 }), '支持模板变量：{email}、{username}、{days}、{last_used_at}。'),
    el('div', { class: 'field' }, el('label', { class: 'switch' }, input('disable_after_email', 'yes', 'checkbox', { checked: item.disable_after_email ?? false }), '提醒邮件发送成功后禁用用户')),
    moreSettings,
    el('label', { class: 'switch' }, input('enabled', 'yes', 'checkbox', { checked: item.enabled ?? false }), '启用此规则'),
    notice('启用后将自动处理符合条件的用户。成功发送的提醒会记录，避免重复发送；邮件发送失败时不会禁用用户。', 'warning')
  ], async form => {
    const data = new FormData(form); const enabled = data.has('enabled'), days = Number(data.get('days'));
    const name = String(data.get('name') || '').trim() || item.name || `闲置 ${days} 天提醒`;
    if (enabled && !confirm('启用此规则后，符合条件的用户可能在下次检查时被发信或禁用。确认保存？')) throw new Error('已取消保存。');
    await api(`/rules${item.id ? `/${item.id}` : ''}`, item.id ? 'PUT' : 'POST', { name, days, subject: data.get('subject'), body: data.get('body'), disable_after_email: data.has('disable_after_email'), enabled }); toast('自动化规则已保存');
  });
}
async function rulesPage() {
  state.rules = await api('/rules');
  return [pageHead('闲置用户提醒', '为长期未使用的用户发送提醒，并按需停用账户。', button('＋  新建规则', () => editRule(), 'primary')), !state.status.mail_configured ? notice('SMTP 尚未配置。可以先保存规则，配置邮件服务后再启用执行。', 'warning') : notice('当前版本一次只启用一条用户维护规则，避免重复发信。调用时间无法确认的用户会跳过，操作结果可在“执行记录”中查看。'),
    card('用户维护规则', `共 ${state.rules.length} 条规则`, state.rules.length ? table(['规则', '触发条件', '执行动作', '启用状态', '操作'], state.rules.map(r => row([
      nameCell(r.name, r.subject), pill(`超过 ${r.days} 天未使用`, 'blue'), r.disable_after_email ? '发送邮件 → 禁用用户' : '仅发送提醒邮件',
      switcher(r.enabled, async enabled => { if (enabled && !confirm('确认启用？规则将自动发送邮件并执行配置的状态变更。')) throw new Error('已取消操作'); await api(`/rules/${r.id}`, 'PUT', { name: r.name, days: r.days, subject: r.subject, body: r.body, disable_after_email: r.disable_after_email, enabled }); toast(enabled ? '规则已启用' : '规则已暂停'); }, r.enabled ? '已启用' : '已暂停'),
      el('div', { class: 'button-group' }, button('编辑', () => editRule(r), 'link'), button('删除', () => { if (confirm(`确认删除规则「${r.name}」？`)) run(() => api(`/rules/${r.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
    ]))) : empty('还没有自动化规则', '设置未使用天数、提醒内容及后续动作。'))];
}
const automationTypes = { subscription_multiplier: '订阅到期倍率调整', monthly_rewards: '月度用量排名奖励' };
function automationPayload(item, enabled = item.enabled) {
  const common = { name: item.name, type: item.type, timezone: item.timezone, enabled };
  if (item.type === 'subscription_multiplier') return { ...common, group_id: item.group_id, expires_within_days: item.expires_within_days, remaining_fraction_gt: item.remaining_fraction_gt, quota_period: item.quota_period, multiplier: item.multiplier, scope: item.scope, restore_on_exit: item.restore_on_exit };
  return { ...common, mode: item.mode, rewards: item.rewards.map(tier => ({ rank: tier.rank, amount: tier.amount })) };
}
async function editAutomation(item = {}) {
  let allGroups = [], groups = [];
  try {
    allGroups = await api('/groups');
    groups = allGroups.filter(g => g.status === 'active' && g.subscription_type === 'subscription');
  } catch (error) { toast(`分组加载失败：${errorText(error)}；仍可配置月度奖励规则。`, true); }
  if (item.group_id !== undefined && item.group_id !== null && !groups.some(g => g.id === item.group_id)) {
    const matched = allGroups.find(g => g.id === item.group_id);
    groups = [{ id: item.group_id, name: matched ? `${matched.name}（原关联分组）` : `原关联分组 #${item.group_id}` }, ...groups];
  }
  const defaultZone = state.status.timezone || 'Asia/Shanghai';
  const type = el('select', { name: 'type' }, Object.entries(automationTypes).map(([key, title]) => el('option', { value: key, ...(item.type === key ? { selected: true } : {}) }, title)));
  if (item.id) type.disabled = true;
  const details = el('div');
  function paint() {
    const nameInput = input('name', item.name || '', 'text', { maxlength: 100, placeholder: type.value === 'subscription_multiplier' ? '留空将按分组与目标倍率自动生成' : '默认：月度用量排名奖励' });
    const zoneInput = input('timezone', item.timezone || defaultZone, 'text', { required: true });
    if (type.value === 'subscription_multiplier') {
      const group = el('select', { name: 'group_id', required: true }, groups.length ? groups.map(g => el('option', { value: g.id, ...(g.id === item.group_id ? { selected: true } : {}) }, `${g.name} · #${g.id}`)) : el('option', { value: '' }, '没有可用分组'));
      const period = el('select', { name: 'quota_period' }, [['day', '日额度'], ['week', '周额度'], ['month', '月额度']].map(([value, label]) => el('option', { value, ...((item.quota_period || 'month') === value ? { selected: true } : {}) }, label)));
      const scope = el('select', { name: 'scope' }, [['user_group', '个人覆盖：仅该用户 × 分组'], ['global_group', '分组默认倍率（个人专属倍率优先）']].map(([value, label]) => el('option', { value, ...((item.scope || 'user_group') === value ? { selected: true } : {}) }, label)));
      const warning = notice('', 'warning');
      const updateWarning = () => {
        warning.replaceChildren(helperIcon('exclamationCircle', 'notice-icon'), el('span', {}, scope.value === 'global_group' ? '将修改整个分组的默认倍率；已设置个人专属倍率的用户仍优先使用其个人倍率。请确认影响范围后再启用。' : '仅为符合条件的用户设置该分组的个人倍率覆盖，不修改其他用户或分组基础倍率。'));
      };
      scope.addEventListener('change', updateWarning); updateWarning();
      const moreSettings = el('details', { class: 'more-settings' },
        el('summary', {}, '更多设置'),
        el('div', { class: 'more-settings-body' },
          field('规则名称', nameInput),
          field('规则时区', zoneInput, '默认使用当前配置的时区。'),
          el('div', { class: 'field' }, el('label', { class: 'switch' }, input('restore_on_exit', 'yes', 'checkbox', { checked: item.restore_on_exit ?? true }), '不再满足条件时恢复原倍率')),
          el('p', { class: 'caption' }, '恢复前会核对当前倍率仍与本规则写入的值相同；若管理员已手动修改，将跳过恢复，避免覆盖。')));
      details.replaceChildren(
        field('订阅分组', group),
        el('div', { class: 'field-row' }, field('距离到期不超过（天）', input('expires_within_days', item.expires_within_days ?? 2, 'number', { required: true, min: 1, max: 365 })), field('检查额度周期', period)),
        el('div', { class: 'field-row' }, field('剩余额度比例超过（%）', input('remaining_percent', (item.remaining_fraction_gt ?? .5) * 100, 'number', { required: true, min: 0, max: 100, step: .1 })), field('调整后的倍率', input('multiplier', item.multiplier ?? .5, 'number', { required: true, min: .000001, max: 100, step: .000001 }), '直接设置目标倍率，不在原倍率上重复相乘。')),
        field('倍率作用范围', scope),
        warning,
        moreSettings);
    } else {
      const mode = el('select', { name: 'mode' }, [['approval', '管理员预览后手动发放'], ['automatic', '每月 1 日自动发放']].map(([value, label]) => el('option', { value, ...((item.mode || 'automatic') === value ? { selected: true } : {}) }, label)));
      const tiers = el('div', { class: 'reward-tiers-row' });
      for (const rank of [1, 2, 3]) {
        const tier = (item.rewards || []).find(value => value.rank === rank) || { rank, amount: 0 };
        tiers.append(el('div', { class: 'reward-tier-item' }, input('reward_rank', rank, 'hidden'), field(`第 ${rank} 名奖励（USD）`, input('reward_amount', tier.amount, 'number', { required: true, min: 0, max: 1000000, step: .000001 }))));
      }
      const previewContent = el('div', { class: 'inline-preview-body' },
        item.id ? el('p', { class: 'caption' }, '点击“加载预览”核对当前已保存规则的上月排名与奖励金额。') : null);
      let previewEpoch = 0;
      let previewDirty = false;
      const loadInlinePreview = async () => {
        if (!item.id) return;
        if (previewDirty) {
          previewContent.replaceChildren(el('p', { class: 'caption', 'data-preview-stale': 'true' }, '规则参数已修改，请保存后重新预览。'));
          return;
        }
        const reqEpoch = ++previewEpoch;
        previewContent.replaceChildren(el('p', { class: 'caption' }, '正在加载已保存规则的执行预览…'));
        try {
          const preview = await api(`/automations/${item.id}/preview`, 'POST');
          if (reqEpoch !== previewEpoch || previewDirty) return;
          const actions = Array.isArray(preview.actions) ? preview.actions : [];
          const period = preview.period || preview.month;
          previewContent.replaceChildren(
            period ? el('p', { class: 'caption' }, `结算周期：${typeof period === 'object' ? JSON.stringify(period) : period}（按已保存规则计算）`) : null,
            previewTable(actions));
        } catch (error) {
          if (reqEpoch !== previewEpoch || previewDirty) return;
          previewContent.replaceChildren(el('p', { class: 'form-error' }, errorText(error)));
        }
      };
      const previewButton = item.id ? button('加载预览', loadInlinePreview, 'small') : null;
      const invalidateInlinePreview = () => {
        if (!item.id) return;
        previewEpoch++;
        previewDirty = true;
        if (previewButton) previewButton.disabled = true;
        previewContent.replaceChildren(el('p', { class: 'caption', 'data-preview-stale': 'true' }, '规则参数已修改，请保存后重新预览。'));
      };
      tiers.addEventListener('input', invalidateInlinePreview);
      tiers.addEventListener('change', invalidateInlinePreview);
      mode.addEventListener('change', invalidateInlinePreview);
      zoneInput.addEventListener('input', invalidateInlinePreview);
      zoneInput.addEventListener('change', invalidateInlinePreview);
      const previewBox = el('div', { class: 'inline-preview', 'data-automation-inline-preview': 'true' },
        el('div', { class: 'inline-preview-head' },
          el('strong', {}, '执行预览'),
          previewButton),
        item.id ? previewContent : el('p', { class: 'caption' }, '保存规则后可在此或规则列表点击“预览 / 执行”，核对上月排名用户名单与奖励金额。'));
      const moreSettings = el('details', { class: 'more-settings' },
        el('summary', {}, '更多设置'),
        el('div', { class: 'more-settings-body' },
          field('规则名称', nameInput),
          field('规则时区', zoneInput, '默认使用当前配置的时区。'),
          el('p', { class: 'caption' }, '每个结算月份与用户的奖励都有持久化发放记录，避免重启或重复执行导致重复奖励。')));
      details.replaceChildren(
        tiers,
        field('执行方式', mode),
        notice('按规则时区，在每月 1 日结算上一个完整自然月，以实际费用 actual_cost 排名。自动模式不会补发规则启用前的月份。金额为 0 的名次跳过。'),
        previewBox,
        moreSettings);
    }
  }
  type.addEventListener('change', paint); paint();
  modal(item.id ? '编辑自动化规则' : '新建自动化规则', [field('规则类型', type), details, el('div', { class: 'field mt' }, el('label', { class: 'switch' }, input('enabled', 'yes', 'checkbox', { checked: item.enabled ?? false }), '启用此规则'))], async form => {
    const data = new FormData(form);
    const timezone = String(data.get('timezone') || '').trim() || item.timezone || defaultZone;
    const rawName = String(data.get('name') || '').trim();
    const value = { name: rawName, type: type.value, timezone, enabled: data.has('enabled') };
    if (type.value === 'subscription_multiplier') {
      if (!data.get('group_id')) throw new Error('请先选择一个分组。');
      const groupId = Number(data.get('group_id')), multiplier = Number(data.get('multiplier'));
      const groupObj = groups.find(g => g.id === groupId);
      value.name = rawName || item.name || autoRuleName(groupObj ? groupObj.name : `分组 #${groupId}`, ` 到期倍率 ${multiplier}x`);
      Object.assign(value, { group_id: groupId, expires_within_days: Number(data.get('expires_within_days')), remaining_fraction_gt: Number(data.get('remaining_percent')) / 100, quota_period: data.get('quota_period'), multiplier, scope: data.get('scope'), restore_on_exit: data.has('restore_on_exit') });
    } else {
      const ranks = data.getAll('reward_rank').map(Number), amounts = data.getAll('reward_amount').map(Number);
      if (!ranks.length) throw new Error('请至少配置一个奖励名次。');
      if (new Set(ranks).size !== ranks.length) throw new Error('奖励名次不能重复。');
      value.name = rawName || item.name || '月度用量排名奖励';
      Object.assign(value, { mode: data.get('mode'), rewards: ranks.map((rank, i) => ({ rank, amount: amounts[i] })) });
    }
    if (value.enabled && !confirm(`确认启用「${value.name}」？${value.type === 'subscription_multiplier' && value.scope === 'global_group' ? '将修改分组默认倍率，已有个人专属倍率仍优先。' : value.type === 'monthly_rewards' && value.mode === 'automatic' ? '规则将按月自动向用户余额发放奖励。' : '规则将按配置执行。'}`)) throw new Error('已取消保存。');
    await api(`/automations${item.id ? `/${item.id}` : ''}`, item.id ? 'PUT' : 'POST', value); toast('自动化规则已保存');
  });
}
function automationScope(item) {
  if (item.type === 'subscription_multiplier') return item.scope === 'global_group' ? '分组默认倍率（个人专属倍率仍优先）' : '用户 × 分组个人覆盖';
  return item.mode === 'automatic' ? '月初自动余额奖励' : '管理员确认后余额奖励';
}
const automationFieldLabels = { id: '对象 ID', user_id: '用户 ID', email: '邮箱', username: '用户', group_id: '分组 ID', group_name: '分组', rank: '名次', amount: '奖励（USD）', reward: '奖励（USD）', old_multiplier: '当前倍率', previous_multiplier: '当前倍率', new_multiplier: '目标倍率', multiplier: '目标倍率', previous: '原值', target: '目标值', action: '操作', operation: '处理方式', reason: '原因', error: '错误', detail: '详情', scope: '作用范围', expires_at: '到期时间', remaining_fraction: '剩余额度比例', period: '结算月份', month: '结算月份', status: '状态', actual_cost: '实际费用（USD）' };
function automationEntries(item) { return Object.entries(item).filter(([key]) => !['ledger_key', 'ownership', 'idempotency_key', 'snapshot', 'rule'].includes(key)); }
function automationValue(key, value) {
  const words = { set_multiplier: '设置倍率', set_user_group_rate: '设置个人分组倍率', set_group_rate: '设置全局分组倍率', restore_user_group_rate: '恢复个人分组倍率', restore_group_rate: '恢复全局分组倍率', credit_balance: '发放余额奖励', user_group: '个人分组覆盖', global_group: '分组默认倍率（个人专属倍率仍优先）', ready: '待执行', pending: '待核对', needs_review: '待人工核对', done: '已完成', skipped: '已跳过', error: '失败', apply: '应用规则', restore: '恢复原值', release: '停止管理并保留现值' };
  if (value === undefined || value === null) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : Object.hasOwn(words, String(value)) ? words[value] : String(value);
}
function previewTable(items) {
  const values = (items || []).map(item => typeof item === 'object' && item !== null ? item : { detail: String(item) });
  if (!values.length) return empty('没有待处理记录');
  const keys = [...new Set(values.flatMap(item => automationEntries(item).map(([key]) => key)))];
  return table(keys.map(key => automationFieldLabels[key] || key), values.map(item => row(keys.map(key => el('span', {}, automationValue(key, item[key]))))));
}
async function previewAutomation(item) {
  try {
    const preview = await api(`/automations/${item.id}/preview`, 'POST');
    const actions = Array.isArray(preview.actions) ? preview.actions : [];
    const skipped = Array.isArray(preview.skipped) ? preview.skipped : [];
    const errors = Array.isArray(preview.errors) ? preview.errors : [];
    const period = preview.period || preview.month;
    const body = el('div', { class: 'dialog-body automation-preview' }, notice(`作用范围：${automationScope(item)}。本次预览 ${actions.length} 个动作，跳过 ${skipped.length} 条${errors.length ? `，另有 ${errors.length} 条检查错误` : ''}。`, item.scope === 'global_group' ? 'warning' : ''), preview.frozen ? notice('本月名单和金额已冻结，按原结算记录处理；修改规则不会重复发奖。', 'warning') : null, period ? el('p', { class: 'caption' }, `结算周期：${typeof period === 'object' ? JSON.stringify(period) : period}`) : null, el('h3', { class: 'preview-title' }, '拟执行的变更'), previewTable(actions), skipped.length ? [el('h3', { class: 'preview-title' }, '跳过的记录'), previewTable(skipped)] : null, errors.length ? [el('h3', { class: 'preview-title' }, '无法完成检查的记录'), previewTable(errors)] : null);
    const footer = el('div', { class: 'dialog-footer' }, button('关闭', () => editor.close()));
    if (item.enabled && actions.length) footer.append(button('确认执行', async event => {
      const detail = actions.slice(0, 12).map(a => typeof a === 'object' ? automationEntries(a).map(([k, v]) => `${automationFieldLabels[k] || k}: ${automationValue(k, v)}`).join('，') : String(a)).join('\n');
      if (!confirm(`确认执行「${item.name}」？\n作用范围：${automationScope(item)}\n共 ${actions.length} 个动作：\n${detail}${actions.length > 12 ? '\n其余动作详见预览表。' : ''}\n执行前将重新校验条件。`)) return;
      const control = event.currentTarget; control.disabled = true;
      try { const result = await api(`/automations/${item.id}/run`, 'POST'); toast('执行已完成，请查看结果与执行记录。'); body.append(el('h3', { class: 'preview-title' }, '执行结果'), notice(`已执行 ${result.executed || 0} 个动作，${(result.errors || []).length} 条错误。`, result.errors?.length ? 'warning' : 'success'), ...(result.actions?.length ? [previewTable(result.actions)] : []), ...(result.errors?.length ? [previewTable(result.errors)] : [])); control.remove(); } catch (error) { toast(errorText(error), true); control.disabled = false; }
    }, 'primary'));
    if (!item.enabled) body.append(notice('此规则尚未启用。可先核对预览内容，启用规则后再执行。', 'warning'));
    editor.replaceChildren(el('div', { class: 'dialog-header' }, el('h2', {}, `规则预览 · ${item.name}`), button('✕', () => editor.close(), 'ghost', { 'aria-label': '关闭' })), body, footer); editor.showModal();
  } catch (error) { toast(errorText(error), true); }
}
function settlementContent(settlements, automations, error) {
  const history = settlements.flatMap(settlement => (settlement.actions || []).map(action => ({ ...action, month: action.month || settlement.month, start_date: settlement.start_date, end_date: settlement.end_date, timezone: settlement.timezone, rule_id: settlement.rule_id }))).sort((a, b) => String(b.month).localeCompare(String(a.month)) || a.rank - b.rank);
  const hasUncertain = history.some(action => ['pending', 'needs_review'].includes(action.status));
  const statuses = { ready: ['待发放', 'blue'], pending: ['待核对', 'amber'], needs_review: ['待人工核对', 'red'], done: ['已发放', 'green'], skipped: ['已跳过', ''] };
  const rows = history.map(action => {
    const status = statuses[action.status] || [action.status || '未知', ''];
    const rule = automations.find(value => String(value.id) === String(action.rule_id));
    return row([nameCell(action.month, `${action.start_date || ''} — ${action.end_date || ''}`), action.rank || '—', `#${action.user_id}`, `$${action.amount}`, action.actual_cost === undefined ? '—' : `$${action.actual_cost}`, pill(status[0], status[1]), nameCell(rule?.name || `规则 #${action.rule_id}`, action.timezone), fmtTime(action.completed_at || action.attempted_at), el('div', { class: 'wrap' }, action.error || action.reason || '—')]);
  });
  return error ? el('div', { class: 'card-body' }, notice(`结算记录读取失败：${error}`, 'error')) : history.length ? [hasUncertain ? el('div', { class: 'card-body' }, notice('存在待核对的发放记录，这些记录不会自动重试。请先在 sub2api 核查余额变更记录，确认结果后再处理，避免重复奖励。', 'warning')) : null, table(['结算月份', '名次', '用户 ID', '奖励（USD）', '实际费用（USD）', '发放状态', '来源规则 / 时区', '最近处理时间', '备注'], rows)] : empty('暂无月度结算记录', '结算后可在此查看历史奖励及需要人工核对的发放。');
}
// Each card loads on its own: a slow or failed metric never hides the settlement records.
// `refresh` promises only re-paint an already loaded card (e.g. rule names), never gate it.
function lazyCard(version, title, description, busy, load, paint, refresh = []) {
  const body = el('div', {}, el('div', { class: 'loading' }, el('span', { class: 'spinner' }), busy));
  const current = () => version === generation && authorized;
  let data;
  const show = () => { const content = paint(data); body.replaceChildren(...(Array.isArray(content) ? content : [content]).filter(node => node != null)); };
  load().then(value => { if (!current()) return; data = value; show(); }).catch(error => { if (version !== generation) return; if (!authorized) { render(); return; } body.replaceChildren(el('div', { class: 'card-body' }, notice(errorText(error), 'error'))); });
  for (const extra of refresh) extra.then(() => { if (data !== undefined && current()) show(); }, () => {});
  return card(title, description, body);
}
function rewardsPage() {
  const version = generation;
  return [pageHead('排名与奖励', '查看月度奖励结算记录，以及模型费用与用户用量排名。', button('↻  刷新', () => render())),
    el('section', { id: 'rewards-settlements', class: 'rules-section' }, lazyCard(version, '月度结算记录', '保留历史发放状态；月份切换或删除规则不会移除记录。', '正在读取结算记录…',
      () => api('/settlements').catch(error => { throw new Error(`结算记录读取失败：${errorText(error)}`); }), data => settlementContent(data, state.automations, ''),
      [api('/automations').then(rules => { state.automations = Array.isArray(rules) ? rules : []; })])),
    el('div', { class: 'overview-grid' },
      lazyCard(version, '模型维度价格统计', '显示上游统计接口返回的当前周期数据', '正在读取统计数据…', () => api('/analytics/models'), data => metricsTable(data, 'models')),
      lazyCard(version, '用户用量排名', '管理员可查看完整使用量统计', '正在读取统计数据…', () => api('/analytics/ranking'), data => metricsTable(data, 'ranking')))];
}
async function automationsPage() {
  state.automations = await api('/automations');
  const rows = state.automations.map(item => row([nameCell(item.name, automationTypes[item.type] || item.type), item.type === 'subscription_multiplier' ? `分组 #${item.group_id} · 到期前 ${item.expires_within_days} 天 · 余量 > ${item.remaining_fraction_gt * 100}%` : (item.rewards || []).map(t => `第 ${t.rank} 名 $${t.amount}`).join(' / '), pill(automationScope(item), item.scope === 'global_group' ? 'amber' : 'blue'), item.timezone,
    switcher(item.enabled, async enabled => { if (enabled && !confirm(`确认启用「${item.name}」？作用范围：${automationScope(item)}。`)) throw new Error('已取消操作'); await api(`/automations/${item.id}`, 'PUT', automationPayload(item, enabled)); item.enabled = enabled; toast(enabled ? '规则已启用' : '规则已暂停'); }, item.enabled ? '已启用' : '已暂停'),
    el('div', { class: 'button-group' }, button('预览 / 执行', () => previewAutomation(item), 'link'), button('编辑', () => editAutomation(item), 'link'), button('删除', () => { if (confirm(`确认删除「${item.name}」？服务端会先尝试安全恢复本规则写入的倍率；存在恢复错误时会阻止删除。`)) run(() => api(`/automations/${item.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
  ]));
  return [pageHead('自动化规则', '按订阅状态调整倍率，或按上月实际用量向用户发放余额奖励。', button('＋ 新建规则', () => editAutomation(), 'primary')), notice('新规则默认关闭。先保存并查看预览，确认用户、金额和作用范围后再启用。月度奖励按规则时区结算，不补发启用前月份。'), card('规则列表', `共 ${rows.length} 条规则`, rows.length ? table(['规则', '触发条件 / 奖励', '作用范围', '时区', '启用状态', '操作'], rows) : empty('还没有自动化规则', '可添加订阅到期倍率调整，或月度排名奖励规则。'))];
}
async function modulesPage() {
  state.modules = await api('/modules');
  let settings = null, settingsError = '', syncUncertain = false, busy = false;
  try { settings = await api('/menu-settings'); } catch (error) { settingsError = errorText(error); }
  let savedOrigin = settings ? settings.public_origin || '' : state.status.public_url || '';
  const browserOrigin = /^https?:\/\//.test(location.origin) ? location.origin : '';
  const detectedOrigin = () => settings?.sub2api_public_origin || browserOrigin;
  const origin = input('public_origin', savedOrigin || detectedOrigin(), 'url', { id: 'menu-public-origin', required: true, autocomplete: 'url', spellcheck: 'false', placeholder: 'https://sub2api.example.com' });
  const feedback = el('p', { class: 'connection-feedback', role: 'status', 'aria-live': 'polite' });
  const status = el('div', { class: 'menu-settings-status' });
  const previewLabel = el('p', { class: 'caption menu-preview-caption' });
  const previews = new Map(), switches = [];
  const form = el('form', { class: 'menu-origin-form' });
  const save = el('button', { type: 'submit', class: 'button primary' }, '保存域名');
  const detect = button('使用当前域名', () => { if (!detectedOrigin()) return; origin.value = detectedOrigin(); paint(); setFeedback(settings?.sub2api_public_origin ? '已填入 sub2api 的用户访问地址，保存后生效。' : '已填入当前浏览器域名，保存后生效。'); });
  const sync = button('同步菜单', async () => {
    busy = true; paint(); setFeedback('正在同步菜单并核对上游结果…');
    try {
      const data = await api('/menu-settings/sync', 'POST', {});
      if (data?.sync_status !== 'verified') throw new Error('菜单同步结果尚未确认，请刷新核对。');
      accept(data); setFeedback(data.changed ? '菜单已同步，并通过上游回读确认。' : '上游菜单已与当前配置一致。');
    } catch (error) { syncUncertain = true; setFeedback(`菜单同步未确认：${errorText(error)}。请刷新核对后再重试。`, true); }
    finally { busy = false; paint(); }
  });
  function setFeedback(text, failed = false) { feedback.textContent = text; feedback.className = `connection-feedback${failed ? ' error' : ''}`; }
  function draftOrigin() {
    const value = origin.value.trim();
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/' ? url.origin : ''; } catch (_) { return ''; }
  }
  function dirty() { return origin.value.trim().replace(/\/+$/, '') !== savedOrigin.replace(/\/+$/, ''); }
  function accept(data) { settings = data; savedOrigin = data.public_origin || ''; origin.value = savedOrigin; state.status.public_url = savedOrigin; settingsError = ''; syncUncertain = false; }
  function paint() {
    const changed = dirty(), previewOrigin = draftOrigin();
    origin.disabled = busy; save.disabled = busy || (!changed && Boolean(settings)); detect.disabled = busy || !detectedOrigin(); detect.textContent = settings?.sub2api_public_origin ? '使用 sub2api 地址' : '使用当前域名';
    sync.disabled = busy || changed || !settings?.ready;
    for (const { control, key } of switches) control.disabled = busy || changed || (!settings?.ready && !state.modules[key]);
    status.replaceChildren(syncUncertain ? notice('上次同步未得到确认。当前菜单状态未知，请先刷新核对。', 'warning') : settingsError ? notice(`菜单状态读取失败：${settingsError}`, 'warning') : settings?.in_sync ? notice('上游菜单与已保存的配置一致。', 'success') : notice('菜单尚未同步到已保存的配置。', 'warning'), ...(settings?.problems || []).map(problem => notice(String(problem), 'warning')));
    previewLabel.textContent = changed ? '以下为未保存的菜单地址预览。先保存域名，再同步菜单。' : '以下为已保存配置的菜单地址预览；同步结果以上方核对状态为准。';
    for (const [key, target] of previews) target.textContent = previewOrigin ? `${previewOrigin}/_helper/features/${key}` : '填写有效访问域名后显示预览地址';
  }
  async function refreshSettings() {
    busy = true; paint();
    try {
      const edited = origin.value;
      const changed = dirty();
      settings = await api('/menu-settings'); savedOrigin = settings.public_origin || ''; state.status.public_url = savedOrigin;
      if (!changed) origin.value = savedOrigin || detectedOrigin(); else origin.value = edited;
      settingsError = ''; syncUncertain = false; setFeedback('已重新读取上游菜单状态。');
    } catch (error) { settingsError = errorText(error); setFeedback(`菜单状态读取失败：${settingsError}`, true); }
    finally { busy = false; paint(); }
  }
  origin.addEventListener('input', () => { setFeedback(''); paint(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const value = draftOrigin();
    if (!value) { setFeedback('请填写完整源地址，例如 https://api.example.com，不含路径、查询参数或账号密码。', true); return; }
    busy = true; paint(); setFeedback('正在保存访问域名…');
    try { accept(await api('/menu-settings', 'PUT', { public_origin: value })); setFeedback(settings.in_sync ? '访问域名已保存，当前菜单与配置一致。' : '访问域名已保存。点击“同步菜单”后才会更新上游入口。'); }
    catch (error) { setFeedback(`访问域名保存失败：${errorText(error)}`, true); }
    finally { busy = false; paint(); }
  });
  form.append(field('用户访问域名', origin, '填写用户浏览器访问 sub2api 的域名与协议，不含 /_helper 路径。'), el('div', { class: 'button-group' }, detect, save, sync), feedback, status);
  const definitions = [ ['benchmark', 'sparkles', '智商检测', '按分组展示模型根据提示词生成的 HTML、SVG 等作品。在「智商检测」中配置生成任务。'], ['pricing', 'chartBar', '模型价格统计', '按模型查看请求量、Token 与费用。普通用户仅查看自己的统计数据。'], ['ranking', 'trophy', '用户用量排名', '展示用户消耗与使用量排名。用户端默认匿名展示，保护用户身份。'] ];
  const cards = el('div', { class: 'module-grid' }, definitions.map(([key, icon, title, desc]) => {
    const toggle = switcher(state.modules[key], async value => {
        if (!confirm(`确认${value ? '上线' : '下线'}「${title}」并同步上游菜单？`)) throw new Error('已取消操作');
        busy = true; paint(); setFeedback('正在更新功能并核对上游菜单…');
        try { state.modules = await api('/modules', 'PUT', { ...state.modules, [key]: value }); toast('功能状态与上游菜单已确认同步'); await render(); }
        catch (error) { syncUncertain = true; paint(); throw error; }
        finally { busy = false; paint(); }
      }, state.modules[key] ? '已启用' : '启用');
    switches.push({ control: toggle.querySelector('input'), key });
    const url = el('div', { class: 'module-url' }); previews.set(key, url);
    return el('section', { class: 'card module-card' }, el('div', { class: 'module-icon' }, helperIcon(icon)), el('h2', {}, title), el('p', {}, desc), el('div', { class: 'module-bottom' }, pill(state.modules[key] ? '功能已启用' : '功能已关闭', state.modules[key] ? 'green' : ''), toggle), url);
  }));
  paint();
  return [pageHead('功能与菜单', '设置用户访问域名，并核对 sub2api 自定义菜单。', button('↻  刷新核对', refreshSettings)), card('菜单访问地址', '修改域名只保存配置；点击同步后才会更新上游菜单。', el('div', { class: 'card-body' }, form)), previewLabel, cards];
}
function extractRows(data, keys) {
  if (Array.isArray(data)) return data;
  for (const key of keys) if (Array.isArray(data?.[key])) return data[key];
  if (data && typeof data === 'object') {
    for (const value of Object.values(data)) if (Array.isArray(value)) return value;
  }
  return [];
}
function metricsTable(data, kind) {
  const rows = extractRows(data, kind === 'ranking' ? ['ranking', 'users', 'items'] : ['models', 'items', 'stats']);
  if (!rows.length) return empty('暂无统计数据', '选定周期内还没有可展示的使用记录。');
  const labels = { model: '模型', model_name: '模型', name: '名称', rank: '排名', username: '用户', email: '邮箱', user_id: '用户 ID', requests: '请求数', request_count: '请求数', tokens: 'Token', total_tokens: '总 Token', input_tokens: '输入 Token', output_tokens: '输出 Token', cost: '费用（USD）', total_cost: '费用（USD）', actual_cost: '实际费用（USD）', percentage: '占比' };
  const keys = Object.keys(rows[0]).filter(k => typeof rows[0][k] !== 'object');
  return table(keys.map(k => labels[k] || k), rows.map(item => row(keys.map(k => {
    const value = item[k]; if (typeof value === 'number') return /cost/.test(k) ? `$${value.toFixed(4)}` : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }); return value ?? '—';
  }))));
}
async function rulesHubPage() {
  const results = await Promise.allSettled(['/schedules', '/rules', '/email-templates', '/automations'].map(path => api(path)));
  const scheduleErr = results[0].status === 'rejected' ? errorText(results[0].reason) : '';
  const ruleErr = results[1].status === 'rejected' ? errorText(results[1].reason) : '';
  const templateErr = results[2].status === 'rejected' ? errorText(results[2].reason) : '';
  const autoErr = results[3].status === 'rejected' ? errorText(results[3].reason) : '';
  if (results[0].status === 'fulfilled') state.schedules = results[0].value;
  if (results[1].status === 'fulfilled') state.rules = results[1].value;
  if (results[2].status === 'fulfilled') state.emailTemplates = results[2].value;
  if (results[3].status === 'fulfilled') state.automations = results[3].value;

  const scheduleRows = state.schedules.map(s => row([
    nameCell(s.name, `规则 #${s.id}`),
    el('span', { class: 'code' }, `${s.start} → ${s.end}${s.end < s.start ? '（次日）' : ''}`),
    s.timezone,
    el('span', { class: 'tiny' }, (s.weekdays || []).map(n => dayNames[n]).join(' / ')),
    `${(s.account_ids || []).length} 个账号`,
    switcher(s.enabled, async enabled => {
      await api(`/schedules/${s.id}`, 'PUT', { name: s.name, account_ids: s.account_ids, start: s.start, end: s.end, timezone: s.timezone, weekdays: s.weekdays, enabled });
      toast(enabled ? '规则已启用' : '规则已暂停');
    }, s.enabled ? '已启用' : '已暂停'),
    el('div', { class: 'button-group' },
      button('编辑', () => editSchedule(s), 'link'),
      button('删除', () => { if (confirm(`删除「${s.name}」？删除后停止管理这些账号，保留账号当前调度状态。`)) run(() => api(`/schedules/${s.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
  ]));
  const scheduleSection = el('section', { id: 'rules-schedules', class: 'rules-section' },
    el('div', { class: 'rules-section-head' },
      el('div', {}, el('h2', {}, '账号时段规则'), el('p', {}, '按时间窗口自动开关账号调度，可在原站账号行「定时」弹窗直接选用或在此维护多账号计划。')),
      button('＋  新建调度', () => editSchedule(), 'primary')),
    scheduleErr ? notice(`时段规则读取失败：${scheduleErr}`, 'error') : card('时段规则列表', `共 ${scheduleRows.length} 条规则 · 同一账号仅可由一条启用计划管理`, scheduleRows.length
      ? table(['规则', '调度窗口', '时区', '开启日期', '管理范围', '启用状态', '操作'], scheduleRows)
      : empty('还没有账号时段规则', '新建时段规则后，可在原站账号行直接套用，或在此批量管理多个账号。')));

  const ruleRows = state.rules.map(r => row([
    nameCell(r.name, r.subject),
    pill(`超过 ${r.days} 天未使用`, 'blue'),
    r.disable_after_email ? '发送邮件 → 禁用用户' : '仅发送提醒邮件',
    switcher(r.enabled, async enabled => {
      if (enabled && !confirm('确认启用？规则将自动发送邮件并执行配置的状态变更。')) throw new Error('已取消操作');
      await api(`/rules/${r.id}`, 'PUT', { name: r.name, days: r.days, subject: r.subject, body: r.body, disable_after_email: r.disable_after_email, enabled });
      toast(enabled ? '规则已启用' : '规则已暂停');
    }, r.enabled ? '已启用' : '已暂停'),
    el('div', { class: 'button-group' },
      button('编辑', () => editRule(r), 'link'),
      button('删除', () => { if (confirm(`确认删除规则「${r.name}」？`)) run(() => api(`/rules/${r.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
  ]));
  const templateRows = state.emailTemplates.map(t => row([
    nameCell(t.name, `模板 #${t.id}`),
    t.subject,
    el('div', { class: 'template-preview' }, t.body),
    el('div', { class: 'button-group' },
      button('编辑', () => editEmailTemplate(t), 'link'),
      button('删除', () => { if (confirm(`确认删除邮件模板「${t.name}」？`)) run(() => api(`/email-templates/${t.id}`, 'DELETE'), '邮件模板已删除'); }, 'link danger'))
  ]));
  const usersSection = el('section', { id: 'rules-users', class: 'rules-section' },
    el('div', { class: 'rules-section-head' },
      el('div', {}, el('h2', {}, '用户提醒与邮件模板'), el('p', {}, '配置闲置用户自动提醒规则，并维护供原站用户行「邮件」弹窗选用的可复用模板。')),
      el('div', { class: 'button-group' },
        button('＋  新建提醒规则', () => editRule()),
        button('＋  新建邮件模板', () => editEmailTemplate(), 'primary'))),
    !state.status.mail_configured ? notice('SMTP 尚未配置。可以先保存提醒规则与模板，配置邮件服务后再启用或发送。', 'warning') : null,
    ruleErr ? notice(`闲置提醒读取失败：${ruleErr}`, 'error') : card('闲置用户提醒规则', `共 ${ruleRows.length} 条规则 · 一次仅启用一条，调用时间不明确的用户自动跳过`, ruleRows.length
      ? table(['规则', '触发条件', '执行动作', '启用状态', '操作'], ruleRows)
      : empty('还没有闲置提醒规则', '设置未使用天数、提醒内容及后续动作。')),
    templateErr ? notice(`邮件模板读取失败：${templateErr}`, 'error') : card('可复用邮件模板', `共 ${templateRows.length} 个自定义模板 · 原站用户行「邮件」弹窗可直接选用`, templateRows.length
      ? table(['模板名称', '默认主题', '正文预览', '操作'], templateRows)
      : empty('还没有自定义邮件模板', '创建常用通知模板后，在原站用户行发邮件时可直接选用并自动填充用户名与邮箱。')));

  const autoRows = state.automations.map(item => row([
    nameCell(item.name, automationTypes[item.type] || item.type),
    item.type === 'subscription_multiplier' ? `分组 #${item.group_id} · 到期前 ${item.expires_within_days} 天 · 余量 > ${item.remaining_fraction_gt * 100}%` : (item.rewards || []).map(t => `第 ${t.rank} 名 $${t.amount}`).join(' / '),
    pill(automationScope(item), item.scope === 'global_group' ? 'amber' : 'blue'),
    item.timezone,
    switcher(item.enabled, async enabled => {
      if (enabled && !confirm(`确认启用「${item.name}」？作用范围：${automationScope(item)}。`)) throw new Error('已取消操作');
      await api(`/automations/${item.id}`, 'PUT', automationPayload(item, enabled));
      item.enabled = enabled;
      toast(enabled ? '规则已启用' : '规则已暂停');
    }, item.enabled ? '已启用' : '已暂停'),
    el('div', { class: 'button-group' },
      button('预览 / 执行', () => previewAutomation(item), 'link'),
      button('编辑', () => editAutomation(item), 'link'),
      button('删除', () => { if (confirm(`确认删除「${item.name}」？服务端会先尝试安全恢复本规则写入的倍率；存在恢复错误时会阻止删除。`)) run(() => api(`/automations/${item.id}`, 'DELETE'), '规则已删除'); }, 'link danger'))
  ]));
  const automationsSection = el('section', { id: 'rules-automations', class: 'rules-section' },
    el('div', { class: 'rules-section-head' },
      el('div', {}, el('h2', {}, '后台自动化规则'), el('p', {}, '按订阅状态调整倍率，或按上月实际用量发放余额奖励（后台任务执行，先预览再启用）。')),
      el('div', { class: 'button-group' }, el('a', { href: '#rewards', class: 'button ghost' }, '结算记录'), button('＋  新建自动化', () => editAutomation(), 'primary'))),
    autoErr ? notice(`自动化规则读取失败：${autoErr}`, 'error') : card('自动化规则列表', `共 ${autoRows.length} 条规则`, autoRows.length
      ? table(['规则', '触发条件 / 奖励', '作用范围', '时区', '启用状态', '操作'], autoRows)
      : empty('还没有自动化规则', '可添加订阅到期倍率调整，或月度排名奖励规则。')));

  return [
    pageHead('规则管理', '集中维护账号时段、用户提醒与邮件模板，以及后台自动化规则。', button('↻  刷新', () => render())),
    scheduleSection,
    usersSection,
    automationsSection
  ];
}
async function analyticsPage() {
  const gallery = el('div');
  helperGalleryAdmin(gallery, { request: api });
  return [pageHead('智商检测', '管理参测分组与同题对比。'), gallery];
}
async function systemPage() {
  const results = await Promise.allSettled(['/accounts', '/schedules', '/rules', '/modules', '/automations', '/audit', '/compatibility'].map(path => api(path)));
  if (results[0].status === 'fulfilled') state.accounts = results[0].value;
  if (results[1].status === 'fulfilled') state.schedules = results[1].value;
  if (results[2].status === 'fulfilled') state.rules = results[2].value;
  if (results[3].status === 'fulfilled') state.modules = results[3].value;
  if (results[4].status === 'fulfilled') state.automations = results[4].value;
  const auditData = results[5].status === 'fulfilled' ? results[5].value : null;
  const auditErr = results[5].status === 'rejected' ? errorText(results[5].reason) : '';
  const compatData = results[6].status === 'fulfilled' ? results[6].value : null;
  const compatErr = results[6].status === 'rejected' ? errorText(results[6].reason) : '';
  const stats = [
    ['管理账号', Array.isArray(state.accounts) ? state.accounts.length : '—', 'database', '来自 sub2api 的账号'],
    ['调度中', Array.isArray(state.accounts) ? state.accounts.filter(a => a.schedulable).length : '—', 'play', '账号当前参与请求调度'],
    ['时段规则', state.schedules.filter(s => s.enabled).length, 'clock', '已启用的时间窗口'],
    ['自动化与提醒', state.rules.filter(r => r.enabled).length + state.automations.filter(r => r.enabled).length, 'cog', '已启用的提醒与自动化规则']
  ];
  const details = [
    ['服务地址', state.status.upstream_url || '—'],
    ['默认时区', state.status.timezone || 'Asia/Shanghai'],
    ['邮件服务', state.status.mail_configured ? pill('已配置', 'green') : pill('未配置', 'amber')],
    ['外链域名', state.status.public_url || '尚未配置（请到功能与菜单填写）'],
    ['版本', state.status.version || '—'],
    ['调度引擎', state.status.engine?.running ? pill('运行中', 'green') : pill('等待启动', 'amber')]
  ];
  const auditRows = Array.isArray(auditData) ? auditData : auditData?.items || [];
  const checks = compatData?.checks || [];
  return [
    pageHead('运行与系统', '查看运行状态、最近执行记录与上游接口兼容性。', button('↻  刷新', () => render())),
    el('section', { class: 'stats-grid' }, stats.map(([label, value, icon, desc]) => el('div', { class: 'stat-card' }, el('div', { class: 'stat-icon' }, helperIcon(icon)), el('div', {}, el('div', { class: 'stat-label' }, label), el('div', { class: 'stat-value' }, String(value)), el('div', { class: 'stat-desc' }, desc))))),
    card('运行状态', '后台任务与邮件服务状态', el('div', { class: 'card-body' }, details.map(([k, v]) => el('div', { class: 'detail-row' }, el('span', { class: 'muted' }, k), el('span', { class: 'value' }, v))))),
    el('section', { id: 'system-audit', class: 'rules-section' },
      card('执行记录', `展示最近 ${auditRows.length} 条记录`, auditErr ? el('div', { class: 'card-body' }, notice(`执行记录读取失败：${auditErr}`, 'error')) : auditRows.length ? table(['时间', '动作', '对象', '结果', '详情'], auditRows.map(a => row([
        fmtTime(a.created_at || a.time || a.timestamp || a.at),
        a.action || a.kind || a.event || '—',
        a.target || a.target_id || a.entity_id || '—',
        pill(a.ok === false || a.success === false || a.status === 'error' || a.status === 'failed' ? '失败' : a.status === 'ok' || a.status === 'success' ? '成功' : a.status || '成功', a.ok === false || a.success === false || a.status === 'error' || a.status === 'failed' ? 'red' : 'green'),
        el('div', { class: 'code' }, typeof (a.detail || a.message || a.data) === 'object' ? JSON.stringify(a.detail || a.message || a.data) : a.detail || a.message || a.data || '—')
      ]))) : empty('暂无执行记录', '执行任务后，这里会展示每次操作结果。'))),
    el('section', { id: 'system-compatibility', class: 'rules-section' },
      card('兼容性检查', compatData?.checked_at ? `最近检查：${fmtTime(compatData.checked_at)}` : '尚未运行检查', compatErr ? el('div', { class: 'card-body' }, notice(`兼容性信息读取失败：${compatErr}`, 'error')) : [
        el('div', { class: 'card-body' }, [
          ['开发基准提交', compatData?.upstream_commit || '未记录'],
          ['上游最新提交', compatData?.latest_commit || '未获取'],
          ['版本状态', compatData?.update_available ? pill('上游有更新，建议核查', 'amber') : compatData?.latest_commit ? pill('与开发基准一致', 'green') : pill('等待检查')]
        ].map(([k, v]) => el('div', { class: 'detail-row' }, el('span', { class: 'muted' }, k), el('span', { class: 'value code' }, v)))),
        checks.length ? table(['检查项', '状态', '说明'], checks.map(c => row([c.name, pill(c.ok ? '通过' : '未通过', c.ok ? 'green' : 'red'), el('div', { class: 'code' }, c.detail || '—')]))) : empty('尚未执行接口检查', '点击“立即检查”验证当前绑定的 sub2api 服务。')
      ], button('↻  立即检查', () => run(() => api('/compatibility/check', 'POST', {}), '兼容性检查完成'), 'primary'))),
    card('关闭页内增强时的备用入口', '正常情况下请直接在 sub2api 原站「账号管理 / 用户管理」行内操作；未开启页内增强时可由此进入备用管理页。', el('div', { class: 'card-body fallback-links' },
      el('a', { href: '#accounts', class: 'button' }, helperIcon('database'), '账号管理（备用）'),
      el('a', { href: '#users', class: 'button' }, helperIcon('users'), '用户维护（备用）')))
  ];
}
function resolveRoute(hashRoute) {
  const clean = hashRoute || 'rules';
  if (clean === 'schedules') return { nav: 'rules', page: 'rules', anchor: 'rules-schedules', title: '规则' };
  if (clean === 'automations') return { nav: 'rules', page: 'rules', anchor: 'rules-automations', title: '规则' };
  if (clean === 'settlements') return { nav: 'rewards', page: 'rewards', anchor: 'rewards-settlements', title: '排名与奖励' };
  if (clean === 'overview') return { nav: 'system', page: 'system', anchor: '', title: '运行与系统' };
  if (clean === 'audit') return { nav: 'system', page: 'system', anchor: 'system-audit', title: '运行与系统' };
  if (clean === 'compatibility') return { nav: 'system', page: 'system', anchor: 'system-compatibility', title: '运行与系统' };
  if (clean === 'accounts') return { nav: 'system', page: 'accounts', anchor: '', title: '账号管理' };
  if (clean === 'users') return { nav: 'system', page: 'users', anchor: '', title: '用户维护' };
  const found = views.find(v => v[0] === clean) || views[0];
  return { nav: found[0], page: found[0], anchor: '', title: found[1] };
}
async function render() {
  const version = ++generation;
  const target = resolveRoute(location.hash.slice(1));
  setConnectionState(authorized);
  $('#navigation').replaceChildren(...views.map(([key, text, icon]) => el('a', { href: `#${key}`, class: `nav-item ${key === target.nav ? 'active' : ''}`, ...(key === target.nav ? { 'aria-current': 'page' } : {}) }, helperIcon(icon, 'nav-icon'), text)));
  if (!authorized && (sessionChecked || isNativeConsole)) { disconnected(); return; }
  main.replaceChildren(el('div', { class: 'loading' }, el('span', { class: 'spinner' }), '正在加载…'));
  try {
    state.status = await api('/status');
    authorized = true;
    sessionChecked = true;
    if (state.status.upstream_url && !$('#connection-url').value) $('#connection-url').value = state.status.upstream_url;
    setConnectionState(true);
    const routes = { rules: rulesHubPage, modules: modulesPage, analytics: analyticsPage, rewards: rewardsPage, system: systemPage, accounts: accountsPage, users: usersPage };
    const content = await routes[target.page]();
    if (version === generation) {
      if (!authorized) { disconnected(); return; }
      main.replaceChildren(...content.filter(Boolean));
      if (target.anchor) document.getElementById(target.anchor)?.scrollIntoView({ block: 'start' });
    }
  } catch (error) {
    if (version !== generation) return;
    sessionChecked = true;
    if (!authorized) { disconnected(); return; }
    main.replaceChildren(...[pageHead(target.title, '数据暂时无法加载。'), notice(errorText(error), 'error'), button('重试', () => render(), 'primary')].filter(Boolean));
  }
}
async function handleDisconnect() {
  const controls = [$('#disconnect'), $('#disconnect-summary')].filter(Boolean);
  controls.forEach(c => { c.disabled = true; });
  let revoked = false;
  try { await api('/connection/disconnect', 'POST', {}); revoked = true; }
  catch (_) { /* Always clear browser credentials even if the server is unreachable. */ }
  finally {
    token = ''; saveSession(''); authorized = false; sessionChecked = true; showCredentialForm = true;
    editor.close(); $('#connection-key').value = '';
    controls.forEach(c => { c.disabled = false; });
    $('#connection-feedback').textContent = revoked
      ? '当前浏览器会话已退出。服务端保存的 Key 与已启用的后台任务不受影响。'
      : '浏览器会话已清除，但服务端会话撤销未确认。已保存的后台任务仍会继续执行。';
    $('#connection-feedback').className = revoked ? 'connection-feedback' : 'connection-feedback warning';
    render();
  }
}
$('#disconnect')?.addEventListener('click', handleDisconnect);
$('#disconnect-summary')?.addEventListener('click', handleDisconnect);
window.addEventListener('hashchange', () => render());
initializeConnection();

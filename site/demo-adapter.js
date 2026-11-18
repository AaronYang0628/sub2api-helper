/* GitHub Pages adapter: every API operation is simulated in memory.
 * No backend, SMTP, model call, credential exchange or persistent user data.
 */
'use strict';
(() => {
  const assetRoot = new URL('../', document.currentScript.src);
  const base = assetRoot.href.replace(/\/$/, '');
  const nativeDemo = document.body.classList.contains('demo-native');
  const fixtureUrl = 'https://sub2api.example.invalid';
  let sequence = 20;
  const clone = value => JSON.parse(JSON.stringify(value));
  const created = '2026-08-01T09:00:00+08:00';
  const accounts = [
    { id: 1, name: '夜间免费额度 · OpenAI A', platform: 'openai', status: 'active', schedulable: true, group_ids: [1] },
    { id: 2, name: '夜间免费额度 · OpenAI B', platform: 'openai', status: 'active', schedulable: true, group_ids: [1] },
    { id: 3, name: 'Claude 日常账号', platform: 'anthropic', status: 'active', schedulable: true, group_ids: [2] },
    { id: 4, name: 'Gemini 备用账号', platform: 'gemini', status: 'active', schedulable: false, group_ids: [2] },
    { id: 5, name: '测试账号 · 可手动切换', platform: 'openai', status: 'active', schedulable: false, group_ids: [3] },
    { id: 6, name: 'Codex 团队账号', platform: 'openai', status: 'active', schedulable: true, group_ids: [3] },
  ];
  const users = [
    { id: 101, username: '示例用户 A', email: 'demo.a@example.invalid', role: 'user', status: 'active', last_used_at: '2026-11-16T21:05:00+08:00', created_at: created },
    { id: 102, username: '示例用户 B', email: 'demo.b@example.invalid', role: 'user', status: 'active', last_used_at: '2026-11-15T16:20:00+08:00', created_at: created },
    { id: 103, username: '示例用户 C', email: 'demo.c@example.invalid', role: 'user', status: 'active', last_used_at: '2026-10-01T13:00:00+08:00', created_at: created },
    { id: 104, username: '示例用户 D', email: 'demo.d@example.invalid', role: 'user', status: 'disabled', last_used_at: '2026-09-20T11:30:00+08:00', created_at: created },
    { id: 105, username: '示例管理员', email: 'admin@example.invalid', role: 'admin', status: 'active', last_used_at: null, created_at: created },
  ];
  const groups = [
    { id: 1, name: '夜间免费分组', status: 'active', subscription_type: 'subscription', rate_multiplier: 1, monthly_limit_usd: 100 },
    { id: 2, name: '高级订阅分组', status: 'active', subscription_type: 'subscription', rate_multiplier: 1.5, monthly_limit_usd: 200 },
    { id: 3, name: '团队分组', status: 'active', subscription_type: 'standard', rate_multiplier: 1 },
  ];
  const state = {
    schedules: [
      { id: 1, name: '每天夜间免费时段', owner: 'native-account', native_account_id: 1, account_ids: [1], start: '23:00', end: '09:00', timezone: 'Asia/Shanghai', weekdays: [0, 1, 2, 3, 4, 5, 6], enabled: true },
      { id: 2, name: '工作日团队时段', account_ids: [6], start: '09:00', end: '18:00', timezone: 'Asia/Shanghai', weekdays: [0, 1, 2, 3, 4], enabled: false },
    ],
    rules: [{ id: 1, name: '30 天未使用提醒', days: 30, enabled: false, subject: '账户长期未使用提醒', body: '您好 {username}，您的账户已有 {days} 天未使用，请及时查看。', disable_after_email: false }],
    automations: [
      { id: 1, name: '临期订阅个人倍率', type: 'subscription_multiplier', enabled: true, timezone: 'Asia/Shanghai', group_id: 1, expires_within_days: 2, remaining_fraction_gt: .5, quota_period: 'month', multiplier: .5, scope: 'user_group', restore_on_exit: true },
      { id: 2, name: '上月用量前三名奖励', type: 'monthly_rewards', enabled: true, timezone: 'Asia/Shanghai', mode: 'approval', rewards: [{ rank: 1, amount: 30 }, { rank: 2, amount: 20 }, { rank: 3, amount: 10 }] },
    ],
    modules: { benchmark: true, pricing: true, ranking: false },
    audit: [
      { at: '2026-11-17T09:00:00+08:00', action: 'account.schedule', target: '1, 2', status: 'ok', detail: '示例：夜间窗口结束，已关闭调度。' },
      { at: '2026-11-16T23:00:00+08:00', action: 'account.schedule', target: '1, 2', status: 'ok', detail: '示例：免费时段开始，已开启调度。' },
      { at: '2026-11-16T16:30:00+08:00', action: 'compatibility.check', target: 'upstream', status: 'ok', detail: '示例：接口契约检查通过。' },
    ],
    settlements: [{ month: '2026-09', start_date: '2026-09-01', end_date: '2026-09-30', timezone: 'Asia/Shanghai', rule_id: 'archived-demo', actions: [
      { user_id: 101, rank: 1, amount: '30', actual_cost: '245.80', month: '2026-09', status: 'needs_review', attempted_at: '2026-10-01T00:01:00+08:00', reason: '示例：请求结果未知，需要人工核对；不会自动重试。' },
      { user_id: 102, rank: 2, amount: '20', actual_cost: '180.24', month: '2026-09', status: 'done', completed_at: '2026-10-01T00:02:00+08:00' },
    ] }],
  };
  const metrics = {
    models: [
      { model: 'gpt-4.1', requests: 12840, input_tokens: 18345000, output_tokens: 4210000, total_tokens: 22555000, cost: 70.37, actual_cost: 56.30 },
      { model: 'claude-sonnet-4', requests: 8620, input_tokens: 11240000, output_tokens: 3180000, total_tokens: 14420000, cost: 81.42, actual_cost: 69.20 },
      { model: 'gemini-2.5-pro', requests: 5340, input_tokens: 9450000, output_tokens: 1860000, total_tokens: 11310000, cost: 30.41, actual_cost: 24.33 },
    ],
    ranking: [
      { rank: 1, user_id: 101, username: '示例用户 A', requests: 8200, tokens: 16800000, actual_cost: 245.8 },
      { rank: 2, user_id: 102, username: '示例用户 B', requests: 6400, tokens: 12400000, actual_cost: 180.24 },
      { rank: 3, user_id: 103, username: '示例用户 C', requests: 4100, tokens: 7900000, actual_cost: 128.6 },
    ],
  };
  let compatibility = {
    checked_at: '2026-11-17T09:20:00+08:00', upstream_commit: 'b0fa9f3e6eeab1968a53e1289b69daa4bb40ca2b', latest_commit: 'b0fa9f3e6eeab1968a53e1289b69daa4bb40ca2b', update_available: false,
    checks: [
      { name: '账号调度字段', ok: true, detail: '示例：id / schedulable 字段存在。' },
      { name: '用户实际使用时间', ok: true, detail: '示例：last_used_at 字段存在。' },
      { name: '自定义菜单设置', ok: true, detail: '示例：自定义菜单格式兼容。' },
      { name: '上游版本', ok: true, detail: '示例：版本和基线一致。' },
    ],
  };
  function log(action, target, detail = '仅修改本页示例数据，未操作真实服务。') {
    state.audit.unshift({ at: new Date().toISOString(), action, target: String(target), status: 'ok', detail });
  }
  function response(value, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
  function bad(detail, status = 422) { return response({ detail }, status); }
  function preview(rule) {
    if (rule.type === 'subscription_multiplier') return {
      type: rule.type, actions: [{ action: rule.scope === 'global_group' ? 'set_group_rate' : 'set_user_group_rate', scope: rule.scope, user_id: rule.scope === 'global_group' ? null : 103, group_id: rule.group_id, previous_multiplier: 1, multiplier: rule.multiplier, status: 'ready' }],
      skipped: [{ user_id: 101, reason: '示例：订阅未进入到期窗口。' }], errors: [],
    };
    const frozen = state.settlements.find(item => item.month === '2026-10');
    return { type: rule.type, month: '2026-10', frozen: !!frozen,
      actions: frozen ? clone(frozen.actions) : rule.rewards.map(tier => ({ action: 'credit_balance', user_id: 100 + tier.rank, rank: tier.rank, amount: String(tier.amount), actual_cost: String(metrics.ranking[tier.rank - 1]?.actual_cost || 0), month: '2026-10', status: Number(tier.amount) > 0 ? 'ready' : 'skipped' })), skipped: [], errors: [] };
  }
  function validate(kind, value, id) {
    if (!value.name?.trim()) return '请填写规则名称。';
    if (kind === 'schedules') {
      if (!value.account_ids?.length || !value.weekdays?.length) return '请至少选择一个账号和一天。';
      if (value.start === value.end) return '开启时间与关闭时间不能相同。';
      if (value.enabled && state.schedules.some(s => s.id !== id && s.enabled && s.account_ids.some(n => value.account_ids.includes(n)))) return '账号已由其他已启用计划管理，请先暂停原计划。';
    }
    if (kind === 'rules' && value.enabled && state.rules.some(r => r.id !== id && r.enabled)) return '同一时间只启用一条闲置提醒规则。';
    if (kind === 'automations' && value.enabled && state.automations.some(r => r.id !== id && r.enabled && r.type === value.type && (r.type === 'monthly_rewards' || r.group_id === value.group_id))) return '该类规则已有启用计划，请先暂停原计划，或保存为停用。';
    return null;
  }
  function route(path, method, data) {
    if (path === '/_helper/api/status') return { timezone: 'Asia/Shanghai', mail_configured: true };
    if (path === '/_helper/api/session' && method === 'POST') return { session: 'demo-session' };
    let native = path.match(/^\/_helper\/api\/users\/(\d+)(\/email)?$/);
    if (native) {
      const user = users.find(item => item.id === Number(native[1]));
      if (!user) return bad('示例用户不存在。', 404);
      if (!native[2] && method === 'GET') return clone(user);
      if (native[2] && method === 'POST') {
        if (!data.subject?.trim() || !data.body?.trim()) return bad('请填写邮件标题和正文。');
        log('email.demo', user.id, '仅模拟发送邮件，没有连接 SMTP 或外部服务。');
        return { ok: true, demo: true };
      }
    }
    native = path.match(/^\/_helper\/api\/accounts\/(\d+)\/schedule$/);
    if (native) {
      const id = Number(native[1]), account = accounts.find(item => item.id === id);
      if (!account) return bad('示例账号不存在。', 404);
      let plan = state.schedules.find(item => item.owner === 'native-account' && item.native_account_id === id);
      const conflict = state.schedules.find(item => item.enabled && item.account_ids.includes(id) && item !== plan);
      if (method === 'PUT') {
        if (conflict) return bad('此账号由其他计划管理，请先在原计划中停用。', 409);
        if (data.mode === 'window') {
          if (!/^\d{2}:\d{2}$/.test(data.start || '') || !/^\d{2}:\d{2}$/.test(data.end || '') || data.start === data.end || !data.weekdays?.length) return bad('请填写有效时间，并至少选择一天。');
          try { new Intl.DateTimeFormat('en', { timeZone: data.timezone }).format(new Date()); } catch (_) { return bad('无效时区。'); }
          const saved = { ...clone(data), id: plan?.id || ++sequence, name: account.name + ' · 运行时段', owner: 'native-account', native_account_id: id, account_ids: [id], enabled: true };
          if (plan) state.schedules.splice(state.schedules.indexOf(plan), 1, saved); else state.schedules.push(saved);
          plan = saved;
          const parts = new Intl.DateTimeFormat('en-GB', { timeZone: plan.timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short' }).formatToParts(new Date());
          const part = kind => parts.find(item => item.type === kind)?.value;
          const day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(part('weekday'));
          const clock = part('hour') + ':' + part('minute');
          account.schedulable = plan.start < plan.end ? plan.weekdays.includes(day) && clock >= plan.start && clock < plan.end : (clock >= plan.start && plan.weekdays.includes(day)) || (clock < plan.end && plan.weekdays.includes((day + 6) % 7));
        } else if (data.mode === 'all_day') {
          account.schedulable = true;
          if (plan) state.schedules.splice(state.schedules.indexOf(plan), 1);
          plan = undefined;
        } else return bad('请选择全天或指定时段。');
        log('schedule.demo', id);
        updateStatusCells();
      }
      return { account_id: id, account_name: account.name, schedulable: account.schedulable, mode: plan ? 'window' : 'all_day', managed: !!plan, read_only: !!conflict, conflict: conflict ? '此账号由其他计划管理，请先在原计划中停用。' : null, start: plan?.start || '23:00', end: plan?.end || '09:00', timezone: plan?.timezone || 'Asia/Shanghai', weekdays: plan?.weekdays || [0, 1, 2, 3, 4, 5, 6], last_error: null };
    }
    if (path === '/api/connection') return { upstream_url: fixtureUrl, configured: true, public_url: base, timezone: 'Asia/Shanghai', session: 'demo-session' };
    if (path === '/api/status') return { upstream_url: fixtureUrl, timezone: 'Asia/Shanghai', mail_configured: true, public_url: base, version: '演示', engine: { running: true, last_tick_at: '2026-11-17T09:20:00+08:00', last_error: null } };
    if (path === '/api/accounts' && method === 'GET') return accounts;
    if (path === '/api/users' && method === 'GET') return users;
    if (path === '/api/groups') return groups;
    if (path === '/api/settlements') return state.settlements;
    if (path === '/api/audit') return state.audit;
    if (path === '/api/modules') {
      if (method === 'PUT') { state.modules = { benchmark: !!data.benchmark, pricing: !!data.pricing, ranking: !!data.ranking }; log('modules.sync', '示例菜单'); }
      return state.modules;
    }
    if (path === '/api/analytics/models') return { models: metrics.models };
    if (path === '/api/analytics/ranking') return { ranking: metrics.ranking };
    if (path === '/api/compatibility' || path === '/api/compatibility/check') { if (method === 'POST') compatibility.checked_at = new Date().toISOString(); return compatibility; }
    if (path === '/api/benchmark/options') return { keys: [{ id: 11, name: '示例分组 Key（不存在真实密钥）', group_id: 1, group_name: '夜间免费分组' }, { id: 12, name: '示例高级分组 Key', group_id: 2, group_name: '高级订阅分组' }] };
    if (path === '/api/benchmark' && method === 'POST') {
      // Mirrors helper/benchmark.py QUESTIONS; the last example deliberately fails.
      const answers = [
        ['计算 17 × 23。只输出整数答案。', '391', '391'],
        ['字符串 SUB2API 中有多少个字母（数字不计）？只输出整数。', '6', '6'],
        ['所有蓝球都是圆的。某个物体是蓝球。该物体是否一定是圆的？只输出 YES 或 NO。', 'YES', 'YES'],
        ['将列表 [9, 2, 11, -1] 从小到大排序，只输出 JSON 数组，不加解释。', '[-1,2,9,11]', '[-1,2,9,11]'],
        ['有 3 个盒子，每盒 4 个球，拿走 5 个球，还剩多少？只输出整数。', '7', '8'],
      ];
      log('benchmark.demo', data.model, '示例检测结果，无模型调用或费用。');
      return { model: data.model, score: 4, total: 5, results: answers.map(([question, expected, answer], i) => ({ question, expected, answer, passed: i < 4, latency_ms: 180 + i * 37 })) };
    }
    let match = path.match(/^\/api\/accounts\/(\d+)\/schedulable$/);
    if (match && method === 'POST') {
      const id = Number(match[1]), account = accounts.find(a => a.id === id);
      if (!account) return bad('示例账号不存在。', 404);
      if (state.schedules.some(s => s.enabled && s.account_ids.includes(id))) return bad('账号由定时计划管理，请先停用计划。', 409);
      account.schedulable = !!data.schedulable; log('account.toggle', id); return { ok: true };
    }
    match = path.match(/^\/api\/users\/(\d+)\/(email|status)$/);
    if (match && method === 'POST') {
      const user = users.find(u => u.id === Number(match[1]));
      if (!user) return bad('示例用户不存在。', 404);
      if (match[2] === 'status') { if (user.role === 'admin') return bad('示例管理员不能禁用。'); user.status = data.status; }
      log(match[2] === 'email' ? 'email.demo' : 'user.status', user.id, match[2] === 'email' ? '仅模拟邮件操作；没有发送邮件。' : '仅修改示例用户状态。'); return { ok: true, demo: true };
    }
    match = path.match(/^\/api\/(schedules|rules|automations)(?:\/(\d+))?(?:\/(preview|run))?$/);
    if (match) {
      const [, kind, rawId, action] = match, id = rawId ? Number(rawId) : null;
      const collection = state[kind], item = collection.find(value => value.id === id);
      if (!rawId && method === 'GET') return collection;
      if (rawId && !item) return bad('示例规则不存在。', 404);
      if (action && kind === 'automations') {
        const plan = preview(item);
        if (action === 'preview') return plan;
        if (!item.enabled) return bad('请先启用规则，停用规则只支持预览。', 409);
        let count = plan.actions.filter(a => a.status === 'ready').length;
        plan.actions = plan.actions.map(a => a.status === 'ready' ? { ...a, status: 'done', completed_at: new Date().toISOString() } : a);
        if (item.type === 'monthly_rewards' && !state.settlements.some(s => s.month === plan.month)) state.settlements.unshift({ month: plan.month, start_date: '2026-10-01', end_date: '2026-10-31', timezone: item.timezone, rule_id: item.id, actions: clone(plan.actions) });
        log('automation.demo', id, `仅模拟执行 ${count} 个动作，未发放真实余额或修改真实倍率。`);
        return { ...plan, executed: count };
      }
      if (method === 'DELETE') { collection.splice(collection.indexOf(item), 1); log(`${kind}.delete`, id); return { ok: true }; }
      if (method === 'POST' || method === 'PUT') {
        const error = validate(kind, data, id);
        if (error) return bad(error, 409);
        const saved = { ...clone(data), id: id || ++sequence };
        if (item) collection.splice(collection.indexOf(item), 1, saved); else collection.push(saved);
        log(`${kind}.save`, saved.id); return saved;
      }
    }
    return bad('静态演示不提供此接口；没有向外部服务发送请求。', 404);
  }
  window.__DEMO_REQUESTS__ = [];
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || !(/^\/(api\/|_helper\/api\/)/).test(url.pathname)) throw new Error('静态演示禁止外发请求。');
    const method = (init.method || 'GET').toUpperCase();
    window.__DEMO_REQUESTS__.push({ path: url.pathname, method });
    let data = {};
    try { if (init.body) data = JSON.parse(init.body); } catch (_) { return bad('演示请求格式无效。'); }
    const value = route(url.pathname, method, data);
    return value instanceof Response ? value : response(clone(value));
  };
  function pinDemoControls() {
    const key = document.getElementById('connection-key'), connect = document.getElementById('connect'), disconnect = document.getElementById('disconnect'), url = document.getElementById('connection-url'), feedback = document.getElementById('connection-feedback');
    if (!key || !connect) return;
    if (!key.disabled) key.disabled = true;
    if (key.value !== 'DEMO-NOT-A-REAL-KEY') key.value = 'DEMO-NOT-A-REAL-KEY';
    if (key.placeholder !== '固定演示凭据，请勿填写真实 Key') key.placeholder = '固定演示凭据，请勿填写真实 Key';
    if (!connect.disabled) connect.disabled = true;
    if (connect.textContent !== '演示已连接') connect.textContent = '演示已连接';
    if (!disconnect.hidden) disconnect.hidden = true;
    if (!url.readOnly) url.readOnly = true;
    const message = '仅使用本页示例数据；未连接任何 sub2api 服务。真实部署请查看上方部署说明。';
    if (feedback.textContent !== message) feedback.textContent = message;
  }
  if (nativeDemo) {
    // Return a fixed fixture JWT to the real extension without reading or
    // writing any browser credential. This adapter is never served by Helper.
    const getItem = Storage.prototype.getItem;
    Storage.prototype.getItem = function (key) { return key === 'auth_token' ? 'demo-native-jwt' : getItem.call(this, key); };
    const selected = /\/admin\/users\/?$/.test(location.pathname) ? 'users' : 'accounts';
    history.replaceState(null, '', new URL('admin/' + selected + '/', assetRoot).pathname);
    renderNative(selected);
    document.getElementById('demo-accounts').addEventListener('click', () => navigate('accounts'));
    document.getElementById('demo-users').addEventListener('click', () => navigate('users'));
    window.addEventListener('popstate', () => renderNative(/\/admin\/users\/?$/.test(location.pathname) ? 'users' : 'accounts'));
  } else {
    const observer = new MutationObserver(pinDemoControls);
    observer.observe(document.querySelector('.connection-card'), { attributes: true, childList: true, subtree: true });
    pinDemoControls();
    document.getElementById('connection-form').addEventListener('submit', event => { event.preventDefault(); event.stopImmediatePropagation(); }, true);
  }
  document.getElementById('demo-reset')?.addEventListener('click', () => location.reload());
  const theme = document.getElementById('demo-theme');
  function themeLabel() { if (theme) theme.textContent = document.documentElement.classList.contains('dark') ? '浅色' : '深色'; }
  theme?.addEventListener('click', () => { const dark = !document.documentElement.classList.contains('dark'); document.documentElement.classList.toggle('dark', dark); document.documentElement.dataset.theme = dark ? 'dark' : 'light'; themeLabel(); });
  document.addEventListener('DOMContentLoaded', themeLabel);

  function node(tag, attrs = {}, ...children) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (name === 'class') element.className = value;
      else element.setAttribute(name, String(value));
    }
    for (const child of children.flat()) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return element;
  }
  function action(label, icon) {
    const svg = helperIcon(icon);
    if (icon === 'more') svg.querySelector('path').setAttribute('d', 'M6.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0zM12.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0zM18.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0z');
    if (icon === 'ban') svg.querySelector('path').setAttribute('d', 'M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636');
    const button = node('button', { type: 'button', class: 'flex flex-col items-center gap-0.5 rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-primary-600 dark:hover:bg-dark-700 dark:hover:text-primary-400', title: label }, svg, node('span', { class: 'text-xs' }, label));
    button.addEventListener('click', () => { const message = document.getElementById('demo-native-message'); message.textContent = '这是 sub2api 原有的“' + label + '”操作。此演示展示新增的邮件与闹钟功能。'; });
    return button;
  }
  function navigate(kind) {
    history.pushState(null, '', new URL('admin/' + kind + '/', assetRoot).pathname);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }
  function updateStatusCells() {
    for (const cell of document.querySelectorAll('[data-demo-account-status]')) {
      const account = accounts.find(item => item.id === Number(cell.dataset.demoAccountStatus));
      cell.textContent = account.schedulable ? '可调度' : '暂停调度';
      cell.className = 'badge ' + (account.schedulable ? 'badge-success' : 'badge-gray');
    }
  }
  function renderNative(kind) {
    const isAccount = kind === 'accounts';
    for (const value of ['accounts', 'users']) {
      const button = document.getElementById('demo-' + value), selected = value === kind;
      button.setAttribute('aria-selected', String(selected));
      button.className = 'btn ' + (selected ? 'btn-secondary' : 'btn-ghost');
    }
    const headings = isAccount ? ['ID', '账号名称', '平台 / 类型', '分组', '调度状态', '操作'] : ['ID', '邮箱', '用户名', '角色', '状态', '操作'];
    const rows = (isAccount ? accounts : users).map(item => {
      const status = node('span', { class: 'badge ' + (item.status === 'active' ? 'badge-success' : 'badge-gray') }, item.status === 'active' ? '启用' : '禁用');
      const values = isAccount ? ['#' + item.id, node('div', { class: 'flex flex-col' }, node('span', { class: 'font-medium text-gray-900 dark:text-white' }, item.name), node('span', { class: 'text-xs text-gray-500' }, 'demo-account-' + item.id + '@example.invalid')), node('span', { class: 'badge badge-gray' }, item.platform + ' / OAuth'), groups.find(group => group.id === item.group_ids[0]).name, node('span', { 'data-demo-account-status': item.id }, '')] : ['#' + item.id, node('span', { class: 'font-medium text-gray-900 dark:text-white' }, item.email), item.username, node('span', { class: 'badge ' + (item.role === 'admin' ? 'badge-purple' : 'badge-gray') }, item.role === 'admin' ? '管理员' : '用户'), status];
      values.push(node('div', { class: 'flex items-center gap-1' }, action('编辑', 'edit'), action(isAccount ? '删除' : item.status === 'active' ? '禁用' : '启用', isAccount ? 'trash' : item.status === 'active' ? 'ban' : 'checkCircle'), action('更多', 'more')));
      return node('tr', { 'data-row-id': item.id, class: 'hover:bg-gray-50 dark:hover:bg-dark-800' }, values.map((value, index) => node('td', { class: 'whitespace-nowrap px-5 py-4 text-sm text-gray-900 dark:text-gray-100' + (index === values.length - 1 ? ' sticky-col sticky-col-right bg-white dark:bg-dark-900' : '') }, value)));
    });
    const table = node('table', { class: 'w-full min-w-max divide-y divide-gray-200 dark:divide-dark-700' }, node('thead', { class: 'table-header bg-gray-50 dark:bg-dark-800' }, node('tr', {}, headings.map((text, index) => node('th', { scope: 'col', class: 'px-5 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-dark-400' + (index === headings.length - 1 ? ' sticky-col sticky-col-right bg-gray-50 dark:bg-dark-800' : '') }, text)))), node('tbody', { class: 'divide-y divide-gray-200 bg-white dark:divide-dark-700 dark:bg-dark-900' }, rows));
    const target = document.getElementById('demo-native-list');
    target.setAttribute('aria-label', isAccount ? '账号列表' : '用户列表');
    target.replaceChildren(node('div', { class: 'table-page-layout' }, node('div', { class: 'table-scroll-container' }, node('div', { class: 'table-wrapper demo-native-table' }, table))), node('p', { id: 'demo-native-message', class: 'demo-native-message', role: 'status' }, isAccount ? '首个账号展示 23:00–09:00 时段，其余账号默认未接管。' : '点击行末新增的邮件图标，向选中的示例用户发信。'));
    updateStatusCells();
  }
})();

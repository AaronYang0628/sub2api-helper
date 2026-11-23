/* Native row enhancements for Wei-Shaw/sub2api v0.2.23.
 * SVG geometry and native control classes follow its Icon.vue and admin views.
 * Upstream portions are LGPL-3.0; see THIRD_PARTY_NOTICES.md and LICENSES/.
 * This bridge never reads refresh credentials or changes native login state.
 */
'use strict';
(() => {
  if (window.__sub2apiHelperNative) return;
  window.__sub2apiHelperNative = true;
  const API = '/_helper/api';
  const TABLE = '.table-page-layout .table-scroll-container';
  const BUTTON = 'helper-native-action shrink-0 whitespace-nowrap flex flex-col items-center gap-0.5 rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-primary-600 dark:hover:bg-dark-700 dark:hover:text-primary-400';
  const DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  const CONSOLE_SESSION_KEY = 'helper_native_console_session';
  const ADVANCED = { rules: '规则管理', modules: '功能与菜单', analytics: '智商检测', rewards: '排名与奖励', system: '运行与系统' };
  const DEFAULT_TEMPLATES = [
    { id: 'builtin:inactive', name: '长期未使用提醒', subject: '账户长期未使用提醒', body: '您好 {username}，您的账户近期未使用，请及时登录查看。如有疑问请联系管理员。', builtin: true },
    { id: 'builtin:maintenance', name: '服务维护通知', subject: '服务维护通知', body: '您好 {username}，服务将进行维护，请留意管理员后续通知。', builtin: true },
  ];
  const state = { key: '', phase: 'idle', status: {}, error: '', scheduled: false, dialog: null, generation: 0, observedToken: undefined };
  const paths = {
    clock: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
    mail: 'M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75',
    x: 'M6 18L18 6M6 6l12 12',
  };
  function element(tag, properties = {}, ...children) {
    const result = document.createElement(tag);
    for (const [key, value] of Object.entries(properties)) {
      if (value === undefined || value === null) continue;
      if (key === 'class') result.className = value;
      else if (key.startsWith('on')) result.addEventListener(key.slice(2), value);
      else if (['value', 'disabled', 'checked', 'hidden', 'readOnly', 'required'].includes(key)) result[key] = value;
      else result.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && child !== false) result.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return result;
  }
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', class: 'h-4 w-4', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', paths[name]); path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round'); svg.append(path); return svg;
  }
  function button(text, action, classes = 'btn btn-secondary') { return element('button', { type: 'button', class: classes, onclick: action }, text); }
  let fieldSequence = 0;
  function field(label, control, hint) {
    const simple = control.matches('input,select,textarea');
    if (simple && !control.id) control.id = `helper-native-field-${++fieldSequence}`;
    return element('div', { class: 'helper-native-field' }, element(simple ? 'label' : 'span', { class: 'input-label', ...(simple ? { for: control.id } : {}) }, label), control, hint ? element('small', { class: 'input-hint' }, hint) : null);
  }
  function input(name, value, type = 'text', properties = {}) { return element('input', { class: ['radio', 'checkbox'].includes(type) ? 'helper-native-check' : 'input', name, type, value, ...properties }); }
  function message(text, kind = '') { return element('p', { class: `helper-native-message ${kind}`, role: kind === 'error' ? 'alert' : 'note' }, text); }
  function currentKind() { return location.pathname.match(/\/admin\/(accounts|users)\/?$/)?.[1] || null; }
  function token() { try { return localStorage.getItem('auth_token') || ''; } catch (_) { return ''; } }
  function validID(value) { return typeof value === 'string' && /^[1-9][0-9]*$/.test(value) && Number.isSafeInteger(Number(value)) ? value : null; }
  function visible(node) { return node.isConnected && node.getClientRects().length > 0 && !node.closest('[hidden], [aria-hidden="true"]'); }
  function errorMessage(error) { return error?.message || '助手请求未完成，请稍后重试。'; }
  async function request(path, method = 'GET', body) {
    const credential = token();
    if (!credential) throw new Error('请先登录 sub2api 管理员账号。');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(API + path, { method, credentials: 'omit', redirect: 'error', signal: controller.signal,
        headers: { Authorization: `Bearer ${credential}`, 'X-Admin-UI-Request': '1', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
      let data; try { data = await response.json(); } catch (_) { throw new Error('助手响应格式不兼容，请检查代理和版本。'); }
      if (!response.ok) {
        let detail = typeof data?.detail === 'string' ? data.detail : data?.detail?.message || '助手拒绝了此操作。';
        if (response.status === 429) { const retry = response.headers.get('Retry-After'); detail = /^\d+$/.test(retry || '') ? `操作过于频繁，请 ${retry} 秒后重试。` : '操作过于频繁，请稍后再试。'; }
        if (response.status === 401 || response.status === 403) { state.phase = 'error'; state.error = detail; scheduleScan(); }
        throw new Error(detail);
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('助手请求超时，请检查服务后重试。');
      if (error instanceof TypeError) throw new Error('无法连接助手服务，请检查代理与网络。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function clearConsoleSession() { try { sessionStorage.removeItem(CONSOLE_SESSION_KEY); } catch (_) { /* Storage may be unavailable. */ } }
  function revokeNativeSession(credential) {
    // Logout remains entirely upstream-owned. This separate request only
    // invalidates Helper sessions associated with the previously observed JWT.
    try {
      void fetch(API + '/session/revoke', { method: 'POST', credentials: 'omit', redirect: 'error', keepalive: true,
        headers: { Authorization: `Bearer ${credential}`, 'X-Admin-UI-Request': '1' } }).catch(() => {});
    } catch (_) { /* Best effort: local cleanup must never depend on the network. */ }
  }
  function checkIdentity() {
    const credential = token();
    if (state.observedToken !== credential) {
      if (state.observedToken) revokeNativeSession(state.observedToken);
      clearConsoleSession(); closeDialog(); state.observedToken = credential; state.key = ''; state.phase = 'idle';
    }
    if (!currentKind()) { clearConsoleSession(); if (state.dialog) closeDialog(); }
    return credential;
  }
  function closeDialog() {
    state.generation++;
    const dialog = state.dialog; state.dialog = null;
    if (dialog) { if (dialog.open) dialog.close(); dialog.remove(); }
  }
  function openDialog(title, wide = false) {
    closeDialog();
    const generation = state.generation;
    const dialog = element('dialog', { class: `modal-content helper-native-dialog${wide ? ' helper-native-wide' : ''}`, 'aria-label': title });
    const body = element('div', { class: 'modal-body helper-native-body' }, message('正在读取最新数据…'));
    const footer = element('div', { class: 'modal-footer helper-native-footer' });
    const heading = element('h3', { class: 'modal-title' }, title);
    const close = button(icon('x'), closeDialog, 'btn btn-ghost btn-icon'); close.setAttribute('aria-label', '关闭');
    dialog.append(element('div', { class: 'modal-header' }, heading, close), body, footer);
    dialog.addEventListener('cancel', () => closeDialog());
    document.body.append(dialog); state.dialog = dialog; dialog.showModal();
    return { dialog, body, footer, heading, current: () => state.dialog === dialog && generation === state.generation };
  }
  function advancedControls(view) {
    const select = element('select', { class: 'input', 'aria-label': '高级功能' }, Object.entries(ADVANCED).map(([key, title]) => element('option', { value: key }, title)));
    const feedback = message('', 'error'); feedback.hidden = true;
    const open = button('打开', async () => {
      open.disabled = true; feedback.hidden = true;
      try {
        const result = await request('/session', 'POST', {});
        if (!view.current()) return;
        if (typeof result.session !== 'string' || !result.session) throw new Error('助手未返回有效会话。');
        try { sessionStorage.setItem(CONSOLE_SESSION_KEY, result.session); } catch (_) { throw new Error('浏览器阻止保存助手会话，暂时无法打开高级功能。'); }
        const route = select.value;
        const panel = openDialog(ADVANCED[route], true);
        const url = new URL('/_helper/console', location.origin);
        url.searchParams.set('theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light'); url.hash = route;
        panel.body.replaceChildren(element('iframe', { class: 'helper-native-console', src: url.href, title: ADVANCED[route] }));
        panel.footer.replaceChildren(button('关闭', closeDialog));
      } catch (error) { if (view.current()) { feedback.textContent = errorMessage(error); feedback.hidden = false; } }
      finally { open.disabled = false; }
    });
    return element('details', { class: 'helper-native-advanced' }, element('summary', {}, '高级功能'), element('div', { class: 'helper-native-advanced-row' }, select, open), feedback);
  }
  function formatWeekdays(weekdays) {
    const list = Array.isArray(weekdays) ? weekdays : [];
    return list.length === 7 ? '每天' : list.length ? list.map(n => DAYS[n]).filter(Boolean).join('、') : '未选择星期';
  }
  function formatScheduleWindow(start, end, tz, weekdays) {
    const overnight = end && start && end < start;
    return `${formatWeekdays(weekdays)} ${start || '23:00'} 开启、${overnight ? '次日 ' : ''}${end || '09:00'} 关闭（${tz}）`;
  }
  function renderEmailTemplate(text, user) {
    return String(text || '')
      .replaceAll('{username}', user.username || user.email || '')
      .replaceAll('{email}', user.email || '')
      .replaceAll('{last_used_at}', user.last_used_at || '暂无记录');
  }
  async function accountDialog(id) {
    const view = openDialog('账号定时调度');
    try {
      const data = await request(`/accounts/${id}/schedule`);
      if (!view.current()) return;
      if (String(data.account_id) !== id || typeof data.schedulable !== 'boolean') throw new Error('账号身份或状态响应不完整，已停止操作。');
      const accountLabel = data.account_name || `账号 #${id}`;
      view.heading.textContent = `账号定时 · ${data.account_name || `#${id}`}`;
      const readOnly = data.read_only === true || !!data.conflict;
      const defaultZone = state.status.timezone || 'Asia/Shanghai';
      const availableSchedules = Array.isArray(data.available_schedules) ? data.available_schedules : [];
      const enabledSchedules = availableSchedules.filter(item => item && item.enabled);
      const shared = data.shared_schedule && typeof data.shared_schedule === 'object' ? data.shared_schedule : null;
      const useRule = input('mode', 'rule', 'radio', { checked: data.mode === 'rule', disabled: readOnly || !enabledSchedules.length });
      const windowed = input('mode', 'window', 'radio', { checked: data.mode === 'window', disabled: readOnly });
      const allDay = input('mode', 'all_day', 'radio', { checked: data.mode !== 'window' && data.mode !== 'rule', disabled: readOnly });
      const ruleSelect = element('select', { class: 'input', name: 'schedule_id', 'aria-label': '已有时段规则' },
        availableSchedules.length
          ? availableSchedules.map(item => element('option', {
            value: String(item.id),
            disabled: !item.enabled,
            selected: shared ? item.id === shared.id : (enabledSchedules[0] && item.id === enabledSchedules[0].id),
          }, `${item.name}（${item.start}–${item.end}${item.enabled ? '' : ' · 未启用'}）`))
          : [element('option', { value: '', disabled: true, selected: true }, '暂无可用时段规则')]);
      const rulePreview = element('p', { class: 'helper-native-summary', 'data-helper-rule-preview': 'true', role: 'status' });
      const start = input('start', data.start || '23:00', 'time', { required: true });
      const end = input('end', data.end || '09:00', 'time', { required: true });
      const ruleName = input('name', typeof data.name === 'string' ? data.name : '', 'text', { maxlength: 100, placeholder: `账号 #${id} 定时调度` });
      const zone = input('timezone', data.timezone || defaultZone, 'text', { required: true });
      const weekdays = element('div', { class: 'helper-native-weekdays' }, DAYS.map((day, n) => element('label', {}, input('weekdays', String(n), 'checkbox', { checked: (data.weekdays || [0, 1, 2, 3, 4, 5, 6]).includes(n) }), day)));
      const summary = element('p', { class: 'helper-native-summary', 'data-helper-schedule-summary': 'true', role: 'status' });
      const allDayPreview = element('p', { class: 'helper-native-summary', 'data-helper-allday-preview': 'true', role: 'status' });
      const updateRulePreview = () => {
        const chosen = availableSchedules.find(item => String(item.id) === ruleSelect.value && item.enabled);
        if (!chosen) {
          rulePreview.textContent = '暂无已启用的时段规则，请先在「高级功能 → 规则管理」中创建并启用。';
          return;
        }
        const actionText = chosen.in_window_now ? '开启调度' : '关闭调度';
        rulePreview.textContent = `预览：将 ${accountLabel}（#${id}）加入「${chosen.name}」（${formatScheduleWindow(chosen.start, chosen.end, chosen.timezone || defaultZone, chosen.weekdays)}，当前共 ${chosen.account_count || 0} 个账号），保存后当前将设为${actionText}。不会修改规则内的其他账号。`;
      };
      const updateSummary = () => {
        const selectedDays = [...weekdays.querySelectorAll('input:checked')].map(control => Number(control.value));
        const tz = zone.value.trim() || defaultZone;
        summary.textContent = `${accountLabel} ${formatScheduleWindow(start.value || '23:00', end.value || '09:00', tz, selectedDays)}`;
      };
      const updateAllDayPreview = () => {
        allDayPreview.textContent = `预览：保存后将把 ${accountLabel}（#${id}）设为开启调度${data.managed ? '，并移除本账号的独立定时计划' : ''}。`;
      };
      const ruleFields = element('div', { class: 'helper-native-window' },
        field('选择已有时段规则', ruleSelect, '规则在助手「规则管理」中统一维护；仅启用状态的规则可直接应用。'),
        rulePreview);
      const moreSettings = element('details', { class: 'helper-native-more' },
        element('summary', {}, '更多设置'),
        element('div', { class: 'helper-native-more-body' },
          field('规则名称', ruleName),
          field('开启日期', weekdays, '日期按开启时间所属日计算。'),
          field('时区', zone, '默认使用当前配置的时区。')));
      const windowFields = element('div', { class: 'helper-native-window' },
        element('div', { class: 'helper-native-two' }, field('开启时间', start), field('关闭时间', end)),
        summary,
        moreSettings,
        message('保存后按所选时段接管账号调度：窗口内开启，窗口外关闭。'));
      const feedback = message('', 'error'); feedback.hidden = true;
      const formId = 'helper-native-account-form';
      const submit = element('button', { type: 'submit', form: formId, class: 'btn btn-primary', disabled: readOnly }, '保存设置');
      const form = element('form', { id: formId, class: 'helper-native-form' });
      let sharedCard = null;
      if (shared) {
        const otherCount = Number(shared.other_account_count) || 0;
        const removePreview = otherCount > 0
          ? `移出预览：仅把 ${accountLabel}（#${id}）从「${shared.name}」移出并设为开启调度，其余 ${otherCount} 个账号保持不变。`
          : `移出预览：将把 ${accountLabel}（#${id}）从「${shared.name}」移出并设为开启调度；移除后该规则将自动停用。`;
        const removeButton = button('仅将此账号移出规则并改为全天', async () => {
          removeButton.disabled = true; feedback.hidden = true;
          try {
            await request(`/accounts/${id}/schedule`, 'PUT', { mode: 'all_day', remove_from_schedule_id: shared.id });
            if (!view.current()) return;
            view.body.replaceChildren(message(`已将 ${accountLabel} 从「${shared.name}」移出并设为全天运行。原生列表将在下次刷新时同步。`, 'success'));
            view.footer.replaceChildren(advancedControls(view), button('关闭', closeDialog));
          } catch (error) {
            if (view.current()) { feedback.textContent = errorMessage(error); feedback.hidden = false; removeButton.disabled = false; }
          }
        }, 'btn btn-secondary btn-sm');
        sharedCard = element('div', { class: 'helper-native-shared', 'data-helper-shared-schedule': String(shared.id) },
          element('div', { class: 'helper-native-shared-title' }, `当前所属规则：${shared.name}`),
          message(`规则时段：${formatScheduleWindow(shared.start, shared.end, shared.timezone || defaultZone, shared.weekdays)}（当前${shared.in_window_now ? '处于开启时段' : '处于关闭时段'}）。`),
          message(`为避免影响同一规则下的其他账号，此处不直接修改该规则。${removePreview}`),
          element('div', { class: 'helper-native-shared-actions' }, removeButton));
      }
      const updateMode = () => {
        ruleFields.hidden = !useRule.checked;
        windowFields.hidden = !windowed.checked;
        allDayPreview.hidden = readOnly || !allDay.checked;
        ruleSelect.disabled = readOnly || !useRule.checked || !enabledSchedules.length;
        for (const control of windowFields.querySelectorAll('input')) control.disabled = readOnly || !windowed.checked;
        submit.disabled = readOnly || (useRule.checked && !enabledSchedules.length);
        updateRulePreview(); updateSummary(); updateAllDayPreview();
      };
      useRule.addEventListener('change', updateMode); allDay.addEventListener('change', updateMode); windowed.addEventListener('change', updateMode);
      ruleSelect.addEventListener('change', updateRulePreview);
      start.addEventListener('input', updateSummary); end.addEventListener('input', updateSummary);
      zone.addEventListener('input', updateSummary); weekdays.addEventListener('change', updateSummary);
      updateMode();
      form.append(...[message(`账号 #${id} · 当前参与调度：${data.schedulable ? '开启' : '关闭'}。打开窗口不会改变当前状态。`),
        data.conflict ? message(data.conflict, 'warning') : null,
        sharedCard,
        data.last_error ? message(`最近执行错误：${data.last_error}，请检查执行记录。`, 'warning') : null,
        element('div', { class: 'helper-native-mode' },
          element('label', {}, useRule, enabledSchedules.length ? '应用已有时段规则' : '应用已有时段规则（暂无启用规则）'),
          element('label', {}, windowed, '自定义此账号时段'),
          element('label', {}, allDay, data.managed ? '全天运行' : '全天（未接管）')),
        message('全天模式默认不创建定时计划，保留原生手动开关。只有明确保存时，才会开启参与调度并移除本账号由助手接管的定时计划。'),
        ruleFields, windowFields, allDayPreview, feedback].filter(Boolean));
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (readOnly) return;
        let value;
        if (useRule.checked) {
          const scheduleId = Number(ruleSelect.value);
          const chosen = enabledSchedules.find(item => item.id === scheduleId);
          if (!chosen) { feedback.textContent = '请选择一条已启用的时段规则。'; feedback.hidden = false; return; }
          value = { mode: 'rule', schedule_id: chosen.id };
        } else if (windowed.checked) {
          const trimmedName = ruleName.value.trim();
          value = { mode: 'window', ...(trimmedName ? { name: trimmedName } : {}), start: start.value, end: end.value, timezone: zone.value.trim() || defaultZone, weekdays: [...weekdays.querySelectorAll('input:checked')].map(control => Number(control.value)) };
        } else {
          value = { mode: 'all_day' };
        }
        feedback.hidden = true;
        if (value.mode === 'window' && !value.weekdays.length) { feedback.textContent = '请至少选择一个开启日期。'; feedback.hidden = false; return; }
        submit.disabled = true;
        try {
          await request(`/accounts/${id}/schedule`, 'PUT', value);
          if (!view.current()) return;
          view.body.replaceChildren(message('设置已保存。原生列表的调度状态将在下次刷新时同步。', 'success'));
          view.footer.replaceChildren(advancedControls(view), button('关闭', closeDialog));
        } catch (error) { if (view.current()) { feedback.textContent = errorMessage(error); feedback.hidden = false; } }
        finally { submit.disabled = readOnly || (useRule.checked && !enabledSchedules.length); }
      });
      view.body.replaceChildren(form);
      view.footer.replaceChildren(advancedControls(view), button('取消', closeDialog), submit);
    } catch (error) { if (view.current()) { view.body.replaceChildren(message(errorMessage(error), 'error')); view.footer.replaceChildren(button('关闭', closeDialog)); } }
  }
  async function userDialog(id) {
    const view = openDialog('发送邮件');
    try {
      const user = await request(`/users/${id}`);
      if (!view.current()) return;
      if (String(user.id) !== id || typeof user.email !== 'string' || !user.email) throw new Error('用户身份或邮箱响应不完整，已停止操作。');
      view.heading.textContent = `发送邮件 · 用户 #${id}`;
      const templates = Array.isArray(user.templates) && user.templates.length ? user.templates : DEFAULT_TEMPLATES;
      const subject = input('subject', '', 'text', { required: true, maxlength: 200 });
      const body = element('textarea', { class: 'input', name: 'body', rows: '6', required: true, maxlength: '20000' });
      const template = element('select', { class: 'input', name: 'template', 'aria-label': '邮件模板' }, [
        element('option', { value: 'custom' }, '自定义邮件'),
        ...templates.map(item => element('option', { value: String(item.id) }, `${item.name}${item.builtin ? '（内置）' : '（规则模板）'}`)),
      ]);
      const previewSubject = element('div', {});
      const previewBody = element('pre', { class: 'helper-native-preview-body' });
      const updatePreview = () => {
        previewSubject.textContent = `主题：${subject.value.trim() || '（未填写）'}`;
        previewBody.textContent = body.value.trim() || '（未填写）';
      };
      template.addEventListener('change', () => {
        if (template.value === 'custom') { updatePreview(); return; }
        const chosen = templates.find(item => String(item.id) === template.value || String(item.id) === `builtin:${template.value}`);
        if (chosen) {
          subject.value = renderEmailTemplate(chosen.subject, user);
          body.value = renderEmailTemplate(chosen.body, user);
          updatePreview();
        }
      });
      subject.addEventListener('input', updatePreview);
      body.addEventListener('input', updatePreview);
      updatePreview();
      const previewBox = element('div', { class: 'helper-native-preview', 'data-helper-email-preview': 'true' },
        element('div', { class: 'helper-native-preview-title' }, '发送前预览'),
        element('div', {}, `收件人：${user.email}（用户 #${id}${user.username ? ` · ${user.username}` : ''}）`),
        previewSubject,
        previewBody);
      const feedback = message('', 'error'); feedback.hidden = true;
      const formId = 'helper-native-user-form';
      const submit = element('button', { class: 'btn btn-primary', type: 'submit', form: formId, disabled: !state.status.mail_configured }, '发送邮件');
      const form = element('form', { id: formId, class: 'helper-native-form' },
        element('div', { class: 'helper-native-recipient' }, element('strong', {}, user.email), element('span', {}, `用户 #${id}${user.username ? ` · ${user.username}` : ''}`)),
        message('仅向当前选中的用户发信，不批量发送、不自动修改用户状态。'),
        !state.status.mail_configured ? message('邮件服务尚未配置，请先配置 Helper 的 SMTP 环境变量。', 'warning') : null,
        field('邮件模板', template, '可在「高级功能 → 规则管理」中新增或编辑可复用模板。'),
        field('主题', subject),
        field('正文', body, '以纯文本邮件发送。'),
        previewBox,
        feedback);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (!state.status.mail_configured) return;
        submit.disabled = true; feedback.hidden = true;
        try {
          await request(`/users/${id}/email`, 'POST', { subject: subject.value, body: body.value });
          if (!view.current()) return;
          view.body.replaceChildren(message(`邮件已发送给用户 #${id}。`, 'success'));
          view.footer.replaceChildren(advancedControls(view), button('关闭', closeDialog));
        } catch (error) { if (view.current()) { feedback.textContent = errorMessage(error); feedback.hidden = false; } }
        finally { submit.disabled = !state.status.mail_configured; }
      });
      view.body.replaceChildren(form); view.footer.replaceChildren(advancedControls(view), button('取消', closeDialog), submit);
    } catch (error) { if (view.current()) { view.body.replaceChildren(message(errorMessage(error), 'error')); view.footer.replaceChildren(button('关闭', closeDialog)); } }
  }
  function mobileInfo(card) {
    const field = card.querySelector('[data-field="id"]');
    if (!field || !visible(field) || field.children.length !== 2) return null;
    // This is the dedicated native ID cell, never an email/name or row position.
    const value = field.children[1].textContent.trim().match(/^#?([1-9][0-9]*)$/)?.[1];
    const id = validID(value);
    const content = field.parentElement, actions = content?.lastElementChild;
    if (!id || !actions || actions.hasAttribute('data-field') || actions === field) return null;
    const group = actions.querySelector(':scope > div');
    return group?.querySelector('button:not([data-helper-native-action])') ? { row: card, group, id, mobile: true } : null;
  }
  function rows() {
    const result = [];
    for (const row of document.querySelectorAll(`${TABLE} .table-wrapper > table > tbody > tr[data-row-id]`)) {
      const id = validID(row.dataset.rowId), group = row.querySelector(':scope > td:last-child > div');
      if (id && visible(row) && group?.querySelector('button:not([data-helper-native-action])')) result.push({ row, group, id, mobile: false });
    }
    for (const field of document.querySelectorAll(`${TABLE} [data-field="id"]`)) {
      const card = field.parentElement?.parentElement;
      if (!card) continue;
      const info = mobileInfo(card); if (info) result.push(info);
    }
    return result;
  }
  function currentRowID(control) {
    const row = control.closest('tr[data-row-id]');
    if (row) return visible(row) ? validID(row.dataset.rowId) : null;
    const card = control.closest('[data-helper-native-card]');
    return card ? mobileInfo(card)?.id || null : null;
  }
  function inject(info, kind) {
    if (info.mobile) info.row.setAttribute('data-helper-native-card', 'true');
    let action = info.group.querySelector(`[data-helper-native-action="${kind}"]`);
    if (action) return;
    action = element('button', { type: 'button', class: BUTTON, 'data-helper-native-action': kind,
      'aria-label': kind === 'accounts' ? '账号定时设置' : '给此用户发送邮件', title: kind === 'accounts' ? '账号定时设置' : '发送邮件' }, icon(kind === 'accounts' ? 'clock' : 'mail'), element('span', { class: 'text-xs' }, kind === 'accounts' ? '定时' : '邮件'));
    action.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      if (currentKind() !== kind || state.phase !== 'ready') return;
      const id = currentRowID(action);
      if (!id) return;
      if (kind === 'accounts') accountDialog(id); else userDialog(id);
    });
    info.group.append(action);
  }
  function updateMobileHint(kind, availableRows) {
    const previous = [...document.querySelectorAll('[data-helper-native-mobile-hint]')];
    const fields = kind && state.phase === 'ready' && !availableRows.length
      ? [...document.querySelectorAll(`${TABLE} [data-field]`)].filter(visible) : [];
    const container = fields[0]?.closest('.table-scroll-container');
    if (!container) { for (const hint of previous) hint.remove(); return; }
    const text = fields.some(field => field.dataset.field === 'id')
      ? '当前 ID 格式不兼容，助手未添加行操作。'
      : '在列设置中显示 ID 后，可使用邮件或定时按钮。';
    let hint = previous.find(node => node.parentElement === container);
    if (!hint) { hint = element('p', { class: 'helper-native-message px-4 py-3', 'data-helper-native-mobile-hint': 'true', role: 'status' }, text); container.prepend(hint); }
    else if (hint.textContent !== text) hint.textContent = text;
    for (const old of previous) if (old !== hint) old.remove();
  }
  function scan() {
    state.scheduled = false;
    const kind = currentKind(), credential = checkIdentity(), key = `${kind || ''}:${credential}`;
    const availableRows = kind ? rows() : [];
    const groups = new Set(availableRows.map(info => info.group));
    for (const node of document.querySelectorAll('[data-helper-native-action]')) {
      if (!kind || !groups.has(node.parentElement) || node.dataset.helperNativeAction !== kind || state.phase !== 'ready') node.remove();
    }
    if (!kind) { state.key = ''; state.phase = 'idle'; document.querySelector('[data-helper-native-unavailable]')?.remove(); updateMobileHint(null, []); return; }
    if (state.key !== key) {
      state.key = key; state.phase = credential ? 'loading' : 'error'; state.error = '请先登录 sub2api 管理员账号。';
      if (credential) request('/status').then(data => { if (state.key === key) { state.status = data; state.phase = 'ready'; scheduleScan(); } }).catch(error => { if (state.key === key) { state.phase = 'error'; state.error = errorMessage(error); scheduleScan(); } });
    }
    const indicator = document.querySelector('[data-helper-native-unavailable]');
    if (state.phase === 'ready') { indicator?.remove(); for (const info of availableRows) inject(info, kind); }
    else if (state.phase === 'error' && availableRows.length) {
      if (indicator) { indicator.disabled = false; indicator.title = state.error; }
      if (indicator && indicator.parentElement !== availableRows[0].group) indicator.remove();
      if (!document.querySelector('[data-helper-native-unavailable]')) availableRows[0].group.append(element('button', { type: 'button', class: 'helper-native-unavailable', 'data-helper-native-unavailable': 'true', title: state.error, 'aria-label': '重试连接助手', onclick: event => { event.preventDefault(); event.stopPropagation(); event.currentTarget.disabled = true; state.key = ''; state.phase = 'idle'; scheduleScan(); } }, '助手暂不可用 · 重试'));
    }
    updateMobileHint(kind, availableRows);
  }
  function scheduleScan() { if (!state.scheduled) { state.scheduled = true; requestAnimationFrame(scan); } }
  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-row-id', 'hidden', 'aria-hidden'] });
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name];
    history[name] = function (...args) { const result = original.apply(this, args); closeDialog(); checkIdentity(); scheduleScan(); return result; };
  }
  window.addEventListener('popstate', () => { closeDialog(); checkIdentity(); scheduleScan(); });
  window.addEventListener('resize', scheduleScan);
  window.addEventListener('storage', event => { if (event.key === 'auth_token' || event.key === null) { closeDialog(); checkIdentity(); scheduleScan(); } });
  window.addEventListener('pagehide', checkIdentity);
  window.addEventListener('focus', scheduleScan);
  document.addEventListener('visibilitychange', scheduleScan);
  scheduleScan();
})();

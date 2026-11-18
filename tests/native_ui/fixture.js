'use strict';
(() => {
  const target = document.getElementById('fixture-table');
  let kind = location.pathname.includes('/users') ? 'users' : 'accounts';
  let ids = kind === 'users' ? [7, 8] : [1, 2];
  let showMobileID = true;
  const e = (name, attrs = {}, text) => { const node = document.createElement(name); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); if (text !== undefined) node.textContent = text; return node; };
  function nativeActions() {
    const group = e('div', { class: 'flex items-center gap-1' });
    for (const label of ['编辑', '更多']) { const b = e('button', { type: 'button', class: 'btn btn-ghost btn-sm' }, label); b.addEventListener('click', () => { window.fixture.nativeClicks++; }); group.append(b); }
    return group;
  }
  function render() {
    target.replaceChildren();
    const wrap = e('div', { class: 'table-wrapper fixture-desktop' }), table = e('table', { class: 'table w-full' }), body = e('tbody');
    const head = e('thead'), hrow = e('tr'); for (const label of ['ID', '名称', '操作']) hrow.append(e('th', {}, label)); head.append(hrow); table.append(head);
    for (const id of ids) { const tr = e('tr', { 'data-row-id': String(id) }); tr.append(e('td', {}, id), e('td', {}, '重复显示名称')); const cell = e('td'); cell.append(nativeActions()); tr.append(cell); body.append(tr); }
    table.append(body); wrap.append(table); target.append(wrap);
    const mobile = e('div', { class: 'fixture-mobile' });
    for (const id of ids) {
      const card = e('div', { class: 'fixture-mobile-card rounded-lg border border-gray-200' }), content = e('div', { class: 'space-y-3' });
      if (showMobileID) { const field = e('div', { 'data-field': 'id', class: 'fixture-id' }); field.append(e('span', {}, 'ID'), e('div', {}, '#' + id)); content.append(field); }
      const name = e('div', { 'data-field': kind === 'users' ? 'email' : 'name', class: 'fixture-name' }); name.append(e('span', {}, '名称'), e('div', {}, '重复显示名称')); content.append(name);
      const actions = e('div', { class: 'fixture-mobile-actions border-t pt-3' }); actions.append(nativeActions()); content.append(actions); card.append(content); mobile.append(card);
    }
    target.append(mobile);
  }
  window.fixture = {
    nativeClicks: 0,
    route(next) { kind = next; ids = next === 'users' ? [7, 8] : [1, 2]; history.pushState({}, '', '/admin/' + next); render(); },
    recycle(id) { const row = target.querySelector('tr[data-row-id]'); row.dataset.rowId = String(id); row.children[0].textContent = String(id); },
    render,
    mobileID(show) { showMobileID = show; render(); },
    rows(values) { ids = values; render(); },
    identity(value) { if (value) localStorage.setItem('auth_token', value); else localStorage.removeItem('auth_token'); document.getElementById('fixture-change').textContent = value || 'removed'; window.dispatchEvent(new StorageEvent('storage', { key: 'auth_token' })); },
  };
  document.getElementById('users').addEventListener('click', () => window.fixture.route('users'));
  document.getElementById('accounts').addEventListener('click', () => window.fixture.route('accounts'));
  document.getElementById('dark').addEventListener('click', () => document.documentElement.classList.toggle('dark'));
  localStorage.setItem('auth_token', 'fixture-admin-jwt');
  localStorage.setItem('auth_user', JSON.stringify({ role: 'admin' }));
  render();
})();

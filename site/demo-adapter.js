/* GitHub Pages adapter: every API operation is simulated in memory.
 * No backend, SMTP, model call, credential exchange or persistent user data.
 */
'use strict';
(() => {
  const assetRoot = new URL('../', document.currentScript.src);
  const base = assetRoot.href.replace(/\/$/, '');
  const nativeDemo = document.body.classList.contains('demo-native');
  const resultDemo = document.body.classList.contains('demo-benchmark');
  const galleryDemo = document.body.classList.contains('demo-gallery');
  // Small offline fixtures: a 240x96 PNG and a two-frame 64x24 GIF.
  const benchmarkPng = 'data:image/png;base64,' +
    'iVBORw0KGgoAAAANSUhEUgAAAPAAAABgCAYAAAAjM//RAAAB6ElEQVR4nO3TsW0VURBAUTeASFwfJRBSAwU4oQFqoAhCSiBy' +
    'BAkBCEE6AUIY/X3Pd/cEJx2NRnPvvvz8/gtoutu9APD/BAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZg' +
    'CBMwhAkYwm4W8McfX4F/JGAIEzCECRjCBAxhAj7I/Yf3T7J7X5oEfBABs4KADyJgVhDwQQTMCgI+iIBZQcAHETArCHh48fD2' +
    'Sf42S8CsIOBBwNQIeDhTwC/fvCZEwAIWcJiABSzgMAELWMBhAhawgMMuG/AtDyVgBCxgAXPTvxTwBQPmGgQ8CJgaAQ8CpkbA' +
    'g4CpEfAgYGoEPAiYGgEPAqZGwIOAqRHwIGBqBDycKeBXj584wO5gBSxgBCxgAV/T7mAFLGAELGABX9PuYAUsYAQs4OceMNcg' +
    '4EHA1Ah4EDA1Ah4ETI2ABwFTI+BBwNQIeBAwNQIeBEyNgIczBfzu2+dL2h2UgAUsYAFfJ2D+bHdIAl5DwCe1OyQBryHgk9od' +
    'koDXEDCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChrBnFzCwnoAhTMAQJmAIEzCECRjCBAxhAoYwAUOYgCFMwBAm' +
    'YAgTMIQJGMIEDGEChjABQ5iAIew3C7pDy/nEBHwAAAAASUVORK5CYII=';
  const benchmarkGif = 'data:image/gif;base64,' +
    'R0lGODlhQAAYAIEAAPD9+g2UiJn25A92biH/C05FVFNDQVBFMi4wAwEAAAAh+QQAKAAAACwAAAAAQAAYAAAC/wRBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEP8EQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAE' +
    'QRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAE' +
    'QRAEQRAEQRAEQRAEQRAEQRAEQRAEwTAMwzAMwzAMwzAMQ1EURVEURVEURVEURVEURVEURVEURVEURVEURRAEQRAEQRAEwTAM' +
    'wzAMwzAMwzAMQ1EURVEURVEURVEURVEURVEURVEURVEURVEURRAEQRAEQRAEwTAMwzAMwzAMwzAMQ1EURVEURVEURVEURVH/' +
    'FEVRFEVRFEVRFEVRFEUQBEEQBEEQBMEwDMMwDMMwDMMwDENRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEUQBEEQBEEQBMEw' +
    'DMMwDMMwDMMwDENRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEUQBEEQBEEQBMEwDMMwDMMwDMMwDENRFEVRFEVRFEVRFEVR' +
    'FEVRFEVRFEVRFEVRFEUQBEEQBEEQBMEwDMMwDMMwDMMwDENRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEUQBEEQBEEQBMEw' +
    'DMMwDMMwDMMwDENRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEVRFEUQ/wRBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEIUEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAE' +
    'QRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAFACH5' +
    'BAAoAAAALAAAAABAABgAAAL/BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ/wRBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEATBMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzD' +
    'MAzDMAzDMAzDMAzDMAxDEARBEARBEATBMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMAxDEARBEARBEATB' +
    'MAzDMAzDMAzDMAzDMAzDMAzDMAzDMAzDMP8MwzAMwzAMwzAMwzAMQxAEQRAEQRAEwTAMwzAMwzAMwzAMwzAMwzAMwzAMwzAM' +
    'wzAMwzAMwzAMwzAMwzAMQxAEQRAEQRAEwTAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMQxAEQRAEQRAE' +
    'wTAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMQxAEQRAEQRAEwTAMwzAMwzAMwzAMwzAMwzAMwzAMwzAM' +
    'wzAMwzAMwzAMwzAMwzAMQxAEQRAEQRAEwTAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMwzAMQxD/BEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQ' +
    'BEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQBEEQhQRBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARBEARB' +
    'EARBEARBEARBEARBEARBEARBEARBEAUAOw==';
  const benchmarkMedia = { png: benchmarkPng, gif: benchmarkGif };
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
    { id: 4, name: 'Kimi 示例分组', status: 'active', subscription_type: 'standard', rate_multiplier: 1 },
  ];
  const galleryStart = Date.now();
  let gallerySequence = 5000;
  let comparisonSequence = 9000;
  const sharedGalleryPrompt = '创作一幅白鹭与自行车经过河畔的原创场景。可用完整 HTML/CSS 或 SVG，体现白鹭、自行车、河岸与微风。只输出作品源码，不使用脚本、外部资源或字体。';
  // Original offline artwork, made only from HTML/CSS and vector primitives.
  function cyclingScene(variant = 0) {
    const skies = ['#dff5ee', '#f8e8d2', '#dfeafa'];
    return `<style>
      *{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}body{font-family:system-ui,sans-serif;background:${skies[variant % 3]}}main{width:100%;height:100%;position:relative}svg{display:block;width:100%;height:100%;object-fit:cover}.caption{position:absolute;top:7%;left:6%;color:#164e47}.caption small{font-size:10px;letter-spacing:.18em}.caption h1{font-size:clamp(16px,4vw,32px);font-weight:600;margin:6px 0}.cloud{animation:drift 12s ease-in-out infinite alternate}.bird{animation:bob 3s ease-in-out infinite alternate;transform-origin:490px 260px}@keyframes drift{to{transform:translateX(26px)}}@keyframes bob{to{transform:translateY(-3px)}}@media(prefers-reduced-motion:reduce){.cloud,.bird{animation:none}}
      </style><main><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 420" role="img" aria-label="原创场景：白鹭与单车经过河畔">
      <rect width="720" height="420" fill="${skies[variant % 3]}"/><circle cx="591" cy="79" r="38" fill="#f8cf80"/><g class="cloud" fill="#fff" opacity=".75"><ellipse cx="390" cy="103" rx="49" ry="14"/><ellipse cx="365" cy="93" rx="21" ry="19"/><ellipse cx="397" cy="88" rx="27" ry="23"/></g>
      <path d="M0 244Q160 118 346 242T720 209V420H0Z" fill="#badfd1"/><path d="M0 301Q180 237 361 283T720 280V420H0Z" fill="#85bcb0"/><path d="M0 351Q270 287 720 340V420H0Z" fill="#d5a97c"/><path d="M0 374Q270 312 720 363" stroke="#f5e0b8" stroke-width="5" stroke-dasharray="23 17" fill="none"/>
      <g fill="none" stroke="#235f66" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"><circle cx="255" cy="296" r="51"/><circle cx="413" cy="296" r="51"/><path d="M255 296L301 211 349 296H255L328 237H390L413 296M349 296L379 205H399M301 211L289 199H272"/><path d="M370 205H397" stroke-width="10"/></g><circle cx="349" cy="296" r="9" fill="#e7794c"/><path d="M349 296L371 308" stroke="#e7794c" stroke-width="5" stroke-linecap="round"/>
      <g class="bird"><ellipse cx="493" cy="260" rx="37" ry="25" fill="#fffdf5"/><path d="M515 251Q541 230 531 210Q524 187 538 177" fill="none" stroke="#fffdf5" stroke-width="19" stroke-linecap="round"/><circle cx="540" cy="174" r="14" fill="#fffdf5"/><path d="M552 173L577 179 552 184Z" fill="#e89f43"/><circle cx="544" cy="170" r="2.7" fill="#24464b"/><path d="M476 258Q489 242 506 262" fill="none" stroke="#dfebde" stroke-width="4"/><path d="M481 281L478 313M499 283L506 313M467 315H483M499 315H516" fill="none" stroke="#bc8343" stroke-width="4" stroke-linecap="round"/></g>
      <g stroke="#437d67" stroke-width="3" stroke-linecap="round"><path d="M101 332V279M101 303L84 288M101 315L119 297M629 327V284M629 309L612 296"/></g></svg><div class="caption"><small>ORIGINAL SCENE · HTML / CSS</small><h1>沿着风，慢慢骑。</h1></div></main>`;
  }
  function posterScene(variant = 0) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 540" role="img" aria-label="原创示例：白鹭与自行车的几何海报"><rect width="720" height="540" fill="${['#f5e8cb', '#f2dfd1', '#e5e7cc'][variant % 3]}"/><circle cx="528" cy="157" r="109" fill="#cc6849"/><path d="M0 368L224 225 402 368 720 281V540H0Z" fill="#90a995"/><path d="M0 439Q270 357 720 428V540H0Z" fill="#2e6669"/><g fill="none" stroke="#173c47" stroke-width="8" stroke-linejoin="round"><circle cx="247" cy="388" r="57"/><circle cx="430" cy="388" r="57"/><path d="M247 388L297 287 355 388H247L328 315H407L430 388M355 388L389 277H413M297 287L282 272H260"/></g><g fill="#fff9e8"><ellipse cx="490" cy="294" rx="43" ry="29"/><path d="M513 290Q554 260 539 224Q532 201 549 188L558 201Q548 215 558 235Q569 267 534 307Z"/><circle cx="552" cy="191" r="16"/></g><path d="M567 190L598 198 567 204Z" fill="#eab25c"/><circle cx="556" cy="186" r="3" fill="#173c47"/><path d="M473 319L467 353M497 321L505 353" stroke="#d1ad6d" stroke-width="5"/><g fill="#173c47" font-family="system-ui,sans-serif"><text x="40" y="52" font-size="12" letter-spacing="4">ORIGINAL POSTER · SVG</text><text x="40" y="101" font-size="38" font-weight="600">风从河岸来。</text></g></svg>`;
  }
  function blueprintScene(variant = 0) {
    const vector = cyclingScene(variant).match(/<svg[\s\S]*?<\/svg>/)[0];
    return `<style>html,body{margin:0;height:100%;overflow:hidden}body{background:#102d48;color:#a8e6dd;font-family:system-ui,sans-serif}main{height:100%;position:relative;background-image:linear-gradient(#8fe5e312 1px,transparent 1px),linear-gradient(90deg,#8fe5e312 1px,transparent 1px);background-size:24px 24px}svg{width:100%;height:100%;display:block}svg [fill]:not([fill="none"]){fill:none;stroke:#a8e6dd;stroke-width:1.5}svg [stroke]{stroke:#a8e6dd}.label{position:absolute;top:8%;left:6%;letter-spacing:.12em;font-size:10px}.label strong{display:block;font-size:22px;margin-top:7px;letter-spacing:.04em}.note{position:absolute;bottom:6%;right:6%;font-size:9px;letter-spacing:.1em}</style><main>${vector}<div class="label">ORIGINAL LINE STUDY · HTML / CSS<strong>河畔骑行 · 线稿</strong></div><div class="note">WHITE EGRET / BICYCLE / RIVER</div></main>`;
  }
  function minimalScene(variant = 0) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 540" role="img" aria-label="原创示例：白鹭单车极简纸片场景"><rect width="720" height="540" fill="${['#eee7dc', '#e6e8df', '#f0e1d8'][variant % 3]}"/><path d="M0 349Q220 263 387 351T720 338V540H0Z" fill="#a7bdad"/><path d="M0 439Q240 373 720 420V540H0Z" fill="#dac19b"/><circle cx="541" cy="153" r="44" fill="#d4a16c"/><g fill="none" stroke="#4e665d" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"><circle cx="250" cy="370" r="53"/><circle cx="419" cy="370" r="53"/><path d="M250 370L296 279 351 370H250L320 307H393L419 370M351 370L381 267H401M296 279L282 267H262"/></g><ellipse cx="482" cy="299" rx="35" ry="23" fill="#fbf7ed"/><path d="M503 295Q534 271 524 243Q518 224 532 214" fill="none" stroke="#fbf7ed" stroke-width="17" stroke-linecap="round"/><circle cx="535" cy="211" r="13" fill="#fbf7ed"/><path d="M546 210L570 216 546 221Z" fill="#bf8c58"/><circle cx="539" cy="207" r="2.5" fill="#4e665d"/><path d="M471 320L465 346M490 321L497 346" stroke="#bf8c58" stroke-width="4"/><path d="M77 205H154M97 221H132M573 351H633" stroke="#6e8b7c" stroke-width="3"/><g fill="#4e665d" font-family="system-ui,sans-serif"><text x="43" y="53" font-size="11" letter-spacing="3">ORIGINAL PAPER SCENE · SVG</text><text x="43" y="95" font-size="32">轻装上路。</text></g></svg>`;
  }
  function comparisonScene(groupId, variant = 0) {
    return groupId === 1 ? cyclingScene(variant) : groupId === 2 ? posterScene(variant) : groupId === 3 ? blueprintScene(variant) : minimalScene(variant);
  }
  const galleryGroups = [
    ...['OpenAI', 'Anthropic', 'GLM', 'Kimi'].map((name, index) => ({ group_id: index + 1, name: `${name} · 示例组`, model: `${name.toLowerCase()}-fixture`, key_id: index + 11, api_format: index === 0 ? 'responses' : 'chat', enabled: false, interval_minutes: 60, next_at: null, running: false, last_error: null, prompt: sharedGalleryPrompt, comparison_member: true })),
  ];
  const galleryWorks = new Map(galleryGroups.map(group => [group.group_id, Array.from({ length: 4 }, (_, index) => ({
    id: group.group_id * 100 + 4 - index, group_id: group.group_id, round_id: index === 0 ? comparisonSequence : null, title: `${['河畔慢骑', '几何微风', '骑行线稿', '轻装上路'][group.group_id - 1]}${index ? ` · 历史 ${index}` : ''}`, model: group.model,
    created_at: new Date(galleryStart - (index * group.interval_minutes + 12) * 60000).toISOString(), finished_at: new Date(galleryStart - (index * group.interval_minutes + 12) * 60000 + 2200).toISOString(), latency_ms: 1280 + group.group_id * 173 + index * 317, status: 'success', error: null, kind: 'html', content: comparisonScene(group.group_id, index), mime: 'text/html', prompt: sharedGalleryPrompt,
  }))]));
  const galleryStats = new Map(galleryGroups.map(group => [group.group_id, { success_24h: 12 + group.group_id, failed_24h: 0, total: 40 + group.group_id * 8 }]));
  let comparisonConfig = { group_ids: [1, 2, 3, 4], prompt: sharedGalleryPrompt, enabled: true, interval_minutes: 60, next_at: new Date(galleryStart + 42 * 60000).toISOString(), running: false };
  let comparisonRound = savedComparisonRound(comparisonSequence);
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
  function benchmarkSample(model = 'demo-model', includeUnscored = false) {
    // These are fixed examples, not answers from an external model.
    const answers = [
      ['计算 17 × 23。只输出整数答案。', '391', '391'],
      ['字符串 SUB2API 中有多少个字母（数字不计）？只输出整数。', '6', '6'],
      ['所有蓝球都是圆的。某个物体是蓝球。该物体是否一定是圆的？只输出 YES 或 NO。', 'YES', 'YES'],
      ['将列表 [9, 2, 11, -1] 从小到大排序，只输出 JSON 数组，不加解释。', '[-1,2,9,11]', '[-1,2,9,11]'],
      ['有 3 个盒子，每盒 4 个球，拿走 5 个球，还剩多少？只输出整数。', '7', '8'],
    ];
    const data = { model, score: 4, total: 5, completed: 5, results: answers.map(([question, expected, answer], index) => ({
      question, expected, answer, passed: index < 4, scored: true, latency_ms: 180 + index * 37,
      media: index === 0 ? [{ src: benchmarkMedia.png, mime: 'image/png', alt: '离线 PNG 示例：结果卡片' }]
        : index === 4 ? [{ src: benchmarkMedia.gif, mime: 'image/gif', alt: '离线 GIF 示例：进度动图' }] : [],
    })) };
    if (includeUnscored) Object.assign(data.results[4], { question: '请用一张动图展示当前进度。', expected: '', answer: '', passed: false, scored: false });
    return data;
  }
  function galleryDetail(groupId) {
    const group = galleryGroups.find(item => item.group_id === groupId);
    if (!group) return bad('示例分组不存在。', 404);
    const works = galleryWorks.get(groupId).map(workPreview);
    return { group: galleryGroup(group), stats: clone(galleryStats.get(groupId)), works };
  }
  function workPreview({ content, prompt, ...work }) {
    return { ...clone(work), preview: content };
  }
  function galleryGroup(group) {
    const member = comparisonConfig?.group_ids.includes(group.group_id) || false;
    return { ...clone(group), comparison_member: member, comparison_enabled: comparisonConfig?.enabled || false,
      comparison_next_at: comparisonConfig?.next_at || null, next_at: member ? null : group.next_at };
  }
  function savedComparisonRound(roundId) {
    const items = comparisonConfig.group_ids.map(groupId => {
      const group = galleryGroups.find(item => item.group_id === groupId), work = galleryWorks.get(groupId)[0];
      return { group_id: groupId, name: group.name, model: work.model, status: work.status, work: workPreview(work) };
    });
    return { id: roundId, prompt: comparisonConfig.prompt, status: 'success', created_at: items[0].work.created_at,
      finished_at: items.map(item => item.work.finished_at).sort().at(-1), items };
  }
  function comparisonDetail() {
    return { config: clone(comparisonConfig), round: clone(comparisonRound), timezone: 'Asia/Shanghai' };
  }
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
    const shared = path.match(/^\/api\/gallery\/comparison(?:\/(run|pause))?$/);
    if (shared) {
      if (!shared[1] && method === 'GET') return comparisonDetail();
      if (!shared[1] && method === 'DELETE') { comparisonConfig = null; return comparisonDetail(); }
      if (!shared[1] && method === 'PUT') {
        if (!Array.isArray(data.group_ids) || data.group_ids.length < 2 || data.group_ids.length > 32 || new Set(data.group_ids).size !== data.group_ids.length || data.group_ids.some(groupId => !galleryGroups.some(group => group.group_id === groupId))) return bad('请选择至少两个不同的已配置分组。');
        if (!data.prompt?.trim() || data.prompt.length > 8000 || !Number.isInteger(data.interval_minutes) || data.interval_minutes < 5 || data.interval_minutes > 1440 || typeof data.enabled !== 'boolean') return bad('请填写共享提示词，并设置有效的生成间隔。');
        comparisonConfig = { ...clone(data), running: false, next_at: data.enabled ? new Date(Date.now() + data.interval_minutes * 60000).toISOString() : null };
        return comparisonDetail();
      }
      if (shared[1] === 'pause' && method === 'POST') {
        if (!comparisonConfig) return comparisonDetail();
        comparisonConfig.enabled = false; comparisonConfig.next_at = null;
        return comparisonDetail();
      }
      if (shared[1] === 'run' && method === 'POST') {
        if (!comparisonConfig) return bad('请先保存共享同题设置。', 409);
        const roundId = ++comparisonSequence, created = new Date().toISOString();
        for (const groupId of comparisonConfig.group_ids) {
          const group = galleryGroups.find(item => item.group_id === groupId), stats = galleryStats.get(groupId), latency = 1200 + groupId * 193;
          galleryWorks.get(groupId).unshift({ id: ++gallerySequence, group_id: groupId, round_id: roundId, title: `同题示例 · ${group.name}`, model: group.model, created_at: created,
            finished_at: new Date(Date.parse(created) + latency).toISOString(), latency_ms: latency, status: 'success', error: null, kind: 'html', content: comparisonScene(groupId, 0), mime: 'text/html', prompt: comparisonConfig.prompt });
          stats.success_24h++; stats.total++;
        }
        comparisonRound = savedComparisonRound(roundId);
        comparisonConfig.next_at = comparisonConfig.enabled ? new Date(Date.now() + comparisonConfig.interval_minutes * 60000).toISOString() : null;
        return { queued: true, already_running: false, round_id: roundId, demo: true };
      }
    }
    if (path === '/api/gallery' && method === 'GET') return { groups: galleryGroups.map(galleryGroup), timezone: 'Asia/Shanghai' };
    let gallery = path.match(/^\/api\/gallery\/(\d+)(?:\/(run|pause)|\/works\/(\d+))?$/);
    if (gallery) {
      const groupId = Number(gallery[1]), group = galleryGroups.find(item => item.group_id === groupId);
      if (!group) return bad('示例分组不存在。', 404);
      if (gallery[3] && method === 'GET') {
        const work = galleryWorks.get(groupId).find(item => item.id === Number(gallery[3]));
        return work ? { ...clone(work), group_name: group.name } : bad('示例作品不存在。', 404);
      }
      if (gallery[2] === 'pause' && method === 'POST') { group.enabled = false; group.next_at = null; return galleryDetail(groupId); }
      if (gallery[2] === 'run' && method === 'POST') {
        if (comparisonConfig?.group_ids.includes(groupId)) return bad('此分组由共享同题计划管理，请从共享计划生成一轮。', 409);
        const works = galleryWorks.get(groupId), stats = galleryStats.get(groupId);
        works.unshift({ ...clone(works[0]), id: ++gallerySequence, title: '本页生成 · 原创场景示例', created_at: new Date().toISOString(), model: group.model, prompt: group.prompt });
        stats.success_24h++; stats.total++;
        group.next_at = group.enabled ? new Date(Date.now() + group.interval_minutes * 60000).toISOString() : null;
        return { queued: true, already_running: false, group_id: groupId, demo: true };
      }
      if (!gallery[2] && !gallery[3] && method === 'PUT') {
        if (!data.model?.trim() || !data.prompt?.trim() || !Number.isInteger(data.interval_minutes) || data.interval_minutes < 5 || data.interval_minutes > 1440) return bad('请填写示例模型、提示词，并将间隔设置为 5～1440 的整数分钟。');
        Object.assign(group, clone(data), { group_id: groupId, running: false });
        group.next_at = group.enabled ? new Date(Date.now() + group.interval_minutes * 60000).toISOString() : null;
        return galleryDetail(groupId);
      }
      if (!gallery[2] && !gallery[3] && method === 'GET') return galleryDetail(groupId);
    }
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
    if (path === '/api/benchmark/options') return { keys: galleryGroups.map(group => ({ id: group.key_id, name: `${group.name} Key（不存在真实密钥）`, group_id: group.group_id, group_name: group.name })) };
    if (path === '/api/benchmark' && method === 'POST') {
      log('benchmark.demo', data.model, '示例检测结果，无模型调用或费用。');
      return benchmarkSample(data.model);
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
  if (galleryDemo) {
    const controller = window.helperGallery(document.getElementById('demo-gallery-main'), {
      timezone: 'Asia/Shanghai',
      request: async path => {
        const result = await fetch('/api' + path, { credentials: 'omit' });
        const data = await result.json();
        if (!result.ok) throw new Error(data.detail || '离线示例暂时无法打开。');
        return data;
      },
    });
    window.addEventListener('pagehide', () => controller.destroy(), { once: true });
  } else if (resultDemo) {
    const result = window.helperBenchmarkResults(benchmarkSample('demo-model', true), {
      groupName: '离线示例分组',
    });
    document.getElementById('demo-result-main').append(result);
  } else if (nativeDemo) {
    // Return a fixed fixture JWT to the real extension without reading or
    // writing any browser credential. This adapter is never served by Helper.
    const getItem = Storage.prototype.getItem;
    Storage.prototype.getItem = function (key) { return key === 'auth_token' ? 'demo-native-jwt' : getItem.call(this, key); };
    const selected = /\/admin\/users\/?$/.test(location.pathname) ? 'users' : 'accounts';
    document.getElementById('demo-benchmark-link').href = new URL('gallery.html', assetRoot).href;
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

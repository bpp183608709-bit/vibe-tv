// 用真实页面出图：不是界面稿，是把 server/web/ 跑起来、喂一份假数据、按 390×844 截屏。
//
// 为什么需要它：界面稿（wrongbook-h5.html）验的是样式，验不了 app.js 里的渲染函数、事件绑定和
// 接口字段名。P2 的 5 个页面是同一份 app.js 拼出来的，语法通过不代表点得动。
//
// 用法：
//   python3 -m http.server 8811 --directory server/web &
//   node server/tools/preview_h5.mjs
// 产物：docs/mockups/live-<屏名>.png（780×1688，即 390×844 @2x）

import { spawn } from 'node:child_process';
import { writeFileSync, rmSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, '..', '..', 'docs', 'mockups');
const PAGE = process.env.PREVIEW_URL || 'http://127.0.0.1:8811/';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
// 每次跑用独立的 profile：上一次异常退出会留下 Chrome 和锁，共用一个目录时下一次直接卡住。
const PROFILE = join(HERE, `.chrome-preview-${process.pid}`);

// ---------------------------------------------------------------- 假数据
// 形状照抄 server 的真实响应；改接口就该改这里，否则截出来的图看不出契约变了。

const topic = (id, name, unit, ordinal, score, subject, code, bank = 10) => ({
  topic_id: id, topic: name, subject, subject_code: code, textbook_id: code === 'math' ? 1 : 2,
  unit, unit_ordinal: ordinal, score, level: score >= 71 ? 3 : score >= 36 ? 2 : score >= 12 ? 1 : 0,
  level_name: score >= 71 ? 'L3' : 'L1', level_word: '', need_right: score < 12 ? 0 : Math.ceil((score - 11) / 10),
  weight: score >= 71 ? 4 : 1, target_qty: 10, daily_cap: 3, in_bank: bank,
  total_wrong: 3, total_right: 1, last_wrong_on: '2026-11-15', paused_until: null,
});

const WEAK = [
  topic(11, '一般现在时第三人称单数', 'Unit 2 Ways to go to school', 2, 86, '英语', 'english', 8),
  topic(4, '分数乘法应用题', '第 1 单元 分数乘法', 1, 72, '数学', 'math', 10),
  topic(6, '一个数除以分数', '第 3 单元 分数除法', 3, 65, '数学', 'math', 9),
  topic(17, '圆的面积', '第 5 单元 圆', 5, 44, '数学', 'math', 10),
  topic(28, '说明文阅读', '第 4 单元', 4, 20, '语文', 'chinese', 3),
];

const Q = (id, stem, yours, right, subject, name, source, day) => ({
  source, question_id: source === 'tv' ? id : null, item_id: source === 'paper' ? `W${id}` : undefined,
  day_key: day, stem, qtype: 'choice', subject, unit: '第 1 单元', topic: name, topic_id: 4,
  chosen: 'B', chosen_text: yours, answer_text: right, explanation: null,
  flagged_unknown: false, mastery_score: 72,
});

const WRONG = [
  Q(901, '一桶油重 5 千克，用去了 2/5，用去了多少千克？', '2 千克', '2 千克', '数学', '分数乘法应用题', 'tv', '2026-11-15'),
  Q(902, '一根绳子长 3/4 米，它的 2/3 是多少米？', '1/4 米', '1/2 米', '数学', '分数乘法应用题', 'tv', '2026-11-15'),
  Q(903, '小明的体重是爸爸的 3/5，爸爸 70 千克，小明多重？', '45 千克', '42 千克', '数学', '分数乘法应用题', 'tv', '2026-11-14'),
  Q(904, '计算 7/8 × 4/21', '28/168', '1/6', '数学', '分数乘法应用题', 'tv', '2026-11-14'),
  Q(905, '', null, null, '数学', '分数乘法应用题', 'paper', '2026-11-13'),
];

const FIXTURES = {
  '/v1/auth/me': {
    account: { id: 'A1', email: 'parent@example.com', has_question: true, question: '妈妈的生日是哪天？' },
    devices: [{ device_id: 'D1', name: '客厅的电视' }],
    legacy: false,
  },
  '/v1/h5/summary': {
    today: {
      day_key: '2026-11-16', budget_ms: 3600000, unlimited: false, used_ms: 1920000,
      used_at: '2026-11-16 19:40:00', passed: 2, awarded_ms: 1800000, attempts: 3,
      attempts_left: 3, success_left: 1, award_left_ms: 3600000, cooldown_seconds: 0,
    },
    version: 3,
    device: { name: '客厅的电视', app_version: '1.4.2' },
  },
  '/v1/h5/config': {
    version: 3,
    config: { daily_minutes: 60, session_minutes: 30, interval_minutes: 20 },
    updated_at: '2026-11-14 09:12:00',
  },
  '/v1/h5/config': {
    version: 3,
    config: { daily_minutes: 60, session_minutes: 30, interval_minutes: 20 },
    updated_at: '2026-11-14 09:12:00',
  },
  '/v1/h5/mastery': {
    items: WEAK, count: WEAK.length, weak_floor: 12,
    mix: { state: 'S2', c: 3, r: 2, p: 0, n: 5, note: 'S2：5 个可练知识点，最深 L3' },
    week: { days: 7, arrived: 2, cleared: 1, avg_now: 57, avg_before: 61 },
    today: '2026-11-16',
    week_line: '现在有 5 个知识点在练。一周讲掉 1–2 个就够，不用全清。',
  },
  '/v1/h5/mastery/4': {
    ...WEAK[1],
    today_used: 2,
    events: [
      { kind: 'tv_wrong', label: '电视答错', delta: 12, score_after: 72, at: '2026-11-15 20:22:11' },
      { kind: 'tv_right', label: '电视答对', delta: -10, score_after: 60, at: '2026-11-15 20:14:03' },
      { kind: 'streak_bonus', label: '连对加成', delta: -10, score_after: 70, at: '2026-11-15 20:09:47' },
      { kind: 'paper_wrong', label: '试卷错题（当日首道）', delta: 20, score_after: 80, at: '2026-11-13 19:40:20' },
      { kind: 'parent_adjust', label: '周末给他讲过了', delta: -10, score_after: 60, at: '2026-11-12 21:02:00' },
    ],
    wrong_items: [
      { item_id: 'W1', day_key: '2026-11-15', source: 'tv', stem: '一桶油重 5 千克，用去了 2/5，用去了多少千克？' },
      { item_id: 'W2', day_key: '2026-11-13', source: 'paper', stem: '' },
    ],
    coach: {
      say: '这个知识点错了两三次，我让题多练练它；做对几次它就退回去了。再答对 7 次就够了。',
      why: '错了三四次，出题权重升到 3 倍，每天最多 3 道。',
      avoid: '别说「你怎么又错了」——说的是题，不是他。',
    },
  },
  '/v1/h5/wrongbook': {
    days: 30, items: WRONG, total: 23, offset: 0,
    counts: { all: 23, tv: 18, paper: 5 }, source: 'all',
  },
  '/v1/h5/topics': {
    topics: [
      ...WEAK,
      topic(5, '分数乘分数', '第 1 单元 分数乘法', 1, 0, '数学', 'math', 10),
      topic(9, '比的意义', '第 4 单元 比', 4, 0, '数学', 'math', 7),
      topic(22, '虚拟语气', 'Unit 3', 3, 0, '英语', 'english', 6),
      topic(30, '古诗默写', '第 7 单元', 7, 0, '语文', 'chinese', 4),
    ],
  },
  '/v1/h5/curriculum': {
    start_date: '2026-09-01', week: 11, today: '2026-11-16',
    books: [
      {
        textbook_id: 1, book: '人教版 六年级上册', subject: '数学', subject_code: 'math',
        current: 5, last: 8, reviewing: false, source: 'calendar',
        units: [
          { ordinal: 1, name: '分数乘法', week_start: 1 },
          { ordinal: 2, name: '位置与方向', week_start: 3 },
          { ordinal: 3, name: '分数除法', week_start: 5 },
          { ordinal: 4, name: '比', week_start: 8 },
          { ordinal: 5, name: '圆', week_start: 10 },
          { ordinal: 6, name: '百分数', week_start: 13 },
          { ordinal: 7, name: '扇形统计图', week_start: 16 },
          { ordinal: 8, name: '数学广角', week_start: 18 },
        ],
      },
      {
        textbook_id: 2, book: '人教版 六年级上册', subject: '英语', subject_code: 'english',
        current: 8, last: 8, reviewing: true, source: 'parent',
        units: [
          { ordinal: 1, name: 'How can I get there?', week_start: 1 },
          { ordinal: 2, name: 'Ways to go to school', week_start: 3 },
          { ordinal: 8, name: 'Recycle 2', week_start: 15 },
        ],
      },
    ],
  },
};

const match = url => {
  const path = url.split('?')[0];
  if (FIXTURES[path]) return FIXTURES[path];
  if (/^\/v1\/h5\/mastery\/\d+$/.test(path)) return FIXTURES['/v1/h5/mastery/4'];
  return null;
};

// ---------------------------------------------------------------- DevTools
rmSync(PROFILE, { recursive: true, force: true });
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${PROFILE}`, 'about:blank',
], { stdio: 'ignore' });

const sleepMs = ms => sleep(ms);
const withTimeout = (p, label, ms = 10000) => Promise.race([
  p, sleepMs(ms).then(() => { throw new Error(`${label} 超时 ${ms}ms`); }),
]);

// DevToolsActivePort 只有端口；browser 的 ws 路径带一个 id，得问 /json/version 要。
async function devtoolsPort() {
  const fs = await import('node:fs/promises');
  for (let i = 0; i < 80; i += 1) {
    try {
      const [line] = (await fs.readFile(join(PROFILE, 'DevToolsActivePort'), 'utf8')).split('\n');
      const port = Number(line.trim());
      if (port) return port;
    } catch { /* 还没写出来 */ }
    await sleepMs(120);
  }
  throw new Error('Chrome 没有起来');
}

const port = await withTimeout(devtoolsPort(), 'DevToolsActivePort');
const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
const ws = new WebSocket(webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true });
  ws.addEventListener('error', reject, { once: true });
});

let seq = 0;
const pending = new Map();
ws.onmessage = event => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
  }
};
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
await send('Page.enable', {}, sessionId);
await send('Runtime.enable', {}, sessionId);
await send('Emulation.setDeviceMetricsOverride',
  { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);

// 在 app.js 之前注入：拦掉 fetch，灌一份 localStorage，让页面以为已经登录过。
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `
    localStorage.setItem('aq.device', 'D1');
    localStorage.setItem('aq.lang', 'zh');
    const FIXTURES = ${JSON.stringify(FIXTURES)};
    const match = ${match.toString()};
    window.fetch = async (url, init = {}) => {
      const path = String(url).split('?')[0];
      const body = match(String(url)) || {};
      window.__calls = window.__calls || [];
      window.__calls.push((init.method || 'GET') + ' ' + String(url));
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    };
  `,
}, sessionId);

const shot = async name => {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(join(OUT, `live-${name}.png`), Buffer.from(data, 'base64'));
  console.log('shot', name);
};

const evaluate = async expression => {
  const { result, exceptionDetails } = await send('Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (exceptionDetails) throw new Error(JSON.stringify(exceptionDetails));
  return result.value;
};

const settle = async (label) => {
  // 等渲染函数把 innerHTML 写完：先等骨架消失，再等一帧。
  for (let i = 0; i < 60; i += 1) {
    const ready = await evaluate(`!document.querySelector('#view .skeleton') && document.querySelector('#view').children.length > 0`);
    if (ready) break;
    await sleepMs(100);
  }
  if (label) {
    const errors = await evaluate(`window.__errors || []`);
    if (errors.length) throw new Error(`${label}: ${JSON.stringify(errors)}`);
  }
  await sleepMs(350);
};

await send('Page.navigate', { url: PAGE }, sessionId);
await sleepMs(1200);
await evaluate(`window.__errors = []; window.onerror = (m) => { window.__errors.push(String(m)); };`);
await settle('boot');

const click = async selector => {
  const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false; el.click(); return true; })()`);
  if (!ok) throw new Error(`找不到 ${selector}`);
  await sleepMs(450);
};

// 逐屏走一遍，走的是真实点击路径，不是直接调渲染函数。
await shot('01-today');
await click('.tab[data-tab="wrong"]');
await settle('wrong');
await shot('02-weak-overview');
await click('#view [data-go="topics"]');
await settle('topics');
await shot('03-weak-list');
await click('#view [data-topic]');
await settle('topic');
await shot('04-topic-detail');
await click('#back');
await settle('back');
await click('#view [data-go="list"]');
await settle('list');
await shot('05-wrong-list');
await click('.tab[data-tab="wrong"]');
await settle('wrong again');
await click('#add-open');
await settle('add sheet');
// 选一个知识点再截：空选状态下保存按钮是禁用的，那张图说明不了这个弹层怎么用。
await click('#add-body [data-pick]');
await shot('06-add-sheet');
// 走一遍保存：POST 之后弹层关掉、总览重绘、toast 出现。
await click('#add-save');
await settle('saved');
await shot('06b-add-saved');
await click('.tab[data-tab="settings"]');
await settle('settings');
await shot('07-settings');
// 换密保：先答旧问题再填新的，两个输入框和两条规则必须在同一屏里说清楚。
await click('#setq');
await settle('set question');
await shot('07b-question-replace');
// 问题够长、答案只有一个字：这时提交，得到的是「答案至少 2 个字」而不是一句和长度无关的错。
const type = async (selector, text) => evaluate(`(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  el.value = ${JSON.stringify(text)};
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return el.value; })()`);
await type('#auth-old-answer', '三月九日');
await type('#auth-question-input', '第一只狗叫什么');
await type('#auth-answer', '小');
await click('#auth-submit');
await shot('07c-question-too-short');
// 这一屏没有「取消」，只能靠浏览器的返回离开；脚本里直接回到主屏，否则下面两张会拍到同一张表单。
await evaluate('showMain(); "ok"');
await sleepMs(300);
await click('#progress');
await settle('progress');
await shot('08-progress');

// 深色模式再走一遍总览，样式变量是同一套，值得看一眼。
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] }, sessionId);
await click('.tab[data-tab="wrong"]');
await settle('dark');
await shot('09-weak-dark');

const calls = await evaluate('window.__calls || []');
console.log('requests:', calls.length);
const leftover = await evaluate('window.__errors || []');
if (leftover.length) console.log('page errors:', leftover);

ws.close();
chrome.kill();
rmSync(PROFILE, { recursive: true, force: true });
process.exit(0);

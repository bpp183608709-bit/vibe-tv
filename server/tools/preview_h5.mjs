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

// `score` 现在是掌握度（加权正确率 ×100，越高越好），level 由它推回来：<40 未掌握，<60 基础，
// <80 熟练，其余拓展。薄弱列表只收 level ≤1 的节点，所以这里的分数都很低。
const topic = (id, name, unit, ordinal, score, subject, code, bank = 10, node = '') => {
  const level = score < 40 ? 0 : score < 60 ? 1 : score < 80 ? 2 : 3;
  return {
    topic_id: id, topic: name, node_code: node, knowledge_type: 'procedural',
    summary: '', subject, subject_code: code, textbook_id: code === 'math' ? 1 : 2,
    unit, unit_ordinal: ordinal, score, level, level_name: `L${level}`, level_word: '',
    level_label: ['0-未掌握', '1-基础', '2-熟练', '3-拓展'][level],
    weighted_accuracy: score / 100, sample_count: 7, hard_right: 0, bias: 0, need_sample: 0,
    need_right: level >= 2 ? 0 : Math.max(1, Math.ceil((60 - score) / 12)),
    weight: [4, 2, 1, 1][level], target_qty: [10, 7, 5, 3][level], daily_cap: 3, in_bank: bank,
    total_wrong: 3, total_right: 1, last_wrong_on: '2026-11-15', paused_until: null,
  };
};

const WEAK = [
  topic(4, '分数乘整数', '分数乘法', 1, 18, '数学', 'math', 3, 'math-6up-d01'),
  topic(6, '一个数除以分数', '分数除法', 3, 32, '数学', 'math', 3, 'math-6up-d08'),
  topic(17, '圆的面积', '圆', 5, 35, '数学', 'math', 3, 'math-6up-d15'),
  topic(11, '一般现在时第三人称单数', 'Daily plan', 2, 47, '英语', 'english', 3, 'english-6up-d03'),
  topic(28, '小说三要素', '小说阅读', 4, 52, '语文', 'chinese', 3, 'chinese-6up-d06'),
];

const Q = (id, stem, yours, right, subject, name, source, day) => ({
  source, question_id: source === 'tv' ? id : null, item_id: source === 'paper' ? `W${id}` : undefined,
  day_key: day, stem, qtype: 'choice', subject, unit: '分数乘法', topic: name, topic_id: 4,
  chosen: 'B', chosen_text: yours, answer_text: right, explanation: null,
  flagged_unknown: false, mastery_score: 18,
});

const WRONG = [
  Q(901, '一桶油重 5 千克，用去了 2/5，用去了多少千克？', '2/5 千克', '2 千克', '数学', '分数乘法应用题', 'tv', '2026-11-15'),
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
    items: WEAK, count: WEAK.length, weak_max_level: 1,
    mix: { state: 'S3', c: 2, r: 2, p: 1, n: 5, deepest: 3,
           note: 'S3：5 个可练知识点，最深 未掌握' },
    week: { days: 7, arrived: 2, cleared: 1, avg_now: 37, avg_before: 33 },
    today: '2026-11-16',
    week_line: '现在有 5 个知识点在练。一周讲掉 1–2 个就够，不用全清。',
  },
  // 两级的报告：章是行，诊断节点是行里的线。第三章故意 diagnostic_count 3 而只有 1 条线，
  // 说明「没测过的节点不进列表，但仍算在章的节点数里」。
  '/v1/h5/mastery/report': {
    student_id: 'D1', period: '最近 30 天',
    aggregate_summary: [
      {
        aggregate_node_id: 'math-6up-u01', aggregate_name: '分数乘法',
        diagnostic_count: 2, weak_diagnostic_count: 1,
        diagnostics: [{
          diagnostic_node_id: 'math-6up-d01', diagnostic_name: '分数乘整数',
          mastery_level: '0-未掌握', weighted_accuracy: 0.18, sample_count: 7,
          ability_breakdown_note: '其中「辨析变式」这条能力线最弱（加权正确率 0.05），建议优先关注。',
          evidence_question_ids: ['1', '2', '3'],
          suggestion: '先别急着讲新内容：把这个节点的例题重做一遍，再让他自己说一遍是怎么想的。连着答对两三道再往前走。',
        }],
      },
      {
        aggregate_node_id: 'math-6up-u03', aggregate_name: '分数除法',
        diagnostic_count: 3, weak_diagnostic_count: 1,
        diagnostics: [{
          diagnostic_node_id: 'math-6up-d08', diagnostic_name: '一个数除以分数',
          mastery_level: '1-基础', weighted_accuracy: 0.32, sample_count: 6,
          ability_breakdown_note: null, evidence_question_ids: ['4', '5'],
          suggestion: '标准题基本能做了，变式还不稳。先练「换个说法还认不认得」，再回到应用题。',
        }],
      },
      {
        aggregate_node_id: 'math-6up-u05', aggregate_name: '圆',
        diagnostic_count: 3, weak_diagnostic_count: 1,
        diagnostics: [{
          diagnostic_node_id: 'math-6up-d15', diagnostic_name: '圆的面积',
          mastery_level: '1-基础', weighted_accuracy: 0.35, sample_count: 5,
          ability_breakdown_note: null, evidence_question_ids: ['6'],
          suggestion: '标准题基本能做了，变式还不稳。先练「换个说法还认不认得」，再回到应用题。',
        }],
      },
      {
        aggregate_node_id: 'english-6up-u02', aggregate_name: 'Daily plan',
        diagnostic_count: 2, weak_diagnostic_count: 1,
        diagnostics: [{
          diagnostic_node_id: 'english-6up-d03', diagnostic_name: '一般现在时第三人称单数',
          mastery_level: '1-基础', weighted_accuracy: 0.47, sample_count: 8,
          ability_breakdown_note: null, evidence_question_ids: ['7', '8'],
          suggestion: '标准题基本能做了，变式还不稳。先练「换个说法还认不认得」，再回到应用题。',
        }],
      },
      {
        aggregate_node_id: 'chinese-6up-u04', aggregate_name: '小说阅读',
        diagnostic_count: 2, weak_diagnostic_count: 1,
        diagnostics: [{
          diagnostic_node_id: 'chinese-6up-d06', diagnostic_name: '小说三要素',
          mastery_level: '1-基础', weighted_accuracy: 0.52, sample_count: 4,
          ability_breakdown_note: null, evidence_question_ids: ['9'],
          suggestion: '标准题基本能做了，变式还不稳。先练「换个说法还认不认得」，再回到应用题。',
        }],
      },
    ],
  },
  '/v1/h5/mastery/4': {
    ...WEAK[0],
    today_used: 2,
    events: [
      { kind: 'tv_wrong', label: '电视答错', delta: -17, score_after: 18, at: '2026-11-15 20:22:11' },
      { kind: 'tv_right', label: '电视答对', delta: 12, score_after: 35, at: '2026-11-15 20:14:03' },
      { kind: 'tv_wrong', label: '电视答错', delta: -13, score_after: 23, at: '2026-11-15 20:09:47' },
      { kind: 'paper_wrong', label: '试卷错题', delta: -9, score_after: 36, at: '2026-11-13 19:40:20' },
      { kind: 'parent_adjust', label: '周末给他讲过了', delta: 10, score_after: 45, at: '2026-11-12 21:02:00' },
    ],
    wrong_items: [
      { item_id: 'W1', day_key: '2026-11-15', source: 'tv', stem: '3/8 × 4 表示 4 个 3/8 相加，结果是（  ）。' },
      { item_id: 'W2', day_key: '2026-11-13', source: 'paper', stem: '' },
    ],
    coach: {
      say: '这个知识点这两次没做对，我让题多练练它；连着做对几道它就往上走了。再答对 4 次就能到基础。',
      why: '最近 7 次里只对了 3 次，加权正确率 18%，所以被排在前面。',
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
      // 没测过的节点：分数是 0 但标签是「待测」，不是「未掌握」——这也是列表页不上这些行的原因。
      { ...topic(5, '分数乘分数', '分数乘法', 1, 0, '数学', 'math', 3, 'math-6up-d02'),
        sample_count: 0, need_sample: 3, weighted_accuracy: 0, level_label: '待测' },
      { ...topic(9, '比的意义', '比', 4, 0, '数学', 'math', 3, 'math-6up-d12'),
        sample_count: 0, need_sample: 3, weighted_accuracy: 0, level_label: '待测' },
      { ...topic(22, 'should / shouldn\'t', 'We shouldn\'t waste water', 8, 0, '英语', 'english', 3,
        'english-6up-d12'), sample_count: 0, need_sample: 3, weighted_accuracy: 0, level_label: '待测' },
      { ...topic(30, '文言实词与文意', '艺术之美', 7, 0, '语文', 'chinese', 3, 'chinese-6up-d11'),
        sample_count: 0, need_sample: 3, weighted_accuracy: 0, level_label: '待测' },
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
// 上一次被 Ctrl-C 掉时会留下还活着的 Chrome，它继续往 profile 里写文件，同步删除就会
// ENOTEMPTY。这类残留只是临时目录，删不干净不该让截图失败。
try { rmSync(PROFILE, { recursive: true, force: true }); } catch { /* 有进程还在写 */ }
// `--no-sandbox` 不是可选项：这台机器上 Chrome 的沙箱初始化会失败，进程写完 DevToolsActivePort
// 就退出，脚本随后卡在握手阶段（没有报错，只是不动）。软件渲染同理——GPU 进程一挂整只浏览器就
// 跟着退出，截图出不来。
const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-software-rasterizer',
  '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
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
// 端口写出来了不代表浏览器还活着（沙箱失败就是先写端口再退出），所以握手也要有超时。
const { webSocketDebuggerUrl } = await withTimeout(
  fetch(`http://127.0.0.1:${port}/json/version`).then(response => response.json()),
  'DevTools 握手');
const ws = new WebSocket(webSocketDebuggerUrl);
await withTimeout(new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true });
  ws.addEventListener('error', reject, { once: true });
}), 'WebSocket 连接');

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
// kill() 只是发信号，Chrome 还在退出时仍会往 profile 写锁和缓存，同步删会 ENOTEMPTY。
// 截图已经落盘了，清理失败不该冒成错误。
await sleepMs(400);
try { rmSync(PROFILE, { recursive: true, force: true }); } catch { /* 退出中的 Chrome 还占着 */ }
process.exit(0);

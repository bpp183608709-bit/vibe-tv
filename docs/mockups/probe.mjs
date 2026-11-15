// 量一次布局：视口宽度、文档滚动宽度、以及超出视口的元素。
// 出图脚本用的是同一套 CDP 连接方式，这里只换成 Runtime.evaluate。
import { spawn } from 'node:child_process';
import { readFileSync, rmSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = 'file://' + join(HERE, 'wrongbook-h5.html');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PROFILE = join(HERE, '.chrome-profile-probe');
const screen = process.argv[2] || 'overview';

rmSync(PROFILE, { recursive: true, force: true });
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=0', `--user-data-dir=${PROFILE}`, 'about:blank',
], { stdio: 'ignore' });

const rpc = async (ws, method, params = {}, sessionId) => {
  const id = Math.floor(Math.random() * 1e9);
  const p = new Promise((res, rej) => {
    const onMsg = ev => {
      const m = JSON.parse(ev.data);
      if (m.id !== id) return;
      ws.removeEventListener('message', onMsg);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    };
    ws.addEventListener('message', onMsg);
  });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return p;
};

try {
  let port;
  for (let i = 0; i < 80; i++) {
    try {
      port = Number(readFileSync(join(PROFILE, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
      if (port) break;
    } catch {}
    await sleep(120);
  }
  const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const ws = new WebSocket(webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));

  const { targetId } = await rpc(ws, 'Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await rpc(ws, 'Target.attachToTarget', { targetId, flatten: true });
  await rpc(ws, 'Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
  await rpc(ws, 'Page.enable', {}, sessionId);
  await rpc(ws, 'Page.navigate', { url: `${PAGE}?only=${screen}` }, sessionId);
  await sleep(800);

  const expr = `(() => {
    const de = document.documentElement;
    const vw = window.innerWidth;
    const over = [...document.querySelectorAll('*')]
      .filter(e => e.getBoundingClientRect().right > vw + 0.5)
      .slice(0, 15)
      .map(e => e.tagName + '.' + (e.className || '-') + ' right=' + Math.round(e.getBoundingClientRect().right) + ' w=' + Math.round(e.getBoundingClientRect().width));
    const card = document.querySelector('.q, .card');
    return JSON.stringify({
      innerWidth: vw, clientWidth: de.clientWidth, scrollWidth: de.scrollWidth,
      visualScale: window.visualViewport && window.visualViewport.scale,
      h: card ? Math.round(card.getBoundingClientRect().height) : null,
      over
    }, null, 1);
  })()`;
  const { result } = await rpc(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
  console.log(result.value);
  ws.close();
} finally {
  chrome.kill();
}

// 用 DevTools 协议按真实设备视口出图。
// 为什么不直接 `chrome --screenshot --window-size=390,844`：无头 Chrome 对窗口宽度有下限，
// 实际布局宽度会被撑到 500 CSS px，截出来的图右边被裁、还带一倍缩放——图上看着「CSS 写错了」，
// 其实是量错了视口。这里用 Emulation.setDeviceMetricsOverride 把视口钉死，再 Page.captureScreenshot。
//
// 用法：node shoot.mjs [只出某一屏]
// 产物：shot-<屏名>.png（780×1688，即 390×844 @2x）

import { writeFileSync, readFileSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = 'file://' + join(HERE, 'wrongbook-h5.html');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PROFILE = join(HERE, '.chrome-profile');

const SCREENS = ['overview', 'topics', 'detail', 'list', 'add', 'empty', 'dark'];
const only = process.argv[2];
const targets = only ? [only] : SCREENS;

// 端口交给 Chrome 自己挑（--remote-debugging-port=0），它把实际端口写进 DevToolsActivePort。
// 写死端口会撞上「上一个实例正在退出但还占着端口」，这时命令会静默挂死——报错都没有。
rmSync(PROFILE, { recursive: true, force: true });
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0',
  `--user-data-dir=${PROFILE}`,
  'about:blank',
], { stdio: 'ignore' });

const withTimeout = (p, label, ms = 8000) => Promise.race([
  p,
  sleep(ms).then(() => { throw new Error(`${label} 超时 ${ms}ms`); }),
]);

const rpc = async (ws, method, params = {}, sessionId) => {
  const id = Math.floor(Math.random() * 1e9);
  const answer = new Promise((res, rej) => {
    const onMsg = ev => {
      const m = JSON.parse(ev.data);
      if (m.id !== id) return;
      ws.removeEventListener('message', onMsg);
      m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result);
    };
    ws.addEventListener('message', onMsg);
  });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return withTimeout(answer, method);
};

try {
  // 等 DevToolsActivePort 落地：第一行是端口，第二行是 browser 的 ws 路径
  let port;
  for (let i = 0; i < 80; i++) {
    try {
      const [line] = readFileSync(join(PROFILE, 'DevToolsActivePort'), 'utf8').split('\n');
      port = Number(line.trim());
      if (port) break;
    } catch { /* 还没写出来 */ }
    await sleep(120);
  }
  if (!port) throw new Error('DevTools 端口没起来');

  const { webSocketDebuggerUrl: wsUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();

  const ws = new WebSocket(wsUrl);
  await new Promise((r, j) => {
    ws.addEventListener('open', r, { once: true });
    ws.addEventListener('error', j, { once: true });
  });

  for (const name of targets) {
    const { targetId } = await rpc(ws, 'Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await rpc(ws, 'Target.attachToTarget', { targetId, flatten: true });

    await rpc(ws, 'Emulation.setDeviceMetricsOverride', {
      width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
    }, sessionId);
    await rpc(ws, 'Page.enable', {}, sessionId);
    await rpc(ws, 'Page.navigate', { url: `${PAGE}?only=${name}` }, sessionId);
    await sleep(700);   // 字体与 innerHTML 渲染，页面是纯静态的，700ms 足够

    const { data } = await rpc(ws, 'Page.captureScreenshot', { format: 'png' }, sessionId);
    const out = join(HERE, `shot-${name}.png`);
    writeFileSync(out, Buffer.from(data, 'base64'));
    console.log(`✓ shot-${name}.png`);

    await rpc(ws, 'Target.closeTarget', { targetId });
  }
  ws.close();
} finally {
  chrome.kill();
}

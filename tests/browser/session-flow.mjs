// Optional browser acceptance using Node's built-in CDP WebSocket, no npm runtime.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, mkdir, writeFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const [base, viewport = 'desktop', artifacts = join(tmpdir(), 'idlixport-browser-artifacts')] = process.argv.slice(2);
const chromeBin = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = await mkdtemp(join(tmpdir(), 'idlixport-browser-'));
await mkdir(artifacts, { recursive: true });
const downloadDir = join(profile, 'downloads'); await mkdir(downloadDir);
const chrome = spawn(chromeBin, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let chromeErrors = ''; chrome.stderr.on('data', (data) => { chromeErrors = (chromeErrors + data.toString()).slice(-3000); });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, message, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { const result = await check(); if (result) return result; } catch (error) { if (error.message.includes("SyntaxError")) throw error; } await sleep(100); }
  try { console.error('Browser diagnostic', await evaluate(`JSON.stringify({connection:document.querySelector('#connection')?.textContent,cards:[...document.querySelectorAll('#jobs .job')].map(e=>({id:e.dataset.jobId,state:e.className}))})`)); } catch {}
  throw new Error(message);
}
let socket;
const pending = new Map(), handlers = new Map(), runtimeErrors = [], interceptionErrors = [];
let nextId = 1;
async function command(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve: (result) => { clearTimeout(timer); resolve(result); }, reject: (error) => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const value = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
  if (value.exceptionDetails) throw new Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);
  return value.result.value;
}
async function click(selector) {
  const point = await evaluate(`(() => {const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing element');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await command('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
  await command('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
}
try {
  const port = await until(async () => Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]), `Chrome failed to start: ${chromeErrors}`);
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    if (data.id) {
      const callback = pending.get(data.id); pending.delete(data.id);
      if (callback) data.error ? callback.reject(new Error(data.error.message)) : callback.resolve(data.result);
    } else if (handlers.has(data.method)) handlers.get(data.method)(data.params);
  });
  handlers.set('Runtime.exceptionThrown', (data) => runtimeErrors.push(data.exceptionDetails.text));
  await command('Runtime.enable'); await command('Page.enable'); await command('Network.enable');
  const width = viewport === 'mobile' ? 390 : 1440, height = viewport === 'mobile' ? 844 : 1000;
  await command('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: viewport === 'mobile' });
  await command('Emulation.setTimezoneOverride', { timezoneId: 'Asia/Jakarta' });
  await command('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloadDir });
  await command('Page.navigate', { url: base });
  await until(() => evaluate(`document.querySelector('#inspect-button')?.disabled === false`), 'UI did not connect');

  const cookieControl = process.argv[5];
  assert.ok(cookieControl, 'Synthetic cookie control required');
  await until(() => evaluate(`document.querySelector('#session-profile').options.length > 0`), 'Profiles missing');
  await click('#browser-session-panel > summary');
  assert.equal(await evaluate(`document.querySelector('#session-consent').checked`), false);
  assert.equal(await evaluate(`document.querySelector('#session-connect').disabled`), true);
  await click('#source-url');
  await command('Input.insertText',{text:'https://z3.idlixku.com/movie/fixture'});
  await click('#inspect-button');
  await until(() => evaluate(`!!document.querySelector('.choice strong')`),'Guest inspection missing');
  assert.equal(await evaluate(`[...document.querySelectorAll('.choice strong')].some(e=>e.textContent==='1080p')`),false);
  await click('#session-consent');
  await click('#session-connect');
  await until(() => evaluate(`document.querySelector('#session-badge').textContent==='Menunggu login'`),'Waiting login missing');
  assert.equal(await evaluate(`document.body.textContent.includes('Sudah login')`),false);
  assert.equal(await evaluate(`document.querySelector('#session-manual').hidden`),false);
  assert.equal(await evaluate(`document.querySelector('#session-login-link').href`),'https://z3.idlixku.com/');
  // Simulate Safari/browser persisting updated cookies; production has no control endpoint.
  await writeFile(cookieControl,'member');
  await evaluate(`window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'))`);
  await until(() => evaluate(`document.querySelector('#session-badge').textContent==='Terhubung'`),'Account not verified');
  await until(() => evaluate(`[...document.querySelectorAll('.choice strong')].some(e=>e.textContent==='1080p')`),'Choices not refreshed automatically');
  // Successful reinspection clears its old error, but preserves unrelated errors.
  assert.equal(await evaluate(`(()=>{const completed=state.inspection;renderInspection({...completed,state:'failed',error:{message:'Old inspection error',action:'Retry'}});renderInspection(completed);return document.querySelector('#notice').hidden;})()`),true);
  assert.equal(await evaluate(`(()=>{notice('Unrelated download error');renderInspection(state.inspection);const text=document.querySelector('#notice').textContent;notice();return text;})()`),'Unrelated download error');
  assert.equal(await evaluate(`JSON.stringify(localStorage).includes('SYNTHETIC_COOKIE_SECRET')`),false);
  assert.equal(await evaluate(`document.querySelector('.mode').textContent`),'SESI AKUN TERHUBUNG');
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`),true);
  await click('#download-button');
  await until(() => evaluate(`!!document.querySelector('#jobs .job.running')`),'Auth download not started');
  await click('#session-disconnect');
  await until(() => evaluate(`document.querySelector('#session-disconnect').hidden && !!document.querySelector('#jobs .job.cancelled')`),'Disconnect did not stop auth job');
  assert.equal(await evaluate(`document.querySelector('#session-consent').checked`),false);
  // A wrong-profile guest must not be labelled connected. Retry retains active consent.
  await writeFile(cookieControl,'permission');
  await click('#session-consent'); await click('#session-connect');
  await until(() => evaluate(`!document.querySelector('#session-retry').hidden`),'Permission recovery missing');
  await writeFile(cookieControl,'verification');
  await click('#session-retry');
  await until(() => evaluate(`document.querySelector('#session-status').textContent.includes('IDLIX meminta verifikasi browser')`),'Verification state missing');
  assert.equal(await evaluate(`document.querySelector('#session-badge').textContent`),'Akun dikenali');
  await writeFile(cookieControl,'member'); await click('#session-retry');
  await until(() => evaluate(`document.querySelector('#session-badge').textContent==='Terhubung'`),'Retry did not reconnect');
  await until(() => evaluate(`!!document.querySelector('#jobs .reinspect-job')`),'Stale session recovery missing');
  await click('#jobs .reinspect-job');
  await until(() => evaluate(`[...document.querySelectorAll('.choice strong')].some(e=>e.textContent==='1080p') && !document.querySelector('#selection').hidden`),'History source reinspection failed');
  await evaluate(`window.scrollTo(0,0)`); await sleep(100);
  const layout=await command('Page.getLayoutMetrics');
  const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:layout.cssContentSize.width,height:layout.cssContentSize.height,scale:1}});
  await writeFile(join(artifacts,`session-${viewport}.png`),Buffer.from(screenshot.data,'base64'));
  await command('Page.reload',{ignoreCache:true});
  await until(() => evaluate(`document.querySelector('#session-badge').textContent==='Terhubung'`),'Tab refresh lost active backend session');
  await click('#browser-session-panel > summary');
  await click('#session-disconnect');
  await until(() => evaluate(`document.querySelector('#session-disconnect').hidden`),'Final disconnect failed');
  assert.deepEqual(runtimeErrors,[]);
  console.log(JSON.stringify({viewport,session_flow:'passed',consent:'passed',automatic_reinspection:'passed',disconnect:'passed',verification:'passed',retry:'passed',tab_reload:'passed'}));
} finally {
  try { if (socket?.readyState === WebSocket.OPEN) await command('Browser.close'); } catch { /* Browser may close before acknowledgement. */ }
  socket?.close();
  if (chrome.exitCode === null) chrome.kill('SIGTERM');
  await sleep(300); await rm(profile, { recursive: true, force: true });
}

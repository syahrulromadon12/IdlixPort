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
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true);
  assert.equal(await evaluate(`document.querySelector('#subtitle').disabled`), true);
  await evaluate(`document.querySelector('#source-url').focus()`);
  await command('Input.insertText', { text: 'http://source.example/master.m3u8?secret=SOURCE_SECRET' });
  await click('#inspect-button');
  await until(() => evaluate(`!document.querySelector('#selection').hidden && !!document.querySelector('#qualities input')`), 'Inspection did not produce choices');
  assert.match(await evaluate(`document.querySelector('#selected-label').textContent`), /1080p/);
  await evaluate(`(() => {const labels=[...document.querySelectorAll('#qualities label')];labels.find(e=>e.querySelector('strong').textContent==='720p').click()})()`);
  assert.match(await evaluate(`document.querySelector('#selected-label').textContent`), /720p/);
  await evaluate(`(() => {const labels=[...document.querySelectorAll('#qualities label')];labels.find(e=>e.querySelector('strong').textContent==='1080p').click()})()`);
  await evaluate(`(() => {const folder=document.querySelector('#folder');folder.value=[...folder.options].find(o=>o.textContent==='chosen-folder').value;folder.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await click('#download-button');
  await until(() => evaluate(`!!document.querySelector('.job.running progress')`), 'Progress not rendered');
  await command('Page.reload', { ignoreCache: true });
  await until(() => evaluate(`!!document.querySelector('.job.completed .job-actions')`), 'Download did not survive reload');
  assert.equal(await evaluate(`Object.values(localStorage).some(value=>value.includes('SOURCE_SECRET') || value.includes('http://'))`), false);
  const jobId = await evaluate(`document.querySelector('.job.completed').dataset.jobId`);
  const job = await (await fetch(`${base}/api/jobs/${jobId}`)).json();
  assert.equal(job.effective_quality.quality_class, 1080);
  assert.equal(job.state, 'completed');
  assert.equal(JSON.stringify(job).includes('SOURCE_SECRET'), false);
  await click('.job.completed .open-file');
  await until(() => evaluate(`!document.querySelector('.job.completed .open-file').disabled`), 'Open-file request did not finish');
  await click('.job.completed .open-folder');
  await until(() => evaluate(`!document.querySelector('.job.completed .open-folder').disabled`), 'Open-folder request did not finish');
  const asset = job.files[0];
  const response = await fetch(`${base}/api/artifacts/${asset.id}/file`);
  const bytes = Buffer.from(await response.arrayBuffer());
  assert.equal(bytes.length, asset.size_bytes);
  const range = await fetch(`${base}/api/artifacts/${asset.id}/file`, { headers: { Range: 'bytes=0-31' } });
  assert.equal(range.status, 206); assert.deepEqual(Buffer.from(await range.arrayBuffer()), bytes.subarray(0, 32));
  await click('.job.completed .copy-link');
  const savedFile = await until(async () => (await readdir(downloadDir)).find(name=>!name.endsWith('.crdownload')), 'Browser copy did not finish');
  await until(async () => (await stat(join(downloadDir, savedFile))).size === bytes.length, 'Browser copy incomplete');
  const hash = (data) => createHash('sha256').update(data).digest('hex');
  assert.equal(hash(await readFile(join(downloadDir, savedFile))), hash(bytes));
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true);
  await evaluate(`window.scrollTo(0,0)`); await sleep(150);
  const layout = await command('Page.getLayoutMetrics');
  const screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: layout.cssContentSize.width, height: layout.cssContentSize.height, scale: 1 } });
  await writeFile(join(artifacts, `${viewport}.png`), Buffer.from(screenshot.data, 'base64'));

  // P3: exercise queued cancellation, a new retry attempt and paginated history.
  const queuedIds = await evaluate(`(async () => {
    const ids=[];
    for(let i=0;i<12;i++) ids.push((await api('/api/jobs',{method:'POST',body:{inspection_id:state.inspection.id,policy:'max_1080'}})).id);
    await refresh();return ids;
  })()`);
  const cancelledId = queuedIds.at(-1);
  await until(() => evaluate(`!!document.querySelector('#jobs [data-job-id="${cancelledId}"] .cancel-job')`), 'Cancel control missing');
  await click(`#jobs [data-job-id="${cancelledId}"] .cancel-job`);
  await until(() => evaluate(`!!document.querySelector('#jobs [data-job-id="${cancelledId}"].cancelled .retry-job')`), 'Queued cancellation did not finish');
  await evaluate(`(async () => {for(const id of ${JSON.stringify(queuedIds.slice(0,-1))}) await api('/api/jobs/'+id+'/cancel',{method:'POST'});})()`);
  await click(`#jobs [data-job-id="${cancelledId}"] .retry-job`);
  const retriedId = await until(async () => {
    const history = await (await fetch(`${base}/api/history?limit=50`)).json();
    return history.jobs.find(entry=>entry.retry_of===cancelledId)?.id;
  }, 'Retry attempt missing');
  await until(() => evaluate(`!!document.querySelector('#jobs [data-job-id="${retriedId}"].completed')`), 'Retried job did not complete');
  await click('#history > summary');
  await until(() => evaluate(`document.querySelector('#history-jobs').children.length === 10 && !document.querySelector('#history-next').disabled`), 'History first page missing');
  await click('#history-next');
  await until(() => evaluate(`document.querySelector('#history-status').textContent.startsWith('11')`), 'History next page missing');
  await click('#history-prev');
  await until(() => evaluate(`document.querySelector('#history-status').textContent.startsWith('1–10')`), 'History previous page missing');
  await click('#history > summary');
  let approveDelete = false;
  handlers.set('Page.javascriptDialogOpening', async () => {
    await command('Page.handleJavaScriptDialog', {accept:approveDelete});
  });
  await click(`#jobs [data-job-id="${jobId}"] .delete-results`);
  await sleep(200);
  assert.equal((await (await fetch(`${base}/api/jobs/${jobId}`)).json()).files[0].available, true);
  approveDelete = true;
  await click(`#jobs [data-job-id="${jobId}"] .delete-results`);
  await until(async () => (await (await fetch(`${base}/api/jobs/${jobId}`)).json()).storage.files_deleted, 'Confirmed result deletion failed');
  assert.equal((await (await fetch(`${base}/api/jobs/${retriedId}`)).json()).files[0].available, true);
  await command('Page.reload', {ignoreCache:true});
  await until(() => evaluate(`!!document.querySelector('#jobs [data-job-id="${retriedId}"].completed')`), 'Retry history did not survive reload');
  let offline = true, modify = false;
  handlers.set('Fetch.requestPaused', async (event) => {
    try {
      if (offline) return await command('Fetch.failRequest', { requestId: event.requestId, errorReason: 'Failed' });
      if (modify) {
        const raw = await command('Fetch.getResponseBody', { requestId: event.requestId });
        const data = JSON.parse(raw.base64Encoded ? Buffer.from(raw.body, 'base64').toString() : raw.body);
        for (const entry of data.jobs) {
          if (entry.kind === 'inspect' && entry.metadata) {
            entry.metadata.title = '<img src=x onerror="window.__xss=1">';
            entry.expires_at = 1; entry.expired = true;
          }
        }
        return await command('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(data)).toString('base64') });
      }
      await command('Fetch.continueRequest', { requestId: event.requestId });
    } catch (error) { interceptionErrors.push(error.message); }
  });
  await command('Fetch.enable', { patterns: [{ urlPattern: `${base}/api/jobs`, requestStage: 'Response' }] });
  await evaluate(`window.dispatchEvent(new Event('online'))`);
  await until(() => evaluate(`document.querySelector('#connection').classList.contains('offline')`), 'Offline state not rendered');
  offline = false; modify = true;
  await evaluate(`window.dispatchEvent(new Event('online'))`);
  await until(() => evaluate(`document.querySelector('#connection').classList.contains('online') && document.querySelector('#film-title').textContent.includes('<img')`), 'Polling did not recover');
  assert.equal(await evaluate(`!!document.querySelector('#film-title img') || window.__xss === 1`), false);
  assert.equal(await evaluate(`document.querySelector('#download-button').disabled`), true);
  assert.equal(await evaluate(`document.querySelector('#refresh-button').hidden`), false);
  await command('Fetch.disable');
  assert.deepEqual(runtimeErrors, []); assert.deepEqual(interceptionErrors, []);
  const version = await command('Browser.getVersion');
  console.log(JSON.stringify({ viewport, browser: version.product, flow: 'passed', reload: 'passed', browser_copy_hash: 'matched', offline_recovery: 'passed', expiry: 'passed', xss: 'passed', cancel_retry: 'passed', history_pagination: 'passed', confirmed_delete: 'passed' }));
} finally {
  try { if (socket?.readyState === WebSocket.OPEN) await command('Browser.close'); } catch { /* Browser may close before acknowledgement. */ }
  socket?.close();
  if (chrome.exitCode === null) chrome.kill('SIGTERM');
  await sleep(300); await rm(profile, { recursive: true, force: true });
}

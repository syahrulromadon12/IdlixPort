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

  const caption=process.argv[5];
  const source='http://source.example/master.m3u8';
  async function setValue(selector,value) {
    await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  }
  async function freshInspection() {
    const previous=await evaluate(`state.current`);
    await click('#refresh-button');
    await until(()=>evaluate(`state.current!==${JSON.stringify(previous)} && !document.querySelector('#selection').hidden`),'Fresh inspection missing');
  }
  await click('#source-url');await command('Input.insertText',{text:source});await click('#inspect-button');
  await until(()=>evaluate(`!document.querySelector('#selection').hidden`),'Subtitle inspection missing');
  assert.equal(await evaluate(`document.querySelector('#subtitle').value`),'embed');
  assert.equal(await evaluate(`document.querySelector('#subtitle-language').value`),'id');
  await setValue('#subtitle','separate');await setValue('#subtitle-language','en');
  await click('#download-button');
  await until(()=>evaluate(`!!document.querySelector('#jobs .job.completed .copy-link')`),'Separate subtitle download failed');
  const separateId=await evaluate(`document.querySelector('#jobs .job.completed').dataset.jobId`);
  const separate=await (await fetch(`${base}/api/jobs/${separateId}`)).json();
  assert.equal(separate.subtitle_status.language,'en');assert.equal(separate.files.length,2);
  assert.ok(separate.files.some(file=>file.name==='media.en.srt'));
  // Failed subtitle keeps the video, and explicit continue needs no new transfer.
  await rm(caption,{force:true});await freshInspection();await click('#download-button');
  await until(()=>evaluate(`!!document.querySelector('#jobs .partial .continue-video')`),'Partial recovery actions missing');
  const partialId=await evaluate(`document.querySelector('#jobs .partial').dataset.jobId`);
  await click(`#jobs [data-job-id="${partialId}"] .continue-video`);
  await until(()=>evaluate(`!!document.querySelector('#jobs [data-job-id="${partialId}"].completed')`),'Continue without subtitle failed');
  // Retry subtitles against a fresh source session, retaining the old video.
  await freshInspection();await click('#download-button');
  await until(()=>evaluate(`!!document.querySelector('#jobs .partial .retry-job')`),'Subtitle retry missing');
  const failedId=await evaluate(`document.querySelector('#jobs .partial').dataset.jobId`);
  await writeFile(caption,"1\n00:00:00,000 --> 00:00:00,800\nSynthetic subtitle\n\n");
  await click(`#jobs [data-job-id="${failedId}"] .retry-job`);
  await until(()=>evaluate(`!![...document.querySelectorAll('#jobs .job.completed')].find(e=>e.textContent.includes('tergabung sebagai track MKV'))`),'Subtitle retry did not embed');
  // Batch has independent outcomes; missing Indonesian never substitutes English.
  await click('#batch-panel > summary');
  await setValue('#batch-urls',source+"?batch-one\nhttp://source.example/missing.m3u8\n"+source+'?no-sub');
  await click('#batch-submit');
  await until(()=>evaluate(`!!document.querySelector('.batch-summary')`),'Batch summary missing');
  const batchId=await evaluate(`document.querySelector('.batch-summary').dataset.batchId`);
  await until(()=>evaluate(`document.querySelector('[data-batch-id="${batchId}"]').textContent.includes('2 selesai') && document.querySelector('[data-batch-id="${batchId}"]').textContent.includes('1 gagal')`),'Independent batch outcomes missing',30000);
  await click(`[data-batch-id="${batchId}"] .batch-detail`);
  await until(()=>evaluate(`document.querySelectorAll('[data-batch-id="${batchId}"] .batch-children .job').length===3`),'Batch child detail missing');
  await click(`[data-batch-id="${batchId}"] .batch-children .failed .retry-job`);
  await sleep(500);
  const retried=await (await fetch(`${base}/api/batches/${batchId}`)).json();
  const retryId=retried.jobs[1].id;assert.equal(retried.jobs[1].attempt,2);
  await until(()=>evaluate(`!!document.querySelector('#jobs [data-job-id="${retryId}"].failed')`),'Per-film retry failed to terminate');
  await setValue('#batch-urls',source+"?cancel-one\n"+source+'?cancel-two');await setValue('#batch-subtitle','off');

  await click('#batch-submit');
  await until(()=>evaluate(`!!document.querySelector('.cancel-batch')`),'Batch cancel missing');
  const cancelId=await evaluate(`document.querySelector('.cancel-batch').closest('.batch-summary').dataset.batchId`);
  await click(`[data-batch-id="${cancelId}"] .cancel-batch`);
  await until(()=>evaluate(`document.querySelector('[data-batch-id="${cancelId}"]').textContent.includes('2 dibatalkan')`),'Batch cancellation failed');
  await command('Page.reload',{ignoreCache:true});
  await until(()=>evaluate(`document.querySelectorAll('.batch-summary').length===2`),'Batch did not survive reload');
  await click('#batch-panel > summary');
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),true);
  const y=await evaluate(`Math.floor(window.scrollY+document.querySelector('#batch-panel').getBoundingClientRect().top)`);
  const layout=await command('Page.getLayoutMetrics');
  const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y,width:layout.cssContentSize.width,height:Math.min(1600,layout.cssContentSize.height-y),scale:1}});
  await writeFile(join(artifacts,`batch-${viewport}.png`),Buffer.from(screenshot.data,'base64'));
  assert.deepEqual(runtimeErrors,[]);
  console.log(JSON.stringify({viewport,p5_flow:'passed',default_id:'passed',separate:'passed',partial_continue:'passed',subtitle_retry:'passed',batch:'passed',batch_cancel:'passed',reload:'passed'}));
} finally {
  try { if (socket?.readyState === WebSocket.OPEN) await command('Browser.close'); } catch { /* Browser may close before acknowledgement. */ }
  socket?.close();
  if (chrome.exitCode === null) chrome.kill('SIGTERM');
  await sleep(300); await rm(profile, { recursive: true, force: true });
}

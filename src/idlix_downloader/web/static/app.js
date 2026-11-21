"use strict";
const $ = (id) => document.getElementById(id);
const safeStore = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
};
const saved = safeStore.get("idlixport.inspection");
const state = {
  csrf: "", ready: false, bootstrap: false, current: /^[a-f0-9]{32}$/.test(saved || "") ? saved : null,
  inspection: null, signature: "", submitting: false, inspecting: false, timer: null,
  polling: false, failures: 0, downloadActive: false, lastRequest: "", requestKey: "",
  historyOffset: 0, historyLoading: false, historyNext: null,
  folder: safeStore.get("idlixport.folder") || "default", cards: new Map(),
};
const active = (job) => ["queued", "running", "cancelling"].includes(job.state);
const phases = { extracting: "Memeriksa sumber", waiting_source: "Menunggu sumber", downloading: "Mengunduh", merging: "Menggabungkan video dan audio", validating: "Memeriksa hasil", subtitles: "Menyiapkan subtitle" };
const statuses = { partial: "Hasil parsial", queued: "Antre", completed: "Selesai", failed: "Gagal", cancelled: "Dibatalkan", interrupted: "Terhenti", cancelling: "Menghentikan" };
function bytes(number) {
  if (!Number.isFinite(number)) return "Ukuran belum diketahui";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = number, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
  return `${value.toLocaleString("id-ID", { maximumFractionDigits: index > 1 ? 1 : 0 })} ${units[index]}`;
}
function duration(number) {
  if (!Number.isFinite(number) || number <= 0) return "Durasi belum diketahui";
  const minutes = Math.ceil(number / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} jam ${minutes % 60} menit` : `${minutes} menit`;
}
function notice(message = "", owner = "", showSession = false) {
  const box = $("notice");
  box.dataset.owner = owner;
  if (box.textContent !== message) box.textContent = message;
  box.hidden = !message;
  $("show-session").hidden = !showSession;
}
function connection(online) {
  $("connection").textContent = online ? "Lokal siap" : "Terputus · mencoba lagi";
  $("connection").className = `connection ${online ? "online" : "offline"}`;
}
async function api(path, { method = "GET", body, key, retry = true } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  const headers = {};
  if (method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  try {
    const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, cache: "no-store", credentials: "same-origin" });
    const data = response.status === 204 ? {} : await response.json();
    if (response.status === 403 && method !== "GET" && retry) {
      const session = await api("/api/session");
      state.csrf = session.csrf_token;
      return api(path, { method, body, key, retry: false });
    }
    if (!response.ok) {
      const error = new Error(data.message || "Permintaan belum dapat diproses.");
      error.code = data.code;
      error.action = data.action;
      throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function errorMessage(error) {
  if (error.name === "AbortError" || error instanceof TypeError) return "Koneksi ke IdlixPort terputus. Pastikan aplikasi masih aktif; kami akan mencoba lagi.";
  return [error.message, error.action].filter(Boolean).join(" ");
}
function step(number) {
  ["link", "quality", "download"].forEach((name, index) => {
    const element = $(`step-${name}`);
    element.classList.toggle("current", index === number - 1);
    if (index === number - 1) element.setAttribute("aria-current", "step"); else element.removeAttribute("aria-current");
  });
}
function currentChoice() {
  const input = document.querySelector('input[name="quality"]:checked');
  return state.inspection?.metadata?.choices.find((choice) => choice.choice_id === input?.value);
}
function updateSelection() {
  const choice = currentChoice();
  const expired = state.inspection && Date.now() / 1000 >= state.inspection.expires_at;
  const policy = $("policy").value;
  $("download-button").disabled = !state.ready || state.submitting || expired || !choice?.audio_available;
  $("selected-label").textContent = expired ? "Pemeriksaan kedaluwarsa" : policy === "best" ? "Terbaik saat mulai" : policy === "max_1080" ? "Maksimum 1080p" : choice ? `${choice.quality_class}p dipilih` : "Pilih resolusi";
  const estimate = state.inspection?.metadata?.estimates?.[choice?.choice_id];
  $("estimated-size").textContent = estimate ? `≈ ${bytes(estimate)} · perkiraan sumber` : "Ukuran belum diketahui";
  $("evidence").textContent = choice ? `Dimensi: ${choice.width || "?"} × ${choice.height}. Informasi: ${{label: "label sumber", probe: "sampel video", dimensions: "metadata sumber"}[choice.evidence] || "sumber"}.` : "";
}
let renderedSeries = null;
function renderSeries(job) {
  const metadata = job.metadata;
  $("series-selection").hidden = false;
  $("selection").hidden = true;
  if (renderedSeries === job.id) return;
  renderedSeries = job.id;
  $("series-title").textContent = metadata.title;
  const field = $("series-episodes");
  field.querySelectorAll("label").forEach((label) => label.remove());
  for (const {season, episode} of metadata.episodes) {
    const label = element("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = `${metadata.series_url}/season/${season}/episode/${episode}`;
    label.append(input, document.createTextNode(` Season ${season}, episode ${episode}`));
    field.append(label);
  }
  updateEpisodeActions();
}

function selectedEpisodes() {
  return [...$("series-episodes").querySelectorAll("input:checked")].map((input) => input.value);
}

function updateEpisodeActions() {
  const count = selectedEpisodes().length;
  const available = $("series-episodes").querySelectorAll("input").length;
  $("inspect-episode").disabled = count !== 1;
  $("batch-episodes").disabled = count < 2 || count > 20;
  $("series-status").textContent = available ? `${count} dipilih · maksimal 20 untuk batch` : "Belum ada episode yang tersedia.";
}

$("series-episodes").addEventListener("change", updateEpisodeActions);
$("inspect-episode").addEventListener("click", () => {
  $("source-url").value = selectedEpisodes()[0];
  $("inspect-form").requestSubmit();
});
$("batch-episodes").addEventListener("click", () => {
  $("batch-urls").value = selectedEpisodes().join("\n");
  $("batch-panel").open = true;
  $("batch-urls").focus();
});

function renderInspection(job) {
  $("series-selection").hidden = true;
  state.inspection = job;
  state.inspecting = active(job);
  $("cancel-inspection").hidden = !active(job);
  $("cancel-inspection").disabled = job.state === "cancelling";
  $("inspect-button").disabled = !state.ready || state.inspecting;
  if (active(job)) {
    if ($("notice").dataset.owner === "inspection") notice();
    $("inspection-status").textContent = job.state === "queued" ? "Menunggu giliran pemeriksaan…" : `${phases[job.phase] || "Memeriksa sumber"}…`;
    $("selection").hidden = true;
    step(1);
    return;
  }
  if (job.state !== "completed") {
    $("inspection-status").textContent = "Pemeriksaan belum berhasil.";
    $("selection").hidden = true;
    if (job.error) {
      const showSession = job.error.code === "VERIFICATION_REQUIRED" &&
        sessionState?.state === "disconnected" && sessionState.profiles.length > 0;
      notice([job.error.message, job.error.action].filter(Boolean).join(" "), "inspection", showSession);
    }
    return;
  }
  if ($("notice").dataset.owner === "inspection") notice();
  if (job.metadata.episodes) {
    $("inspection-status").textContent = "";
    renderSeries(job);
    return;
  }
  const signature = `${job.id}:${JSON.stringify(job.metadata)}`;
  $("inspection-status").textContent = "Pemeriksaan selesai.";
  if (state.signature !== signature) {
    state.signature = signature;
    $("selection").hidden = false;
    $("film-title").textContent = job.metadata.title || "Film";
    $("film-details").textContent = duration(job.metadata.duration);
    $("source-limit").textContent = job.metadata.session_max_height ? `Batas sesi ${job.metadata.session_max_height}p` : "Pilihan dari sumber";
    const available = job.metadata.choices.filter((choice) => choice.audio_available);
    const preferred = available.filter((choice) => choice.quality_class <= 1080).at(-1) || available[0];
    $("qualities").replaceChildren();
    for (const choice of job.metadata.choices) {
      const label = document.createElement("label"); label.className = "choice";
      const radio = document.createElement("input"); radio.type = "radio"; radio.name = "quality"; radio.value = choice.choice_id; radio.disabled = !choice.audio_available; radio.checked = choice.choice_id === preferred?.choice_id;
      radio.addEventListener("change", updateSelection);
      const title = document.createElement("strong"); title.textContent = `${choice.quality_class}p`;
      const detail = document.createElement("small"); detail.textContent = choice.audio_available ? `${choice.width || "?"} × ${choice.height}` : "Audio belum terkonfirmasi";
      label.append(radio, title, detail); $("qualities").append(label);
    }
    const languages=job.metadata.subtitles || [];
    $("subtitle-language").replaceChildren(...languages.map((language) => {
      const option=document.createElement("option"); option.value=language.code;option.textContent=language.label;return option;
    }));
    const indonesia=languages.find((language) => language.code==="id") || languages.find((language)=>language.code.startsWith("id-"));
    $("subtitle").value=indonesia ? "embed" : "off";
    if (indonesia) $("subtitle-language").value=indonesia.code;
    $("subtitle").disabled=!languages.length;
    $("subtitle-language").disabled=!languages.length || !indonesia;
    $("subtitle-hint").textContent=!languages.length ? "Sumber tidak menyediakan subtitle yang didukung." : indonesia ? "Indonesia tersedia; default digabung dalam MKV tanpa encode ulang video." : "Indonesia tidak tersedia. Pilih bahasa lain secara eksplisit atau lanjutkan tanpa subtitle.";
    $("no-formats").hidden = available.length > 0;
    $("policy").value = "choice";
    $("policy").disabled = !available.length;
    $("refresh-button").hidden = false;
    step(2);
  } else { $("selection").hidden = false; }
  updateSelection();
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function action(button, path) {
  button.disabled = true;
  try { await api(path, { method: "POST" }); }
  catch (error) { notice(errorMessage(error)); }
  finally { button.disabled = false; }
}
function jobCard(job) {
  const card = element("article", `job ${job.state}`); card.dataset.jobId = job.id;
  const head = element("div", "job-head"), text = element("div");
  text.append(element("h3", "", job.title || "Film"));
  const created = element("time", "", new Date(job.created * 1000).toLocaleString("id-ID", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
  text.append(created); head.append(text, element("span", "job-state", statuses[job.state] || phases[job.phase] || "Memproses")); card.append(head);
  if (active(job)) {
    const progress = document.createElement("progress"); progress.max = 100;
    progress.setAttribute("aria-label", `Progres ${job.title || "film"}`);
    const metrics = [phases[job.phase] || "Menunggu giliran"];
    if (job.phase === "downloading") {
      const {total_bytes: total, total_bytes_estimate: estimate, downloaded_bytes: downloaded,
        fragment_index: fragment, fragment_count: fragments} = job.progress || {};
      const hasTotal = Number.isFinite(total) && total > 0;
      const hasEstimate = Number.isFinite(estimate) && estimate > 0;
      let percent;
      if (Number.isFinite(downloaded) && downloaded >= 0 && (hasTotal || hasEstimate)) {
        percent = 100 * downloaded / (hasTotal ? total : estimate);
      } else if (Number.isFinite(fragment) && fragment >= 0 && Number.isFinite(fragments) && fragments > 0) {
        percent = 100 * fragment / fragments;
      }
      if (percent !== undefined) {
        progress.value = Math.min(99, percent);
        metrics.push(`${hasTotal ? "" : "≈ "}${Math.floor(progress.value)}%`);
      } else {
        progress.hidden = true;
        metrics.push("Persentase belum tersedia");
      }
    }
    if (Number.isFinite(job.progress?.downloaded_bytes)) metrics.push(bytes(job.progress.downloaded_bytes));
    if (job.progress?.speed > 0) metrics.push(`${bytes(job.progress.speed)}/s`);
    if (Number.isFinite(job.progress?.eta)) metrics.push(`sisa ≈ ${Math.ceil(job.progress.eta / 60)} menit`);
    card.append(progress, element("p", "job-progress", metrics.join(" · ")));
  }
  const controls = element("div", "job-actions");
  if (active(job)) {
    const cancel = element("button", "button secondary cancel-job", job.state === "cancelling" ? "Menghentikan…" : "Batalkan");
    cancel.type = "button"; cancel.disabled = job.state === "cancelling";
    cancel.addEventListener("click", () => jobAction(cancel, job, "cancel")); controls.append(cancel);
  }
  if (!active(job) && job.needs_reinspection && job.inspection_id) {
    const inspect = element("button", "button secondary reinspect-job", "Periksa ulang"); inspect.type="button";
    inspect.addEventListener("click", async () => {
      inspect.disabled=true;
      try {
        const result=await api(`/api/inspections/${job.inspection_id}/refresh`, {method:"POST",key:crypto.randomUUID()});
        state.current=result.id;state.signature="";safeStore.set("idlixport.inspection",result.id);await refresh();
      } catch(error) {notice(errorMessage(error));}
      finally {inspect.disabled=false;}
    }); controls.append(inspect);
  }
  if (job.can_continue) {
    const proceed=element("button","button secondary continue-video","Lanjut tanpa subtitle");proceed.type="button";
    proceed.addEventListener("click",()=>jobAction(proceed,job,"continue-without-subtitles"));controls.append(proceed);
  }
  if (job.can_retry) {
    const retry = element("button", "button secondary retry-job", "Coba lagi"); retry.type = "button";
    retry.addEventListener("click", () => jobAction(retry, job, "retry")); controls.append(retry);
  }
  if (!active(job) && (job.storage?.bytes > 0 || job.files?.some((file) => file.available))) {
    const remove = element("button", "button secondary delete-results", "Hapus hasil"); remove.type = "button";
    remove.addEventListener("click", () => {
      if (window.confirm("Hapus semua hasil dan file parsial pekerjaan ini secara permanen? Riwayat tetap disimpan.")) jobAction(remove, job, "files");
    }); controls.append(remove);
  }
  if (controls.childElementCount) card.append(controls);
  const details = element("details", "job-details"); details.append(element("summary", "", "Detail unduhan"));
  const requested = job.requested_quality?.choice;
  const policy = job.requested_quality?.policy;
  const requestedLabel = requested ? `${requested.quality_class}p` : policy === "best" ? "Terbaik saat mulai" : "Maksimum 1080p";
  const effective = job.effective_quality;
  details.append(element("p", "field-hint", `Attempt ${job.attempt || 1} dari 3 · Pilihan: ${requestedLabel}`));
  if (effective) details.append(element("p", "field-hint", `Hasil: ${effective.quality_class}p · ${effective.width || "?"} × ${effective.height}`));
  if (job.storage?.bytes > 0 && job.state !== "completed") details.append(element("p", "field-hint", `${bytes(job.storage.bytes)} file parsial tersimpan. Resume bergantung pada dukungan sumber.`));
  if (job.storage?.files_deleted) details.append(element("p", "field-hint", "Hasil pekerjaan ini sudah dihapus."));
  if (job.resumed) details.append(element("p", "field-hint", "Transfer dilanjutkan dari parsial yang terverifikasi."));
  card.append(details);
  if (job.error) {
    card.append(element("p", "job-error", job.error.message), element("p", "field-hint", job.error.action));
  }
  if (["completed","partial"].includes(job.state)) {
    const file = job.files?.find((entry) => entry.available);
    if (file) {
      card.append(element("p", "job-progress", `${job.effective_quality?.quality_class || "?"}p · ${bytes(file.size_bytes)} · tersimpan di perangkat`));
      const actions = element("div", "job-actions");
      const open = element("button", "button secondary open-file", "Buka file"); open.type = "button";
      open.addEventListener("click", () => action(open, `/api/artifacts/${file.id}/open`));
      const folder = element("button", "button secondary open-folder", "Buka folder"); folder.type = "button";
      folder.addEventListener("click", () => action(folder, `/api/jobs/${job.id}/open-folder`));
      actions.append(open, folder); card.append(actions);
      for (const subtitle of job.files.filter((entry)=>entry.available && entry.name.endsWith(".srt"))) {
        const link=element("a","copy-link","Subtitle · "+subtitle.name);link.href=`/api/artifacts/${subtitle.id}/file`;link.download="";card.append(link);
      }
      const sub=job.subtitle_status;
      if (sub) card.append(element("p","field-hint",sub.failed ? "Video tersedia; subtitle belum selesai." : sub.mode==="embed" ? `Subtitle ${sub.language} tergabung sebagai track MKV.` : sub.mode==="separate" ? `Subtitle ${sub.language} disimpan terpisah.` : "Tanpa subtitle."));
      const copy = element("a", "copy-link client-download", "Simpan ke Downloads"); copy.href = `/api/artifacts/${file.id}/file`; copy.download = ""; card.append(copy);
    } else { card.append(element("p", "field-hint", "File sudah dipindahkan atau tidak tersedia.")); }
  }
  return card;
}
const clientDownloads = new Set();

function sendCompletedDownloads(jobs) {
  for (const job of jobs) {
    if (!clientDownloads.has(job.id) || active(job)) continue;
    clientDownloads.delete(job.id);
    if (job.state !== "completed") continue;
    const link = state.cards.get(job.id)?.node.querySelector(".client-download");
    link?.click();
  }
}

function renderJobs(jobs) {
  const downloads = jobs.filter((job) => job.kind === "download");
  state.downloadActive = downloads.some(active);
  $("queue-count").textContent = downloads.length;
  $("empty-jobs").hidden = downloads.length > 0;
  const present = new Set(downloads.map((job) => job.id));
  for (const [id, stored] of state.cards) {
    if (!present.has(id)) { stored.node.remove(); state.cards.delete(id); }
  }
  for (const job of [...downloads].reverse()) {
    const signature = JSON.stringify(job);
    const stored = state.cards.get(job.id);
    if (stored?.signature === signature) continue;
    const node = jobCard(job);
    if (stored) stored.node.replaceWith(node); else $("jobs").prepend(node);
    state.cards.set(job.id, { signature, node });
  }
  updateSelection();
}
function schedule(busy = false) {
  clearTimeout(state.timer);
  const delay = document.hidden ? 15000 : state.failures ? Math.min(30000, 5000 * state.failures) : busy ? 1000 : 5000;
  state.timer = setTimeout(refresh, delay);
}
async function refresh() {
  if (state.polling) return;
  if (!state.ready) return bootstrap();
  state.polling = true;
  const current = state.current;
  let busy = false;
  try {
    const data = await api("/api/jobs");
    connection(true); state.failures = 0;
    renderJobs(data.jobs);
    sendCompletedDownloads(data.jobs);
    renderBatches(data.batches || []);
    busy = data.jobs.some(active);
    if (state.current !== current) { busy = true; return; }
    let inspection = data.jobs.find((job) =>
      job.kind === "inspect" && (!current || job.id === current));
    if (!inspection && current) {
      try {
        inspection = await api(`/api/inspections/${current}`);
      } catch (error) {
        if (state.current !== current) { busy = true; return; }
        if (error.code !== "NOT_FOUND") throw error;
        state.current = null;
        safeStore.set("idlixport.inspection", "");
      }
    }
    if (inspection && state.current === current) {
      state.current = inspection.id;
      safeStore.set("idlixport.inspection", inspection.id);
      renderInspection(inspection);
    } else if (state.current !== current) {
      busy = true;
    }
  } catch (error) { state.failures++; connection(false); }
  finally { state.polling = false; schedule(busy); }
}
async function bootstrap() {
  if (state.bootstrap) return;
  state.bootstrap = true;
  try {
    const [session, folders] = await Promise.all([api("/api/session"), api("/api/folders")]);
    state.csrf = session.csrf_token;
    $("folder").replaceChildren();
    for (const folder of folders.folders) {
      const option = document.createElement("option"); option.value = folder.id; option.textContent = folder.label; $("folder").append(option);
    }
    if (folders.folders.some((folder) => folder.id === state.folder)) $("folder").value = state.folder;
    $("batch-folder").replaceChildren(...[...$("folder").options].map((option)=>option.cloneNode(true)));
    $("batch-folder").value=$("folder").value;
    $("batch-submit").disabled = false;
    state.ready = true; state.failures = 0; connection(true); $("inspect-button").disabled = false;
    await refresh();
  } catch (error) { connection(false); state.failures++; notice(errorMessage(error)); schedule(); }
  finally { state.bootstrap = false; }
}
$("inspect-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice(); $("inspect-button").disabled = true;
  state.inspecting = true; state.signature = ""; $("selection").hidden = true;
  try {
    const result = await api("/api/inspections", { method: "POST", body: { url: $("source-url").value.trim() }, key: crypto.randomUUID() });
    state.current = result.id; safeStore.set("idlixport.inspection", result.id);
    $("inspection-status").textContent = "Menunggu giliran pemeriksaan…"; step(1);
    await refresh();
  } catch (error) { state.inspecting = false; $("inspect-button").disabled = false; notice(errorMessage(error)); }
});
$("refresh-button").addEventListener("click", async () => {
  if (!state.current) return;
  notice(); $("refresh-button").disabled = true;
  try {
    const result = await api(`/api/inspections/${state.current}/refresh`, { method: "POST", key: crypto.randomUUID() });
    state.current = result.id; state.signature = ""; safeStore.set("idlixport.inspection", result.id); await refresh();
  } catch (error) { notice(errorMessage(error)); }
  finally { $("refresh-button").disabled = false; }
});
$("download-button").addEventListener("click", async () => {
  if (state.submitting || !state.inspection) return;
  state.submitting = true; notice(); updateSelection();
  const payload = { inspection_id: state.inspection.id, folder_id: $("folder").value, policy: $("policy").value };
  payload.subtitle_mode=$("subtitle").value;
  if (payload.subtitle_mode!=="off") payload.subtitle_language=$("subtitle-language").value;
  if (payload.policy === "choice") payload.choice_id = currentChoice()?.choice_id;
  const serialized = JSON.stringify(payload);
  if (state.lastRequest !== serialized) { state.lastRequest = serialized; state.requestKey = crypto.randomUUID(); }
  try {
    const result = await api("/api/jobs", { method: "POST", body: payload, key: state.requestKey });
    clientDownloads.add(result.id);
    step(3); await refresh();
  } catch (error) { notice(errorMessage(error)); }
  finally { state.submitting = false; updateSelection(); }
});
$("folder").addEventListener("change", () => { state.folder = $("folder").value; safeStore.set("idlixport.folder", state.folder); });
$("policy").addEventListener("change", updateSelection);
document.addEventListener("visibilitychange", () => { clearTimeout(state.timer); if (!document.hidden) refresh(); else schedule(); });
window.addEventListener("online", () => refresh());
bootstrap();

async function jobAction(button, job, operation) {
  button.disabled = true; notice();
  try {
    await api(`/api/jobs/${job.id}/${operation}`, {
      method: operation === "files" ? "DELETE" : "POST",
      key: operation === "retry" ? crypto.randomUUID() : undefined,
    });
    await refresh();
    if ($("history").open) await loadHistory();
  } catch (error) { notice(errorMessage(error)); }
  finally { button.disabled = false; }
}
async function loadHistory() {
  if (state.historyLoading) return;
  state.historyLoading = true;
  try {
    const data = await api(`/api/history?offset=${state.historyOffset}&limit=10`);
    state.historyNext = data.next_offset;
    $("history-jobs").replaceChildren(...data.jobs.map(jobCard));
    $("history-status").textContent = data.total ? `${data.offset + 1}–${data.offset + data.jobs.length} dari ${data.total} attempt` : "Belum ada riwayat.";
    $("history-prev").disabled = data.offset === 0;
    $("history-next").disabled = data.next_offset === null;
  } catch (error) { $("history-status").textContent = errorMessage(error); }
  finally { state.historyLoading = false; }
}
$("history").addEventListener("toggle", () => { if ($("history").open) loadHistory(); });
$("history-prev").addEventListener("click", () => { state.historyOffset = Math.max(0, state.historyOffset - 10); loadHistory(); });
$("history-next").addEventListener("click", () => { if (state.historyNext !== null) { state.historyOffset = state.historyNext; loadHistory(); } });
$("history-refresh").addEventListener("click", loadHistory);
$("cancel-inspection").addEventListener("click", async () => {
  const button = $("cancel-inspection"); button.disabled = true;
  try { await api(`/api/jobs/${state.current}/cancel`, {method: "POST"}); await refresh(); }
  catch (error) { notice(errorMessage(error)); }
  finally { button.disabled = false; }
});

let sessionState = null, sessionBusy = false, sessionActionBusy = false, sessionTimer = null, sessionRevision = null;
const sessionMessages = {
  disconnected: "Mode tanpa akun. Tempel tautan film sebelum menghubungkan sesi.",
  connecting: "Menunggu login. Setelah login di profil pilihan, kembali ke tab ini; sesi diperiksa otomatis hingga dua menit.",
  connected: "Akun diterima sumber dan format media siap dipilih.",
  disconnecting: "Menghentikan pekerjaan terkait sebelum membuang salinan sesi…",
  expired: "Sesi kedaluwarsa atau ditolak. Periksa akses di situs lalu coba lagi.",
  error: "Sesi belum siap. Periksa petunjuk berikut.",
};
function renderSession(data) {
  const changed = sessionRevision !== null && sessionRevision !== data.revision;
  sessionState = data; sessionRevision = data.revision;
  const locked = data.state !== "disconnected";
  document.querySelector(".mode").textContent = data.state === "connected" ? "SESI AKUN TERHUBUNG" : "TANPA AKUN";
  const selected = $("session-profile").value || data.profile_id;
  $("session-profile").replaceChildren(...data.profiles.map((profile) => {
    const option = document.createElement("option"); option.value = profile.id; option.textContent = profile.label; return option;
  }));
  if (data.profiles.some((profile) => profile.id === selected)) $("session-profile").value = selected;
  $("session-profile").disabled = locked;
  $("session-consent").disabled = locked;
  if (locked) $("session-consent").checked = true;
  $("session-no-profiles").hidden = data.profiles.length > 0;
  $("session-connect").hidden = locked;
  $("session-connect").disabled = !data.profiles.length || !$("session-consent").checked;
  $("session-disconnect").hidden = !locked;
  $("session-disconnect").disabled = data.state === "disconnecting";
  $("session-retry").hidden = !["error", "expired"].includes(data.state);
  $("session-badge").textContent = data.state === "connected" ? "Terhubung" : data.account_verified ? "Akun dikenali" : data.state === "connecting" ? "Menunggu login" : data.state === "expired" ? "Sesi kedaluwarsa" : data.state === "error" ? "Koneksi bermasalah" : "Tanpa koneksi akun";
  const messages = [sessionMessages[data.state]];
  if (data.account_verified && !data.media_ready) messages.push("Akun dikenali; akses media belum siap.");
  if (data.error) messages.push(data.error.message, data.error.action);
  $("session-status").textContent = messages.filter(Boolean).join(" ");
  $("session-manual").hidden = !data.manual_login || !locked || ["connected","disconnecting"].includes(data.state);
  if (data.login_url) $("session-login-link").href = data.login_url;
  if (changed && state.current) {
    state.signature = ""; $("selection").hidden = true;
    if (data.state === "connected") {
      const previous = state.current;
      const revision = data.revision;
      api(`/api/inspections/${previous}/refresh`, {method:"POST",key:crypto.randomUUID()})
        .then((result) => {
          if (state.current !== previous || sessionRevision !== revision) return;
          state.current = result.id;
          safeStore.set("idlixport.inspection", result.id);
          refresh();
        })
        .catch((error) => notice(errorMessage(error)));
    } else { refresh(); }
  }
}
async function sessionRefresh(focused = false) {
  if (sessionBusy || sessionActionBusy) return;
  sessionBusy = true; clearTimeout(sessionTimer);
  try {
    const data = await api(focused && sessionState?.state === "connecting" ? "/api/browser-session/probe" : "/api/browser-session", {
      method:focused && sessionState?.state === "connecting" ? "POST" : "GET",
    });
    renderSession(data);
  } catch (error) { $("session-status").textContent = errorMessage(error); }
  finally {
    sessionBusy = false;
    sessionTimer = setTimeout(sessionRefresh, sessionState?.state === "connecting" ? 5000 : 15000);
  }
}
async function sessionAction(operation) {
  if (sessionActionBusy) return;
  sessionActionBusy = true;
  while (sessionBusy) await new Promise((resolve) => setTimeout(resolve, 25));
  sessionBusy = true; clearTimeout(sessionTimer); notice();
  ["session-connect","session-retry","session-disconnect"].forEach((id) => {$(id).disabled=true;});
  try {
    const options = {method:operation === "disconnect" ? "DELETE" : "POST"};
    if (operation === "connect") {
      const seriesEpisode = !$("series-selection").hidden && $("series-episodes").querySelector("input");
      const url = (seriesEpisode && (selectedEpisodes()[0] || seriesEpisode.value)) || $("source-url").value.trim() || $("batch-urls").value.split(/\r?\n/).map((line) => line.trim()).find(Boolean);
      if (!url) throw new Error("Tempel tautan film IDLIX terlebih dahulu.");
      options.body = {profile_id:$("session-profile").value,url,consent:$("session-consent").checked};
    }
    const data = await api(operation === "disconnect" ? "/api/browser-session" : `/api/browser-session/${operation}`, options);
    renderSession(data);
    if (operation === "disconnect") $("session-consent").checked=false;
  } catch (error) { notice(errorMessage(error)); }
  finally {
    sessionBusy=false; sessionActionBusy=false;
    ["session-connect","session-retry","session-disconnect"].forEach((id) => {$(id).disabled=false;});
    if (sessionState) renderSession(sessionState);
    sessionTimer=setTimeout(sessionRefresh,5000);
  }
}
$("show-session").addEventListener("click", () => {
  $("browser-session-panel").open = true;
  $("session-profile").focus();
});
$("session-consent").addEventListener("change", () => {$("session-connect").disabled=!$("session-consent").checked || !sessionState?.profiles.length;});
$("session-connect").addEventListener("click", () => sessionAction("connect"));
$("session-retry").addEventListener("click", () => sessionAction("retry"));
$("session-disconnect").addEventListener("click", () => sessionAction("disconnect"));
window.addEventListener("focus", () => sessionRefresh(true));
document.addEventListener("visibilitychange", () => {if (!document.hidden) sessionRefresh(true);});
sessionRefresh();

$("subtitle").addEventListener("change",()=>{$("subtitle-language").disabled=$("subtitle").value==="off";});
$("batch-subtitle").addEventListener("change",()=>{$("batch-language").disabled=["auto","off"].includes($("batch-subtitle").value);});
let batchKey=null,batchRequest=null;
$("batch-form").addEventListener("submit",async(event)=>{
  event.preventDefault();$("batch-submit").disabled=true;notice();
  try {
    const urls=$("batch-urls").value.split(/\r?\n/).map((line)=>line.trim()).filter(Boolean);
    if (!urls.length || urls.length>20) throw new Error("Masukkan 1–20 tautan, satu per baris.");
    const body={urls,policy:$("batch-policy").value,folder_id:$("batch-folder").value,subtitle_mode:$("batch-subtitle").value};
    if (["embed","separate"].includes(body.subtitle_mode)) body.subtitle_language=$("batch-language").value;
    const serialized=JSON.stringify(body);
    if (serialized!==batchRequest) {batchKey=crypto.randomUUID();batchRequest=serialized;}
    const result=await api("/api/batches",{method:"POST",body,key:batchKey});
    $("batch-status").textContent="Batch diterima. Proses tetap berjalan setelah tab ditutup.";
    await refresh();
  } catch(error) {notice(errorMessage(error));}
  finally {$("batch-submit").disabled=false;}
});
const batchCards=new Map();
function renderBatches(batches) {
  for (const batch of batches) {
    const signature=JSON.stringify(batch);
    if (batchCards.get(batch.id)?.signature===signature) continue;
    const stored=batchCards.get(batch.id);
    const card=element("article","card batch-summary");card.dataset.batchId=batch.id;
    card.append(element("h3","",`Batch · ${batch.total} film`));
    const counts=batch.counts;
    card.append(element("p","field-hint",`${statuses[batch.state] || "Memproses"} · ${counts.completed||0} selesai · ${counts.partial||0} parsial · ${counts.failed||0} gagal · ${counts.cancelled||0} dibatalkan`));
    const actions=element("div","job-actions");
    const detail=element("button","button secondary batch-detail","Lihat film");detail.type="button";
    const content=stored?.content || element("div","batch-children");if (!stored) content.hidden=true;
    if (!content.hidden) detail.textContent="Muat ulang film";
    detail.addEventListener("click",async()=>{
      detail.disabled=true;
      try {const data=await api(`/api/batches/${batch.id}`);content.replaceChildren(...data.jobs.map(jobCard));content.hidden=false;detail.textContent="Muat ulang film";}
      catch(error){notice(errorMessage(error));}finally{detail.disabled=false;}
    });actions.append(detail);
    if (["queued","running"].includes(batch.state)) {
      const cancel=element("button","button secondary cancel-batch","Batalkan batch");cancel.type="button";
      cancel.addEventListener("click",async()=>{await action(cancel,`/api/batches/${batch.id}/cancel`);refresh();});actions.append(cancel);
    }
    card.append(actions,content);
    if (stored) stored.node.replaceWith(card);else $("batch-results").prepend(card);
    batchCards.set(batch.id,{signature,node:card,content});
  }
}

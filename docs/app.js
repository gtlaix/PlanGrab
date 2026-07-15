// PlanGrab frontend — drives the JSON contract documented in web/app.py.
"use strict";

const $ = (id) => document.getElementById(id);
let docs = []; // last discovered documents, in order

// --- Talking to the local helper -------------------------------------------
// This same file backs two deployments:
//   * the helper's OWN UI, served from http://127.0.0.1:<port> (same-origin), and
//   * the hosted UI on GitHub Pages, which must reach the helper cross-origin.
// Discovery tries the page's own origin first (covers the helper's own UI, even
// on a fallback port); if that isn't the helper, it probes the known candidate
// ports for /api/ping. API_BASE is set to whatever answers ("" means relative /
// same-origin). Every request goes through api() below.
const LOCAL_PORTS = [8756, 8757, 8758, 8759, 8760];
let API_BASE = "";

const api = (path, opts) => fetch(API_BASE + path, opts);

// Is `base` a live PlanGrab helper? ("" = the page's own origin.)
async function isHelper(base) {
  try {
    const resp = await fetch(base + "/api/ping", { signal: AbortSignal.timeout(1500) });
    if (!resp.ok) return false;
    const data = await resp.json();
    return !!data && data.app === "plangrab";
  } catch {
    return false; // refused / timed out / blocked by CORS — not the helper here
  }
}

// The helper only ever serves from localhost, so a same-origin probe is only
// worth making there (it also covers the helper binding a fallback port). On a
// hosted origin we skip it to avoid a pointless 404 in the console.
const onLocalhost = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(location.origin);

// Locate the helper, set API_BASE, and report how it was found:
//   "same-origin" -> the helper is serving this page (its own UI)
//   "remote"      -> found on a probed localhost port (hosted UI)
//   null          -> no helper running
async function discoverHelper() {
  if (onLocalhost && await isHelper("")) { API_BASE = ""; return "same-origin"; }
  for (const port of LOCAL_PORTS) {
    const base = `http://127.0.0.1:${port}`;
    if (await isHelper(base)) { API_BASE = base; return "remote"; }
  }
  return null;
}

function setStatus(el, msg, isError = false) {
  el.textContent = msg || "";
  el.classList.toggle("error", !!isError);
}

async function discover() {
  const url = $("url").value.trim();
  if (!url) return;
  $("discover").disabled = true;
  setStatus($("discover-status"), "Discovering…");
  try {
    const resp = await api("/api/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus($("discover-status"), data.error || "Discovery failed.", true);
      return;
    }
    docs = data.documents;
    renderResults(data);
    setStatus($("discover-status"), "");
  } catch (e) {
    setStatus($("discover-status"), String(e), true);
  } finally {
    $("discover").disabled = false;
  }
}

// Council name -> portal base URL, for the reference-search picker.
const councilBaseUrl = new Map();

async function loadCouncils() {
  try {
    const resp = await api("/api/councils");
    const data = await resp.json();
    const list = $("council-list");
    list.innerHTML = "";
    for (const c of data.councils) {
      if (!c.supports_reference) continue; // only councils we can search by ref
      councilBaseUrl.set(c.name, c.base_url);
      const opt = document.createElement("option");
      opt.value = c.name;
      list.appendChild(opt);
    }
  } catch (e) {
    // Non-fatal: the URL box still works without the picker.
    setStatus($("find-status"), "Couldn't load the council list — paste a URL instead.");
  }
}

async function findByReference() {
  const councilName = $("ref-council").value.trim();
  const reference = $("ref-number").value.trim();
  if (!reference) {
    setStatus($("find-status"), "Enter an application reference.", true);
    return;
  }
  const base = councilBaseUrl.get(councilName);
  if (!base) {
    setStatus($("find-status"), "Pick a council from the list first.", true);
    return;
  }
  $("find-ref").disabled = true;
  setStatus($("find-status"), "Searching the portal…");
  try {
    const resp = await api("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ council: base, reference }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus($("find-status"), data.error || "Couldn't find that application.", true);
      return;
    }
    // Hand off to the existing discover flow: fill the URL box and run it.
    $("url").value = data.url;
    setStatus($("find-status"), `Found ${data.reference} — loading documents…`);
    await discover();
    setStatus($("find-status"), "");
  } catch (e) {
    setStatus($("find-status"), String(e), true);
  } finally {
    $("find-ref").disabled = false;
  }
}

function renderResults(data) {
  $("lpa").textContent = data.lpa;
  $("system").textContent = data.system;
  $("count").textContent = `${data.count} document${data.count === 1 ? "" : "s"}`;
  const list = $("doc-list");
  list.innerHTML = "";
  for (const d of data.documents) {
    const li = document.createElement("li");
    li.dataset.index = d.index;
    const meta = [d.date, d.doc_type].filter(Boolean).join(" · ");
    li.innerHTML =
      `<span class="idx">${d.index}/${d.total}</span>` +
      `<span><div class="title"></div><div class="meta"></div></span>` +
      `<span class="tag pending">pending</span>`;
    li.querySelector(".title").textContent = d.title;
    li.querySelector(".meta").textContent = meta;
    list.appendChild(li);
  }
  $("order-note").classList.toggle("hidden", data.count === 0);
  $("progress").classList.add("hidden");   // reset any bar from a previous download
  $("results").classList.remove("hidden");
  $("download").disabled = data.count === 0;
}

function setTag(index, status) {
  const li = document.querySelector(`#doc-list li[data-index="${index}"]`);
  if (!li) return;
  const tag = li.querySelector(".tag");
  tag.className = `tag ${status}`;
  tag.textContent = status;
}

async function browse() {
  setStatus($("folder-note"), "Opening folder picker…");
  try {
    const resp = await api("/api/pick-folder");
    const data = await resp.json();
    if (data.path) {
      $("folder").value = data.path;
      setStatus($("folder-note"), "");
    } else {
      setStatus($("folder-note"), data.error || "No folder chosen — you can paste a path instead.");
    }
  } catch (e) {
    setStatus($("folder-note"), "Picker unavailable — paste a path instead.", true);
  }
}

async function download() {
  const url = $("url").value.trim();
  const folder = $("folder").value.trim();
  if (!folder) {
    setStatus($("download-status"), "Choose a folder first.", true);
    return;
  }
  $("download").disabled = true;
  document.querySelectorAll("#doc-list .tag").forEach((t) => {
    t.className = "tag pending";
    t.textContent = "pending";
  });
  setStatus($("download-status"), "Starting…");
  progressDone = 0;
  $("progress-bar").style.width = "0%";
  $("progress-bar").classList.remove("has-failures");
  $("progress").classList.remove("hidden");

  try {
    const resp = await api("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, folder }),
    });
    // Stream NDJSON: one JSON object per line.
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (e) {
    setStatus($("download-status"), String(e), true);
  } finally {
    $("download").disabled = false;
  }
}

let progressDone = 0;

function handleEvent(ev) {
  switch (ev.type) {
    case "discovered":
      setStatus($("download-status"), `Downloading ${ev.count} files…`);
      break;
    case "file":
      setTag(ev.index, ev.status);
      progressDone += 1;
      if (ev.total) $("progress-bar").style.width = `${Math.round((progressDone / ev.total) * 100)}%`;
      if (ev.status === "failed") $("progress-bar").classList.add("has-failures");
      setStatus($("download-status"), `(${progressDone}/${ev.total}) ${ev.title}`);
      break;
    case "done": {
      const s = ev.summary;
      $("progress-bar").style.width = "100%";
      if (s.failed > 0) $("progress-bar").classList.add("has-failures");
      setStatus($("download-status"),
        `Done — ${s.downloaded} downloaded, ${s.skipped} skipped, ${s.failed} failed. Saved to ${s.folder}`);
      break;
    }
    case "error":
      setStatus($("download-status"), ev.message, true);
      break;
  }
}

// --- Helper connection (hosted UI) -----------------------------------------
// On the local helper's own UI the app is always available. On GitHub Pages we
// gate the form behind a live connection to the helper and onboard first-timers.
function setConnected(ok) {
  const banner = $("helper-status");
  const onboarding = $("helper-onboarding");
  const appBody = $("app-body");
  if (banner) {
    banner.classList.remove("hidden");
    banner.classList.toggle("connected", ok);
    banner.classList.toggle("disconnected", !ok);
    const msg = banner.querySelector(".helper-msg");
    if (msg) msg.textContent = ok
      ? "Connected to PlanGrab on this computer — downloads run from your own IP."
      : "PlanGrab isn't running on this computer yet.";
  }
  if (onboarding) onboarding.classList.toggle("hidden", ok);
  if (appBody) appBody.classList.toggle("hidden", !ok);
}

let retryTimer = null;
function stopAutoRetry() {
  if (retryTimer) { clearInterval(retryTimer); retryTimer = null; }
}

async function checkConnection() {
  const mode = await discoverHelper();
  const connected = mode !== null;
  const banner = $("helper-status");
  if (mode === "same-origin") {
    // The helper is serving this very page — no banner/onboarding needed.
    if (banner) banner.classList.add("hidden");
    if ($("helper-onboarding")) $("helper-onboarding").classList.add("hidden");
  } else {
    setConnected(connected);  // hosted UI: show live status + onboarding
  }
  if (connected) {
    stopAutoRetry();
    $("app-body").classList.remove("hidden");
    loadCouncils();
  }
  return connected;
}

async function initConnection() {
  const ok = await checkConnection();
  if (!ok && !retryTimer) retryTimer = setInterval(checkConnection, 3000);
}

$("discover").addEventListener("click", discover);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") discover(); });
$("find-ref").addEventListener("click", findByReference);
$("ref-number").addEventListener("keydown", (e) => { if (e.key === "Enter") findByReference(); });
$("ref-council").addEventListener("keydown", (e) => { if (e.key === "Enter") $("ref-number").focus(); });
$("browse").addEventListener("click", browse);
$("download").addEventListener("click", download);
const _retryBtn = $("helper-retry");
if (_retryBtn) _retryBtn.addEventListener("click", checkConnection);

initConnection();

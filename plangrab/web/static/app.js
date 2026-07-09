// PlanGrab frontend — drives the JSON contract documented in web/app.py.
"use strict";

const $ = (id) => document.getElementById(id);
let docs = []; // last discovered documents, in order

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
    const resp = await fetch("/api/discover", {
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
    const resp = await fetch("/api/councils");
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
    const resp = await fetch("/api/resolve", {
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
    const resp = await fetch("/api/pick-folder");
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
    const resp = await fetch("/api/download", {
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

$("discover").addEventListener("click", discover);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") discover(); });
$("find-ref").addEventListener("click", findByReference);
$("ref-number").addEventListener("keydown", (e) => { if (e.key === "Enter") findByReference(); });
$("ref-council").addEventListener("keydown", (e) => { if (e.key === "Enter") $("ref-number").focus(); });
$("browse").addEventListener("click", browse);
$("download").addEventListener("click", download);

loadCouncils();

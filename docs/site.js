// PlanGrab compatibility dashboard. Reads /api/compat; optional re-run button.
"use strict";

const $ = (id) => document.getElementById(id);
const ATTENTION = new Set(["stale_example", "parse_error", "no_documents", "auth_or_terms", "unsupported"]);
const BAD = new Set(["parse_error", "network_error"]);

let allRows = [];
let sortKey = "lpa_name";
let sortDir = 1;

async function load() {
  const data = await (await fetch("compat.json")).json();
  allRows = data.rows;
  renderCoverage(data.summary);
  populateFilters(data.summary);
  render();
}

const SVGNS = "http://www.w3.org/2000/svg";

async function loadMap() {
  let data;
  try {
    data = await (await fetch("coverage-map.json")).json();
  } catch (e) { return; }
  const c = data.counts || {};
  const ks = data.known_systems || {};
  const ksText = Object.entries(ks).sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `${sysLabel(s)} ${n}`).join(" · ");
  $("map-systems").innerHTML =
    `<b>${c.ok || 0}</b> work · <b>${c.addable || 0}</b> on supported systems, ready to add · ` +
    `<b>${c.known || 0}</b> on other systems` + (ksText ? ` (${ksText})` : "") +
    ` · <b>${c.unknown || 0}</b> unknown`;

  const svg = $("map");
  svg.setAttribute("viewBox", data.viewBox);
  svg.innerHTML = "";
  for (const f of data.features) {
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", f.d);
    p.setAttribute("fill-rule", "evenodd");
    p.setAttribute("class", f.category);
    p.addEventListener("mousemove", (e) => showTip(e, f));
    p.addEventListener("mouseleave", hideTip);
    p.addEventListener("click", () => {
      $("search").value = f.name;
      render();
      document.getElementById("grid").scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    svg.appendChild(p);
  }
}

const SYSTEM_LABELS = { idox: "IDOX", northgate: "Northgate / NEC", civica: "Civica",
  civica_w2: "Civica W2",
  swiftlg: "SwiftLG", ocella2: "Ocella", appsearchserv: "AppSearch", fastweb: "Fastweb",
  acolnet: "AcolNet", custom: "bespoke", annuallist: "list-only" };
function sysLabel(s) { return SYSTEM_LABELS[s] || (s ? s[0].toUpperCase() + s.slice(1) : "?"); }

function showTip(e, f) {
  const tip = $("map-tip");
  const docs = f.doc_count != null ? ` · ${f.doc_count} docs` : "";
  const label = f.category === "ok" ? "works"
    : f.category === "fail" ? f.status.replace(/_/g, " ")
    : f.category === "addable" ? (f.system === "idox"
        ? "IDOX — ready to add (run the harvester)"
        : `${sysLabel(f.system)} — supported, paste a documents URL to add`)
    : f.category === "known" ? `${sysLabel(f.system)} — not yet supported`
    : "system unknown";
  tip.innerHTML = `<div><strong></strong></div><div class="t-status ${f.category}"></div>`;
  tip.querySelector("strong").textContent = f.name;
  tip.querySelector(".t-status").textContent = label + docs;
  const wrap = document.querySelector(".map-wrap").getBoundingClientRect();
  tip.style.left = Math.min(e.clientX - wrap.left + 12, wrap.width - 232) + "px";
  tip.style.top = (e.clientY - wrap.top + 12) + "px";
  tip.classList.remove("hidden");
}
function hideTip() { $("map-tip").classList.add("hidden"); }

function renderCoverage(s) {
  const systems = Object.entries(s.by_system)
    .sort((a, b) => b[1].total - a[1].total)
    .map(([name, d]) => {
      const issues = Object.entries(d.by_status)
        .filter(([k]) => k !== "ok")
        .map(([k, v]) => `${v} ${k}`)
        .join(", ");
      const tail = issues ? ` / ${issues}` : "";
      return `<span class="sys-chip"><b>${sysLabel(name)}</b> ${d.ok} ok${tail}</span>`;
    }).join("");

  const totalLpas = s.total_lpas || s.total;
  const coveredPct = s.covered_pct != null ? s.covered_pct : s.supported_pct;
  $("coverage").innerHTML =
    `<div class="big">${s.ok}/${totalLpas}<small> LPAs Covered</small></div>` +
    `<div class="big">${coveredPct}%<small> Covered</small></div>` +
    `<div class="systems">${systems}</div>`;
}

function populateFilters(s) {
  const sysSel = $("filter-system");
  const statSel = $("filter-status");
  if (sysSel.options.length > 1) return; // once
  Object.keys(s.by_system).sort().forEach((name) => {
    sysSel.add(new Option(sysLabel(name), name));
  });
  s.statuses.forEach((st) => {
    if ((s.by_status[st] || 0) > 0 || st === "ok") statSel.add(new Option(st, st));
  });
}

function render() {
  const q = $("search").value.trim().toLowerCase();
  const sys = $("filter-system").value;
  const stat = $("filter-status").value;

  let rows = allRows.filter((r) =>
    (!q || r.lpa_name.toLowerCase().includes(q)) &&
    (!sys || r.system === sys) &&
    (!stat || r.status === stat));

  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (x == null) x = ""; if (y == null) y = "";
    if (typeof x === "number" || typeof y === "number") return (Number(x) - Number(y)) * sortDir;
    return String(x).localeCompare(String(y)) * sortDir;
  });

  const tbody = $("rows");
  tbody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    if (ATTENTION.has(r.status)) tr.className = BAD.has(r.status) ? "attention bad" : "attention";
    const ex = r.example_application_url
      ? `<a href="${r.example_application_url}" target="_blank" rel="noopener">open ↗</a>` : "—";
    const msg = r.message ? `<div class="msg"></div>` : "";
    tr.innerHTML =
      `<td><div class="name"></div>${msg}</td>` +
      `<td><span class="system-tag">${sysLabel(r.system)}</span></td>` +
      `<td><span class="badge ${r.status}">${r.status.replace(/_/g, " ")}</span></td>` +
      `<td class="num">${r.doc_count ?? ""}</td>` +
      `<td>${r.last_checked ?? "—"}</td>` +
      `<td>${ex}</td>`;
    tr.querySelector(".name").textContent = r.lpa_name;
    if (r.message) tr.querySelector(".msg").textContent = r.message;
    tbody.appendChild(tr);
  }
  $("empty").classList.toggle("hidden", rows.length > 0);
}

function setSort(key) {
  if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.classList.toggle("sort-asc", th.dataset.sort === key && sortDir === 1);
    th.classList.toggle("sort-desc", th.dataset.sort === key && sortDir === -1);
  });
  render();
}

async function recheck() {
  const sys = $("filter-system").value;
  $("recheck").disabled = true;
  setStatus("Starting checks…");
  try {
    const resp = await fetch("/api/smoke-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system: sys || null, days: 0 }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", total = 0, done = 0;
    while (true) {
      const { value, done: fin } = await reader.read();
      if (fin) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "start") total = ev.total;
        else if (ev.type === "row") { done++; setStatus(`(${done}/${total}) ${ev.lpa_name} → ${ev.status}`); }
        else if (ev.type === "done") setStatus("Checks complete.");
        else if (ev.type === "error") setStatus("Error: " + ev.message, true);
      }
    }
    await load(); // refresh table + coverage
    await loadMap(); // recolour the map
  } catch (e) {
    setStatus(String(e), true);
  } finally {
    $("recheck").disabled = false;
  }
}

function setStatus(msg, err = false) {
  const el = $("recheck-status");
  el.textContent = msg;
  el.classList.toggle("error", err);
}

$("search").addEventListener("input", render);
$("filter-system").addEventListener("change", render);
$("filter-status").addEventListener("change", render);
$("recheck").addEventListener("click", recheck);
document.querySelectorAll("th[data-sort]").forEach((th) =>
  th.addEventListener("click", () => setSort(th.dataset.sort)));

load();
loadMap();

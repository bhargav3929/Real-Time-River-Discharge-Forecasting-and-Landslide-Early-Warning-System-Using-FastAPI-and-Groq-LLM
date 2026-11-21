"use strict";
/* All requests use relative URLs (api/...) so the app also works under a sub-path. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 1) => (n === null || n === undefined || Number.isNaN(n)) ? "—" :
  Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

const state = { info: null, samples: [], result: null, busy: false, activeSample: null };

/* ---------------- demo ticket (kept in sessionStorage, removed from the address bar) ---------------- */
function loadTicket() {
  let t = null;
  try {
    const u = new URL(location.href);
    const fromUrl = u.searchParams.get("ticket");
    if (fromUrl) {
      sessionStorage.setItem("lastsem_ticket", fromUrl);
      u.searchParams.delete("ticket");
      history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
    }
    t = sessionStorage.getItem("lastsem_ticket");
  } catch (e) { /* storage blocked: samples still work */ }
  return t;
}
const TICKET = loadTicket();

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (TICKET) headers["X-LastSem-Ticket"] = TICKET;
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (e) {
    throw { error: "Can't reach the app. Check that it is still running, then try again.", code: "NETWORK" };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw Object.assign({ error: "Something went wrong.", code: "UNKNOWN", status: res.status }, data || {});
  return data;
}

/* ---------------- banner ---------------- */
function showBanner(err) {
  const msg = typeof err === "string" ? err : (err.error || "Something went wrong.");
  $("#bannerText").textContent = msg;
  const code = err && err.code;
  $("#bannerAction").hidden = !(state.info && state.info.mode === "local" && (code === "AI_NOT_CONFIGURED" || code === "AI_KEY_INVALID"));
  $("#banner").hidden = false;
}
function hideBanner() { $("#banner").hidden = true; }

/* ---------------- quota pill ---------------- */
function setQuota(n) {
  if (typeof n !== "number") return;
  const total = (state.info && state.info.limits.hourly_scans) || 15;
  const pill = $("#quotaPill");
  pill.textContent = `${n}/${total} Free Demo Runs Left This Hour`;
  pill.classList.toggle("low", n <= 3);
}

/* ---------------- tabs ---------------- */
function showView(name) {
  $$(".tab").forEach((t) => {
    const on = t.dataset.view === name;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  if (name === "brief") renderBrief();
}

/* ---------------- severity helpers ---------------- */
function tierClass(tier) {
  return { Low: "sev-low", Moderate: "sev-mod", High: "sev-high", Severe: "sev-severe", "Very High": "sev-severe" }[tier] || "sev-mod";
}
function tierColor(tier) {
  return { Low: "#15803D", Moderate: "#D97706", High: "#C2410C", Severe: "#9A1B1B", "Very High": "#9A1B1B" }[tier] || "#D97706";
}

function gaugeSVG(score, tier) {
  const len = Math.PI * 80;
  const dash = (len * Math.max(0, Math.min(100, score))) / 100;
  return `<svg viewBox="0 0 180 104" role="img" aria-label="Score ${score} out of 100, ${esc(tier)}">
    <path d="M10 94 A80 80 0 0 1 170 94" fill="none" stroke="#E4EBF2" stroke-width="14" stroke-linecap="round"/>
    <path d="M10 94 A80 80 0 0 1 170 94" fill="none" stroke="${tierColor(tier)}" stroke-width="14" stroke-linecap="round"
      stroke-dasharray="${dash.toFixed(1)} ${len.toFixed(1)}"/>
  </svg>`;
}

/* ---------------- KPI header ---------------- */
function kpiHTML(r) {
  const f = r.flood, l = r.landslide;
  return `<div class="kpis">
    <div class="card gauge-card">${gaugeSVG(f.score, f.tier)}
      <div><h3>7-Day Flood Risk Score</h3>
        <div class="score num">${f.score}<small>/100</small></div>
        <span class="tier ${tierClass(f.tier)}">${esc(f.tier)} flood risk</span></div></div>
    <div class="card gauge-card">${gaugeSVG(l.score, l.tier)}
      <div><h3>Landslide Susceptibility</h3>
        <div class="score num">${l.score}<small>/100</small></div>
        <span class="tier ${tierClass(l.tier)}">${esc(l.tier)} · ${esc(l.terrain_class)}</span></div></div>
    <div class="card tile"><h3>Elevation</h3><div class="val">${fmt(r.location.elevation_m, 0)} <small>m</small></div>
      <div class="note num">${fmt(r.location.terrain.relief_m, 0)} m relief in 11 km</div></div>
    <div class="card tile"><h3>Peak River Discharge</h3>
      <div class="val">${f.discharge_available ? fmt(f.peak_discharge_m3s, 2) : "n/a"} <small>m³/s</small></div>
      <div class="note num">${f.discharge_available ? `${fmt(f.surge_ratio, 1)}× recent median · ${esc(f.peak_date)}` : "No river in this grid cell"}</div></div>
    <div class="card tile"><h3>7-Day Cumulative Rain</h3><div class="val">${fmt(r.rain.total_7d_mm, 1)} <small>mm</small></div>
      <div class="note num">${fmt(r.rain.max_daily_mm, 1)} mm wettest day · soil ${fmt(r.landslide.soil_moisture_peak, 3)} m³/m³</div></div>
  </div>`;
}

/* ---------------- SVG hydrograph + rainfall chart ---------------- */
function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}
function dayLabel(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function chartHTML(r, uid) {
  const s = r.series;
  const W = 720, H = 330, L = 58, R = 54, T = 28, B = 46;
  const pw = W - L - R, ph = H - T - B;
  const hasQ = r.flood.discharge_available;
  const qVals = s.map((d) => d.discharge_m3s).concat(s.map((d) => d.discharge_max_m3s)).filter((v) => v != null);
  const thr = r.flood.threshold_m3s;
  const qTop = niceMax(Math.max(...qVals, thr || 0, 0.1) * 1.08);
  const pTop = niceMax(Math.max(...s.map((d) => d.precip_mm), 10) * 1.1);
  const slot = pw / s.length;
  const cx = (i) => L + slot * i + slot / 2;
  const yq = (v) => T + ph - (v / qTop) * ph;
  const yp = (v) => T + ph - (v / pTop) * ph;

  let g = "";
  for (let k = 0; k <= 4; k++) {
    const y = T + (ph * k) / 4;
    g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#E4EBF2"/>`;
    g += `<text x="${L - 8}" y="${y + 4}" text-anchor="end">${fmt(qTop * (1 - k / 4), qTop < 5 ? 2 : 0)}</text>`;
    g += `<text x="${W - R + 8}" y="${y + 4}">${fmt(pTop * (1 - k / 4), 0)}</text>`;
  }
  const bars = s.map((d, i) => {
    const bw = Math.min(34, slot * 0.5);
    const y = yp(d.precip_mm);
    return `<rect x="${cx(i) - bw / 2}" y="${y}" width="${bw}" height="${Math.max(0, T + ph - y)}" rx="3" fill="#7FB8D6" opacity=".85"/>`;
  }).join("");
  const labels = s.map((d, i) => `<text x="${cx(i)}" y="${H - B + 18}" text-anchor="middle">${esc(dayLabel(d.date))}</text>`).join("");

  const line = (key, color, dash, w) => {
    const pts = s.map((d, i) => (d[key] == null ? null : [cx(i), yq(d[key])])).filter(Boolean);
    if (pts.length < 2) return "";
    return `<polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${color}" stroke-width="${w}" ${dash ? `stroke-dasharray="${dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`;
  };
  const dots = hasQ ? s.map((d, i) => d.discharge_m3s == null ? "" :
    `<circle cx="${cx(i)}" cy="${yq(d.discharge_m3s)}" r="4" fill="#fff" stroke="#0284C7" stroke-width="2.5"/>`).join("") : "";
  const thrLine = hasQ && thr ? `<line x1="${L}" x2="${W - R}" y1="${yq(thr)}" y2="${yq(thr)}" stroke="#C2410C" stroke-width="2" stroke-dasharray="7 5"/>
    <text x="${L + 6}" y="${yq(thr) - 6}" style="fill:#C2410C;font-weight:600">flood threshold ${fmt(thr, 2)} m³/s</text>` : "";
  const hits = s.map((d, i) => `<rect class="hit" data-i="${i}" x="${L + slot * i}" y="${T}" width="${slot}" height="${ph}" fill="transparent"/>`).join("");

  return `<div class="card chart-card">
    <h3>7-day river hydrograph and rainfall</h3>
    <div class="legend">
      <span><i style="border-color:#0284C7"></i>River discharge (m³/s)</span>
      <span><i style="border-color:#7DB7D9;border-top-style:dotted"></i>Upper ensemble member (m³/s)</span>
      <span><i style="border-color:#C2410C;border-top-style:dashed"></i>Historical flood threshold</span>
      <span><i class="bar"></i>Daily rainfall (mm, right axis)</span>
    </div>
    <div class="chart-wrap" id="${uid}">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Seven day river discharge line with rainfall bars and flood threshold">
        ${g}
        <text x="${L - 8}" y="${T - 6}" text-anchor="end">m³/s</text>
        <text x="${W - R + 8}" y="${T - 6}">mm</text>
        ${bars}${thrLine}${hasQ ? line("discharge_max_m3s", "#7DB7D9", "2 4", 2) + line("discharge_m3s", "#0284C7", "", 3) + dots : ""}
        ${labels}
        <line class="cursor" x1="0" x2="0" y1="${T}" y2="${T + ph}" stroke="#102A43" stroke-width="1" opacity="0"/>
        ${hits}
      </svg>
    </div>
    ${hasQ ? "" : '<p class="hint">No river channel was found in the discharge grid cell for this place, so only rainfall is plotted.</p>'}
    <p class="hint">Threshold: ${esc(r.flood.threshold_basis)}.</p>
  </div>`;
}

function bindChart(uid, r) {
  const wrap = document.getElementById(uid);
  if (!wrap) return;
  const tip = $("#tooltip");
  const svg = $("svg", wrap);
  const cursor = $(".cursor", svg);
  const show = (i, ev) => {
    const d = r.series[i];
    tip.innerHTML = `${esc(dayLabel(d.date))}<br>Discharge: ${d.discharge_m3s == null ? "n/a" : fmt(d.discharge_m3s, 2) + " m³/s"}` +
      `<br>Upper member: ${d.discharge_max_m3s == null ? "n/a" : fmt(d.discharge_max_m3s, 2) + " m³/s"}` +
      `<br>Rain: ${fmt(d.precip_mm, 1)} mm` + (d.precip_prob_pct != null ? ` (${d.precip_prob_pct}% chance)` : "") +
      `<br>Soil moisture: ${d.soil_moisture == null ? "n/a" : fmt(d.soil_moisture, 3) + " m³/m³"}`;
    tip.hidden = false;
    const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + "px";
    tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - 10) + "px";
    const hit = $(`.hit[data-i="${i}"]`, svg);
    const cxp = Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) / 2;
    cursor.setAttribute("x1", cxp); cursor.setAttribute("x2", cxp); cursor.setAttribute("opacity", ".35");
  };
  $$(".hit", svg).forEach((h) => {
    h.addEventListener("mousemove", (ev) => show(Number(h.dataset.i), ev));
    h.addEventListener("mouseleave", () => { tip.hidden = true; cursor.setAttribute("opacity", "0"); });
  });
}

/* ---------------- advisory cards ---------------- */
const ul = (items, cls = "") => `<ul class="${cls}">${(items || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;

function advisoryHTML(r) {
  const a = r.advisory;
  return `<div class="card summary">
      <h3>${esc(a.headline)}</h3>
      <p><span class="lbl">Flood: </span>${esc(a.flood_summary)}</p>
      <p><span class="lbl">Landslide: </span>${esc(a.landslide_summary)}</p>
    </div>
    <div class="cards">
      <div class="card action-card"><h3>Immediate 24-hour actions</h3>${ul(a.immediate_24h_actions)}</div>
      <div class="card action-card next"><h3>Next 48 to 72 hours</h3>${ul(a.next_48_72h_actions)}</div>
      <div class="card action-card agri"><h3>Agricultural and livestock protection</h3>${ul(a.agri_livestock_steps)}</div>
      <div class="card action-card evac"><h3>Evacuation readiness checklist</h3>${ul(a.evacuation_checklist, "check")}
        <div class="helplines">${(r.helplines || []).map((h) => `<div>${esc(h.name)}<b>${esc(h.number)}</b></div>`).join("")}</div></div>
    </div>
    ${a.confidence_note ? `<p class="meta-line">${esc(a.confidence_note)}</p>` : ""}`;
}

let chartSeq = 0;
function resultHTML(r, saved) {
  const uid = "chart" + (++chartSeq);
  const src = r.advisory_source === "groq" ? `Plan written by ${esc(r.model)}` : "Plan recorded without a live AI call";
  const html = `<div class="res-head">
      <div><h2>${esc(r.location.name)}, ${esc(r.location.admin1)}</h2>
        <div class="sub num">${fmt(r.location.latitude, 3)}°N, ${fmt(r.location.longitude, 3)}°E · ${esc(r.asset_profile)} · ${esc(r.forecast_period.start)} to ${esc(r.forecast_period.end)}</div></div>
      <span class="badge ${saved ? "" : "live"}">${saved ? "Saved sample result" : "Live result"}</span>
    </div>
    ${kpiHTML(r)}${chartHTML(r, uid)}${advisoryHTML(r)}
    <p class="meta-line">${src}. Generated ${esc(r.generated_at)}.</p>
    <p class="disclaimer">${esc(r.disclaimer)}</p>`;
  return { html, uid };
}

function renderResult(r, saved) {
  state.result = r;
  const { html, uid } = resultHTML(r, saved);
  $("#emptyState").hidden = true;
  $("#resultMount").innerHTML = html;
  bindChart(uid, r);
  if ($("#view-brief").classList.contains("active")) renderBrief();
}

/* ---------------- tracker ---------------- */
function resetTracker() {
  $$("#tracker li").forEach((li) => { li.className = ""; $("em", li).textContent = ""; });
}
function setStep(step, status, text) {
  const li = $(`#tracker li[data-step="${step}"]`);
  if (!li) return;
  li.className = status;
  if (text !== undefined) $("em", li).textContent = text;
}
function trackerFromAgents(agents) {
  resetTracker();
  (agents || []).forEach((a) => setStep(a.step, "done", a.summary));
}

/* ---------------- live analysis (streamed progress) ---------------- */
async function runAnalysis(location, profile) {
  if (state.busy) return;
  state.busy = true;
  hideBanner();
  $("#runBtn").disabled = true;
  $("#runBtn").textContent = "Analysing…";
  state.activeSample = null; $$(".chip").forEach((c) => c.classList.remove("active"));
  resetTracker();
  let running = 1;
  try {
    const headers = { "Content-Type": "application/json" };
    if (TICKET) headers["X-LastSem-Ticket"] = TICKET;
    let res;
    try {
      res = await fetch("api/analyze?stream=1", { method: "POST", headers, body: JSON.stringify({ location, profile }) });
    } catch (e) { throw { error: "Can't reach the app. Check that it is still running, then try again.", code: "NETWORK" }; }
    if (!res.ok) throw Object.assign({ error: "Something went wrong." }, await res.json().catch(() => ({})));
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "", final = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.event === "agent") {
          running = ev.step;
          setStep(ev.step, ev.status, ev.summary);
        } else if (ev.event === "result") final = ev.result;
        else if (ev.event === "error") throw ev;
      }
    }
    if (!final) throw { error: "The analysis ended without a result. Please try again.", code: "AI_FAILED" };
    trackerFromAgents(final.agents);
    renderResult(final, false);
    setQuota(final.scans_left);
  } catch (err) {
    setStep(running, "error");
    showBanner(err);
    if (typeof err.scans_left === "number") setQuota(err.scans_left);
    else refreshInfo();
  } finally {
    state.busy = false;
    $("#runBtn").disabled = false;
    $("#runBtn").textContent = "Run 7-day risk analysis";
  }
}

/* ---------------- samples ---------------- */
async function openSample(id) {
  hideBanner();
  try {
    const s = await api("api/samples/" + encodeURIComponent(id));
    $("#loc").value = s.input_text;
    if (s.asset_profile) $("#profile").value = s.asset_profile;
    state.activeSample = id;
    $$(".chip").forEach((c) => c.classList.toggle("active", c.dataset.id === id));
    if (s.result) { trackerFromAgents(s.result.agents); renderResult(s.result, true); }
    else showBanner("This sample has no saved result yet. Press Run to analyse it live.");
  } catch (err) { showBanner(err); }
}

/* ---------------- compare ---------------- */
function compareHTML(res) {
  const c = res.comparison;
  const word = (k) => (k === "A" ? res.a.location.name : k === "B" ? res.b.location.name : "Similar");
  const col = (r) => {
    const uid = "chart" + (++chartSeq);
    return { uid, html: `<div><h2 style="font-size:18px;margin-bottom:8px">${esc(r.location.name)}, ${esc(r.location.admin1)}</h2>${kpiHTML(r)}${chartHTML(r, uid)}</div>` };
  };
  const A = col(res.a), B = col(res.b);
  return { A, B, html: `<div class="card verdict"><h3>${esc(c.verdict)}</h3>
      <p><b>Higher flood risk:</b> ${esc(word(c.higher_flood_risk))} &nbsp; <b>Higher landslide risk:</b> ${esc(word(c.higher_landslide_risk))}</p>
      ${ul(c.key_differences)}<p><b>First step:</b> ${esc(c.recommendation)}</p>
      <p class="meta-line">Comparison written by ${esc(res.model)}.</p></div>
      <div class="cmp-grid">${A.html}${B.html}</div><p class="disclaimer">${esc(res.a.disclaimer)}</p>` };
}

async function runCompare(ev) {
  ev.preventDefault();
  if (state.busy) return;
  state.busy = true; hideBanner();
  $("#cmpBtn").disabled = true; $("#cmpStatus").textContent = "Resolving both basins and comparing… this can take 10 to 20 seconds.";
  try {
    const res = await api("api/compare", { method: "POST", body: JSON.stringify({
      location_a: $("#locA").value, location_b: $("#locB").value, profile: $("#profileCmp").value }) });
    const out = compareHTML(res);
    $("#compareMount").innerHTML = out.html;
    bindChart(out.A.uid, res.a); bindChart(out.B.uid, res.b);
    setQuota(res.scans_left);
  } catch (err) {
    showBanner(err);
    refreshInfo();
  } finally {
    state.busy = false; $("#cmpBtn").disabled = false; $("#cmpStatus").textContent = "";
  }
}

/* ---------------- printable brief ---------------- */
function renderBrief() {
  const m = $("#briefMount");
  const r = state.result;
  if (!r) { m.innerHTML = '<div class="empty"><h2>No analysis yet</h2><p>Run or open a sample in the Risk Assessment Workbench first.</p></div>'; return; }
  const a = r.advisory;
  m.innerHTML = `<article class="brief">
    <h2>Emergency Action Brief: ${esc(r.location.name)}, ${esc(r.location.admin1)}</h2>
    <div class="sub num">${esc(r.asset_profile)} · ${fmt(r.location.latitude, 3)}°N ${fmt(r.location.longitude, 3)}°E · forecast ${esc(r.forecast_period.start)} to ${esc(r.forecast_period.end)}</div>
    <div class="scores">
      <div>Flood risk<br><b>${r.flood.score}/100</b> ${esc(r.flood.tier)}</div>
      <div>Landslide<br><b>${r.landslide.score}/100</b> ${esc(r.landslide.tier)}</div>
      <div>Elevation<br><b>${fmt(r.location.elevation_m, 0)} m</b></div>
      <div>Peak discharge<br><b>${r.flood.discharge_available ? fmt(r.flood.peak_discharge_m3s, 2) + " m³/s" : "n/a"}</b></div>
      <div>7-day rain<br><b>${fmt(r.rain.total_7d_mm, 1)} mm</b></div>
    </div>
    <p><b>${esc(a.headline)}</b></p><p>${esc(a.flood_summary)}</p><p>${esc(a.landslide_summary)}</p>
    <h3>Immediate 24-hour actions</h3>${ul(a.immediate_24h_actions)}
    <h3>Next 48 to 72 hours</h3>${ul(a.next_48_72h_actions)}
    <h3>Agricultural and livestock protection</h3>${ul(a.agri_livestock_steps)}
    <h3>Evacuation readiness checklist</h3>${ul(a.evacuation_checklist, "check")}
    <h3>Emergency helplines</h3>${ul((r.helplines || []).map((h) => `${h.name}: ${h.number}`))}
    <p class="disclaimer">${esc(r.disclaimer)}</p></article>`;
}

/* ---------------- history drawer ---------------- */
async function openHistory() {
  $("#historyDrawer").hidden = false;
  const list = $("#historyList");
  list.innerHTML = "<li class='hint'>Loading…</li>";
  try {
    const { items } = await api("api/history");
    list.innerHTML = items.length ? items.map((h) =>
      `<li><button type="button" data-id="${h.id}"><b>${esc(h.title)}</b><small>${esc(h.kind)} · ${new Date(h.ts * 1000).toLocaleString("en-IN")}</small></button></li>`).join("")
      : "<li class='hint'>No analyses yet.</li>";
  } catch (err) { list.innerHTML = `<li class="hint">${esc(err.error)}</li>`; }
}
async function openHistoryItem(id) {
  try {
    const item = await api("api/history/" + id);
    $("#historyDrawer").hidden = true;
    if (item.kind === "compare") {
      showView("compare");
      const out = compareHTML(item.payload);
      $("#compareMount").innerHTML = out.html; bindChart(out.A.uid, item.payload.a); bindChart(out.B.uid, item.payload.b);
    } else {
      showView("workbench");
      $("#loc").value = item.payload.location.query;
      if (item.payload.asset_profile) $("#profile").value = item.payload.asset_profile;
      trackerFromAgents(item.payload.agents);
      renderResult(item.payload, false);
    }
  } catch (err) { showBanner(err); }
}

/* ---------------- API keys modal ---------------- */
async function loadKeyStatus() {
  const dot = $("#keysDot");
  try {
    const k = (await api("api/keys")).groq;
    $("#keysStatus").textContent = k.configured ? `Key ready (${k.source === "ui" ? "saved in the app" : "from .env"}): ${k.masked}` : "No key yet. Paste one below.";
    dot.className = "dot " + (k.configured ? "ok" : "bad");
    return k;
  } catch (err) { $("#keysStatus").textContent = err.error; return null; }
}
async function openKeys() {
  $("#keyMsg").textContent = ""; $("#keyInput").value = "";
  await loadKeyStatus();
  const d = $("#keysModal");
  if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
}
async function saveKey() {
  try {
    await api("api/keys", { method: "POST", body: JSON.stringify({ groq_api_key: $("#keyInput").value.trim() }) });
    $("#keyInput").value = ""; $("#keyMsg").textContent = "Saved.";
    await loadKeyStatus(); refreshInfo(); hideBanner();
  } catch (err) { $("#keyMsg").textContent = err.error; }
}
async function removeKey() {
  try {
    await api("api/keys/groq", { method: "DELETE" });
    $("#keyMsg").textContent = "Saved key removed."; await loadKeyStatus(); refreshInfo();
  } catch (err) { $("#keyMsg").textContent = err.error; }
}

/* ---------------- init ---------------- */
async function refreshInfo() {
  try {
    state.info = await api("api/info");
    setQuota(state.info.limits.scans_left);
    return state.info;
  } catch (err) { showBanner(err); return null; }
}

async function init() {
  const info = await refreshInfo();
  if (!info) return;
  document.title = "Flood and Landslide Risk Engine";
  const opts = (info.profiles || []).map((p) => `<option>${esc(p)}</option>`).join("");
  $("#profile").innerHTML = opts; $("#profileCmp").innerHTML = opts;

  if (info.mode === "demo") {
    $("#keysBtn").hidden = true;
    if (!info.demo || !info.demo.ticket_valid) {
      $("#banner").classList.add("info");
      showBanner("Sign in on lastsem.com to try your own input. The bundled samples work without it.");
    }
  } else {
    loadKeyStatus();
  }

  try {
    state.samples = (await api("api/samples")).samples;
    $("#chips").innerHTML = state.samples.map((s) =>
      `<button type="button" class="chip" data-id="${esc(s.id)}"><b>${esc(s.title)}</b><small>${esc(s.asset_profile)}</small></button>`).join("");
    const first = state.samples.find((s) => s.has_saved_result);
    if (first) openSample(first.id);
  } catch (err) { showBanner(err); }
}

$$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
$("#analyzeForm").addEventListener("submit", (e) => { e.preventDefault(); runAnalysis($("#loc").value, $("#profile").value); });
$("#compareForm").addEventListener("submit", runCompare);
$("#chips").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (b) openSample(b.dataset.id); });
$("#bannerClose").addEventListener("click", hideBanner);
$("#bannerAction").addEventListener("click", openKeys);
$("#historyBtn").addEventListener("click", openHistory);
$("#historyClose").addEventListener("click", () => { $("#historyDrawer").hidden = true; });
$("#historyList").addEventListener("click", (e) => { const b = e.target.closest("button[data-id]"); if (b) openHistoryItem(b.dataset.id); });
$("#historyClear").addEventListener("click", async () => { try { await api("api/history", { method: "DELETE" }); openHistory(); } catch (err) { showBanner(err); } });
$("#keysBtn").addEventListener("click", openKeys);
$("#keySave").addEventListener("click", saveKey);
$("#keysForm").addEventListener("submit", (e) => { e.preventDefault(); saveKey(); });
$("#keyRemove").addEventListener("click", removeKey);
$("#keyClose").addEventListener("click", () => $("#keysModal").close());
$("#printBtn").addEventListener("click", () => window.print());
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#historyDrawer").hidden = true; });

init();

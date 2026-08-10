"""Lazy localhost frontend. Zero dependencies beyond the stdlib.

Two pages, GitHub-styled:
  /          org-profile landing (brand header, pinned "listings" card)
  /listings  the board: sidebar filters, contribute banner, spotlights,
             search, label-pill rows expanding to qualifications

Routes:
  GET  /api/postings     JSON: all postings, newest first
  GET  /api/suggestions  JSON: recent suggestions with status
  POST /api/suggest      queue a suggestion {kind, value, company?, keywords?}
  POST /api/visit        record a page open {page}

NOTE: PAGE strings are plain Python strings. Backslash escapes inside
embedded JS must be double-escaped or avoided; a stray \\n kills the script.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .locations import countries_of
from .roles import classify_role
from .store import Store

BASE_CSS = """
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         margin: 0; background:#161616; color:#e6edf3; }
  a { color:#4493f8; text-decoration:none; } a:hover { text-decoration:underline; }
  .topbar { background:#2c090d; border-bottom:1px solid #4a1218; padding:10px 20px;
            display:flex; align-items:center; gap:14px; }
  .burger { color:#c9a0a4; border:1px solid #5a1a20; border-radius:6px; padding:2px 8px;
            font-size:15px; }
  .brand { font-size:15.5px; font-weight:600; color:#e6edf3; }
  .brand:hover { text-decoration:none; }
  .brand .ru { color:#f85149; }
  .crumb { color:#8b949e; font-weight:400; }
  .crumb a { color:#e6edf3; font-weight:600; }
  .topsearch { margin-left:auto; background:#1d0507; border:1px solid #363636; color:#8b949e;
               border-radius:6px; padding:4px 12px; font-size:13px; width:240px; }
"""

TOPBAR_HOME = """<div class="topbar">
  <span class="burger">&#9776;</span>
  <a class="brand" href="/"><span class="ru">RU</span>employed</a>
  <input class="topsearch" placeholder="Type / to search" disabled>
</div>"""

TOPBAR_LISTINGS = """<div class="topbar">
  <span class="burger">&#9776;</span>
  <span class="brand"><a class="brand" href="/"><span class="ru">RU</span>employed</a>
    <span class="crumb"> &middot; <a href="/listings" id="crumbname">Listings</a></span></span>
  <input class="topsearch" placeholder="Type / to search" disabled>
</div>"""

LANDING = """<!doctype html>
<meta charset="utf-8">
<title>RUemployed</title>
<style>""" + BASE_CSS + """
  .wrap { max-width:1010px; margin:0 auto; padding:0 20px; }
  .tabs { border-bottom:1px solid #2a2a2a; display:flex; gap:8px; padding:0 20px; }
  .tabs .tab { padding:10px 12px; color:#e6edf3; font-size:14px; border-bottom:2px solid transparent; }
  .tabs .tab.on { border-bottom-color:#f78166; font-weight:600; }
  .tabs .n { background:#363636; border-radius:2em; padding:0 8px; font-size:12px; color:#c9d1d9; }
  .profile { display:flex; gap:22px; align-items:flex-start; margin:34px 0 26px; }
  .avatar { width:76px; height:76px; border-radius:12px; background:#1e1e1e; border:1px solid #363636;
            display:flex; align-items:center; justify-content:center; font-size:26px; font-weight:700; }
  .avatar span { color:#f85149; }
  .pname { font-size:24px; font-weight:600; margin:0; }
  .verified { border:1px solid #3fb95055; color:#7ee787; border-radius:2em; padding:0 10px;
              font-size:12px; display:inline-block; margin-top:4px; }
  .pmeta { color:#8b949e; font-size:13.5px; margin-top:8px; }
  .notif { margin-left:auto; background:#2a2a2a; color:#e6edf3; border:1px solid #363636;
           border-radius:6px; padding:5px 16px; font-size:13px; font-weight:600; cursor:pointer; }
  .notif:hover { background:#363636; }
  h3.sec { font-size:16px; font-weight:400; color:#e6edf3; margin:8px 0 12px; }
  .pins { display:flex; gap:16px; }
  .pin.dim { opacity:.55; }
  .pin { width:32%; border:1px solid #363636; border-radius:8px; background:#161616;
         padding:16px 18px; }
  .pin .name { font-weight:600; }
  .pin .pub { border:1px solid #363636; color:#8b949e; border-radius:2em; padding:0 8px;
              font-size:12px; margin-left:6px; }
  .pin .desc { color:#8b949e; font-size:13px; margin:8px 0 14px; }
  .pin .foot { color:#8b949e; font-size:12.5px; display:flex; gap:14px; }
  .gdot { color:#3fb950; }
  .repolist { border:1px solid #363636; border-radius:8px; margin-bottom:40px; }
  .repo { border-top:1px solid #2a2a2a; padding:14px 18px; }
  .repo:first-child { border-top:none; }
  .repo.dim { opacity:.55; }
  .rname { font-weight:600; font-size:15px; }
  .rname.soon { color:#8b949e; }
  .pub { border:1px solid #363636; color:#8b949e; border-radius:2em; padding:0 8px;
         font-size:12px; margin-left:8px; }
  .rdesc { color:#8b949e; font-size:13px; margin:4px 0 8px; }
  .rfoot { color:#8b949e; font-size:12.5px; }
</style>
""" + TOPBAR_HOME + """
<div class="tabs">
  <span class="tab on">Overview</span>
  <span class="tab">Repositories <span class="n">7</span></span>
</div>
<div class="wrap">
  <div class="profile">
    <div class="avatar"><span>RU</span></div>
    <div>
      <p class="pname"><span style="color:#f85149">RU</span>employed</p>
      <span class="verified">Verified &middot; scarlet knights build here</span>
      <div class="pmeta">&#128101; Rutgers CS students &nbsp; &#128205; New Brunswick, NJ
        &nbsp; &#128279; <a href="/listings?repo=All+Listings&amp;degree=any&amp;country=all">/listings</a></div>
    </div>
    <button class="notif" onclick="this.textContent='coming soon'">Notification settings</button>
  </div>
  <h3 class="sec">Pinned</h3>
  <div class="pins">
    <div class="pin">
      <div><span>&#128214;</span> <a class="name" href="/listings?repo=All+Listings&amp;degree=any&amp;country=all">All Listings</a><span class="pub">Public</span></div>
      <div class="desc">What you're here for</div>
      <div class="foot"><span><span class="gdot">&#9679;</span> Internships</span>
        <span>&#9733; <span id="livecount">&hellip;</span> open</span>
        <span>updated continuously</span></div>
    </div>
    <div class="pin">
      <div><span>&#128214;</span> <a class="name" href="/listings?repo=%2727+Cycle&amp;degree=BS&amp;season=cycle:2027">'27 Cycle</a><span class="pub">Public</span></div>
      <div class="desc">Bachelors, every 2027 season</div>
      <div class="foot"><span><span class="gdot">&#9679;</span> internships</span>
        <span>&#9733; <span id="livecount27">&hellip;</span> open</span></div>
    </div>
    <div class="pin dim">
      <div><span>&#128214;</span> <span class="name" style="color:#8b949e">events</span><span class="pub">Coming soon</span></div>
      <div class="desc">Company interest meetings, hackathons, recruiting events. Soft
      tunnels into the hiring pipeline.</div>
      <div class="foot"><span><span class="gdot" style="color:#d29922">&#9679;</span> events</span></div>
    </div>
  </div>
  <h3 class="sec">Repositories</h3>
  <div class="repolist">
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Summer+%2727&amp;degree=BS&amp;season=Summer+2027">Summer '27</a><span class="pub">Public</span></div>
      <div class="rdesc">Undergraduate internships, Summer 2027</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Spring+%2727&amp;degree=BS&amp;season=Spring+2027">Spring '27</a><span class="pub">Public</span></div>
      <div class="rdesc">Undergraduate internships, Spring 2027</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Fall+%2726&amp;degree=BS&amp;season=Fall+2026">Fall '26</a><span class="pub">Public</span></div>
      <div class="rdesc">Undergraduate internships, Fall 2026</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Winter+%2727&amp;degree=BS&amp;season=Winter+2027">Winter '27</a><span class="pub">Public</span></div>
      <div class="rdesc">Undergraduate internships, Winter 2027</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Bachelors&amp;degree=BS">Bachelors</a><span class="pub">Public</span></div>
      <div class="rdesc">Everything open to undergraduates</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo">
      <div><a class="rname" href="/listings?repo=Masters%2FPhD&amp;degree=grad">Masters/PhD</a><span class="pub">Public</span></div>
      <div class="rdesc">Graduate-level internships</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> internships</div>
    </div>
    <div class="repo dim">
      <div><span class="rname soon">Summer '26</span><span class="pub">Closed</span></div>
      <div class="rdesc">Undergraduate internships, Summer 2026 (season over)</div>
      <div class="rfoot"><span class="gdot" style="color:#f85149">&#9679;</span> internships</div>
    </div>
    <div class="repo dim">
      <div><span class="rname soon">New Grad</span><span class="pub">Coming soon</span></div>
      <div class="rdesc">Full-time new grad roles</div>
      <div class="rfoot"><span class="gdot">&#9679;</span> newgrad</div>
    </div>
  </div>
</div>
<script>
fetch("/api/visit", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({page: "landing"})});
fetch("/api/postings").then(r => r.json()).then(d => {
  const isOpen = p => ["pending","gated","verified","published"].includes(p.status);
  document.getElementById("livecount").textContent = d.filter(isOpen).length;
  const deg = p => (p.verdict && (p.verdict.degree_levels || []).length
    ? p.verdict.degree_levels : (p.degree_levels || []));
  const sea = p => (p.verdict && p.verdict.season) || p.season || null;
  document.getElementById("livecount27").textContent = d.filter(p => isOpen(p)
    && (!deg(p).length || deg(p).includes("BS")) && (sea(p) || "").includes("2027")).length;
});
</script>
"""

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>RUemployed &middot; Listings</title>
<style>""" + BASE_CSS + """
  .layout { display:flex; gap:16px; max-width:1400px; margin:0 auto; padding:18px 20px 18px 8px; }
  .sidebar { width:34px; flex-shrink:0; display:flex; flex-direction:column;
             overflow:hidden auto; transition:width .15s ease;
             position:sticky; top:12px; align-self:flex-start;
             max-height:calc(100vh - 24px); }
  .sidebar:hover, .layout.pinned .sidebar, .layout.edge .sidebar { width:200px; }
  .sidebar .rail { position:absolute; top:6px; left:8px; color:#8b949e; font-size:15px; }
  .sidebar:hover .rail, .layout.pinned .rail, .layout.edge .rail { display:none; }
  .side-inner { width:200px; opacity:0; transition:opacity .15s ease; display:flex;
                flex-direction:column; flex:1; }
  .sidebar:hover .side-inner, .layout.pinned .side-inner, .layout.edge .side-inner { opacity:1; }
  .main { flex:1; min-width:0; }
  .side-section { margin-bottom:14px; }
  .side-head { color:#8b949e; font-size:11.5px; font-weight:600; text-transform:uppercase;
               letter-spacing:.4px; margin:0 0 4px 8px; }
  .side-select { width:100%; background:#1e1e1e; color:#e6edf3; border:1px solid #363636;
                 border-radius:6px; padding:4px 8px; font-size:13px; }
  .side-collapse { margin-top:auto; color:#8b949e; font-size:13px; cursor:pointer;
                   padding:6px 8px; border-radius:6px; }
  .side-collapse:hover { background:#1e1e1e; color:#e6edf3; }
  .banner { border:1px solid #363636; border-radius:8px; padding:12px 16px; margin-bottom:12px;
            background:#1e1e1e; }
  .banner h3 { margin:0 0 6px; font-size:15px; }
  .banner .muted { color:#8b949e; font-size:13px; }
  .banner input { background:#161616; color:#e6edf3; border:1px solid #363636;
                  border-radius:6px; padding:5px 10px; margin-right:6px; font-size:13px; }
  .banner button { background:#238636; color:#fff; border:1px solid #2ea04366; border-radius:6px;
                   padding:5px 14px; cursor:pointer; font-size:13px; font-weight:600; }
  .spotlights { display:flex; gap:12px; margin-bottom:12px; }
  .spot { flex:1; border:1px solid #363636; border-radius:8px; padding:10px 14px; background:#1e1e1e; }
  .spot .co { color:#8b949e; font-size:12px; }
  .spot .t { font-weight:600; }
  .searchrow { display:flex; gap:10px; margin-bottom:10px; }
  .searchrow input { flex:1; background:#161616; color:#e6edf3; border:1px solid #363636;
                     border-radius:6px; padding:7px 12px; font-size:14px; }
  .listhead { border:1px solid #363636; border-radius:8px 8px 0 0; background:#1e1e1e;
              padding:9px 16px; display:flex; gap:18px; align-items:center; font-size:13.5px; }
  .listhead .tab { cursor:pointer; color:#8b949e; font-weight:600; }
  .listhead .tab.on { color:#e6edf3; }
  .listhead .right { margin-left:auto; color:#8b949e; }
  .rows { border:1px solid #363636; border-top:none; border-radius:0 0 8px 8px; }
  .issue { border-top:1px solid #2a2a2a; padding:9px 16px; }
  .issue:first-child { border-top:none; }
  .issue summary { list-style:none; cursor:pointer; }
  .issue summary::-webkit-details-marker { display:none; }
  .l1 { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
  .dot { font-size:15px; line-height:1; position:relative; top:1px; }
  .open .dot { color:#3fb950; } .closed .dot { color:#f85149; } .pend .dot { color:#d29922; }
  .t { font-weight:600; font-size:15px; }
  .lbl { padding:0 9px; border-radius:2em; font-size:11.5px; font-weight:500; line-height:19px;
         display:inline-block; border:1px solid; }
  .cobox { background:#1f6feb1c; border:1px solid #1f6feb55; color:#79c0ff; font-weight:600;
           padding:2px 12px; border-radius:6px; font-size:12.5px; min-width:110px;
           text-align:center; flex-shrink:0; align-self:center; }
  .lbl.country { border-color:#3fb95055; color:#7ee787; background:#3fb9501a; }
  .lbl.season  { border-color:#d2992255; color:#e3b341; background:#d299221a; }
  .lbl.role    { border-color:#bc8cff55; color:#d2a8ff; background:#bc8cff1a; }
  .lbl.deg     { border-color:#4493f855; color:#79c0ff; background:#4493f81a; }
  .lbl.src     { border-color:#363636; color:#8b949e; background:transparent; }
  .posted { margin-left:auto; color:#8b949e; font-size:12.5px; white-space:nowrap; }
  .l2 { color:#8b949e; font-size:12.5px; margin-top:3px; padding-left:22px; }
  .body { margin:10px 0 4px 22px; color:#9da7b3; font-size:13px; border-left:2px solid #363636;
          padding-left:14px; white-space:pre-wrap; }
  .sugstat { color:#8b949e; font-size:12px; margin-top:8px; }
</style>
""" + TOPBAR_LISTINGS + """
<div class="layout" id="layout">
<div class="sidebar">
  <div class="rail">&#9881;</div>
  <div class="side-inner">
  <div class="side-section"><div class="side-head">Status</div>
    <select class="side-select" id="f-status" onchange="tab=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Degree</div>
    <select class="side-select" id="f-degree" onchange="degree=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Role</div>
    <select class="side-select" id="f-role" onchange="role=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Posted</div>
    <select class="side-select" id="f-fresh" onchange="fresh=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Country</div>
    <select class="side-select" id="f-country" onchange="country=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Season</div>
    <select class="side-select" id="f-season" onchange="season=this.value;render()"></select></div>
  <div class="side-collapse" id="pinbtn" onclick="togglePin()">&#128204; Pin sidebar open</div>
  </div>
</div>
<div class="main">
  <div class="banner">
    <h3>Want to contribute?</h3>
    <div class="muted">Know a posting we have not indexed, or a company we should watch?
    Paste a link or a company name. Every submission is validated before it goes live.</div>
    <div style="margin-top:8px">
      <input id="sugval" placeholder="posting URL or company name" size="38">
      <input id="sugkw" placeholder="keywords (optional)" size="20">
      <button onclick="suggest()">Submit</button>
      <span class="muted" id="sugmsg"></span>
    </div>
    <div class="sugstat" id="suglist"></div>
  </div>
  <div class="spotlights" id="spotlights"></div>
  <div class="searchrow">
    <input id="q" placeholder="Search title, company, qualifications" oninput="render()">
  </div>
  <div class="listhead">
    <span class="tab" data-tab="open" onclick="tab='open';render()">Open <span id="n-open"></span></span>
    <span class="tab" data-tab="closed" onclick="tab='closed';render()">Closed <span id="n-closed"></span></span>
    <span class="right" id="count"></span>
  </div>
  <div class="rows" id="rows"></div>
</div>
</div>
<script>
const OPEN = ["pending", "gated", "verified", "published"];
const DEGREES = [["any", "any"], ["BS", "BS"], ["MS", "MS"], ["PhD", "PhD"], ["grad", "MS/PhD"]];
const FRESH = [["all", Infinity], ["2h", 2], ["8h", 8], ["24h", 24], ["2d", 48], ["3d", 72], ["1w", 168]];
const PRESTIGE = ["jane street", "openai", "anthropic", "google", "apple", "nvidia", "stripe",
  "citadel", "hudson river trading", "two sigma", "palantir", "databricks", "microsoft", "meta"];
let tab = "open", degree = "BS", role = "all", fresh = "all",
    country = "United States", season = "all", data = [];

// Repo presets arrive as query params; they override the defaults above.
const params = new URLSearchParams(location.search);
if (params.get("status")) tab = params.get("status");
if (params.get("degree")) degree = params.get("degree");
if (params.get("role")) role = params.get("role");
if (params.get("fresh")) fresh = params.get("fresh");
if (params.get("country")) country = params.get("country");
if (params.get("season")) season = params.get("season");
if (params.get("repo")) {
  document.getElementById("crumbname").textContent = params.get("repo");
}

fetch("/api/visit", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({page: "listings:" + (params.get("repo") || "all")})});

function togglePin() {
  const on = document.getElementById("layout").classList.toggle("pinned");
  document.getElementById("pinbtn").innerHTML = on
    ? "&#128204; Unpin sidebar" : "&#128204; Pin sidebar open";
  localStorage.setItem("sbpin", on ? "1" : "");
}
if (localStorage.getItem("sbpin") === "1") togglePin();

// Sidebar must react even when the cursor sits in the far-left window gutter
// outside the centered layout.
document.addEventListener("mousemove", e => {
  const lay = document.getElementById("layout");
  if (e.clientX < 44) lay.classList.add("edge");
  else if (e.clientX > 300) lay.classList.remove("edge");
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/`/g, "&#96;");
}
function degreesOf(p) {
  const v = p.verdict && (p.verdict.degree_levels || []).length
    ? p.verdict.degree_levels : (p.degree_levels || []);
  return v;
}
function seasonOf(p) { return (p.verdict && p.verdict.season) || p.season || null; }
function hoursAgo(p) {
  return p.date_posted ? (Date.now() - new Date(p.date_posted).getTime()) / 3.6e6 : null;
}
function postedLabel(p) {
  const h = hoursAgo(p);
  if (h === null) return p.date_posted_text || "date unknown";
  if (h < 1) return "just now";
  if (h < 24) return Math.floor(h) + "h ago";
  if (h < 24 * 14) return Math.floor(h / 24) + "d ago";
  return p.date_posted.slice(0, 10);
}
function matches(p) {
  if (tab === "open" ? !OPEN.includes(p.status) : p.status !== "rejected") return false;
  const d = degreesOf(p);
  if (degree === "grad") {
    if (d.length && !d.includes("MS") && !d.includes("PhD")) return false;
  } else if (degree !== "any" && d.length && !d.includes(degree)) return false;
  if (role !== "all" && p.role !== role) return false;
  if (fresh !== "all") {
    const h = hoursAgo(p);
    if (h === null || h > FRESH.find(f => f[0] === fresh)[1]) return false;
  }
  if (country !== "all" && !(p.countries || []).includes(country)) return false;
  if (season !== "all") {
    const sv = seasonOf(p) || "";
    if (season.startsWith("cycle:")) {
      if (!sv.includes(season.slice(6))) return false;
    } else if (sv !== season) return false;
  }
  const q = document.getElementById("q").value.trim().toLowerCase();
  if (q) {
    const hay = (p.title + " " + p.company + " " + (p.qualifications || "")).toLowerCase();
    if (!q.split(/\\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}
function fillOpts(id, opts, cur) {
  const sel = document.getElementById(id);
  sel.innerHTML = opts.map(o => `<option value="${esc(o[0])}">${esc(o[1])}</option>`).join("");
  sel.value = cur;
  return sel.value;
}
function rowHtml(p) {
  const cls = p.status === "rejected" ? "closed" : (p.status === "pending" || p.status === "gated" ? "pend" : "open");
  const misc =
    `<span class="lbl role">${esc(p.role)}</span> `
    + `<span class="lbl deg">${degreesOf(p).join("/") || "any degree"}</span> `
    + (p.countries || []).slice(0, 3).map(c => `<span class="lbl country">${esc(c)}</span>`).join(" ") + " "
    + (seasonOf(p) ? `<span class="lbl season">${esc(seasonOf(p))}</span> ` : "")
    + p.sources.map(s => `<span class="lbl src">${esc(s)}</span>`).join(" ");
  const bodyParts = [];
  if (p.qualifications) bodyParts.push("<b>Qualifications</b><br>" + esc(p.qualifications));
  if (p.verdict) bodyParts.push("<b>Verifier</b><br>" + esc((p.verdict.reasons || []).join("; ") || "approved"));
  if (p.reject_reason) bodyParts.push("<b>Rejected</b><br>" + esc(p.reject_reason));
  bodyParts.push("<b>Locations</b><br>" + esc((p.locations || []).slice(0, 6).join("; ") || "unlisted"));
  return `<details class="issue ${cls}">
    <summary>
      <div class="l1">
        <span class="dot">&#9679;</span>
        <span class="cobox">${esc(p.company)}</span>
        <span class="t"><a href="${esc(p.canonical_url || p.url)}" target="_blank">${esc(p.title)}</a></span>
        <span class="posted">${postedLabel(p)}</span>
      </div>
      <div class="l2">${misc} &nbsp; ${p.status}</div>
    </summary>
    <div class="body">${bodyParts.join("<br><br>")}</div>
  </details>`;
}
function render() {
  const rows = data.filter(matches);
  const openN = data.filter(p => OPEN.includes(p.status)).length;
  document.getElementById("n-open").textContent = openN;
  document.getElementById("n-closed").textContent = data.length - openN;
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("on", t.dataset.tab === tab));
  document.getElementById("count").textContent = rows.length + " shown";
  document.getElementById("rows").innerHTML =
    rows.map(rowHtml).join("") || `<div class="issue" style="color:#8b949e">no matches</div>`;

  fillOpts("f-status", [["open", "open (" + openN + ")"],
    ["closed", "closed (" + (data.length - openN) + ")"]], tab);
  fillOpts("f-degree", DEGREES.map(([v, label]) => [v, v === "any" ? "any" : label + " (" + data.filter(p => {
    const dd = degreesOf(p);
    if (v === "grad") return !dd.length || dd.includes("MS") || dd.includes("PhD");
    return !dd.length || dd.includes(v);
  }).length + ")"]), degree);
  const roles = [...new Set(data.map(p => p.role))].sort();
  role = fillOpts("f-role", [["all", "all (" + data.length + ")"]].concat(
    roles.map(r => [r, r + " (" + data.filter(p => p.role === r).length + ")"])), role) || "all";
  fillOpts("f-fresh", FRESH.map(f => [f[0], f[0]]), fresh);
  const cs = [...new Set(data.flatMap(p => p.countries || []))].sort();
  country = fillOpts("f-country", [["all", "all"]].concat(cs.map(c => [c, c])),
    cs.includes(country) || country === "all" ? country : "all") || "all";
  const ss = [...new Set(data.map(seasonOf).filter(Boolean))].sort();
  const sOpts = [["all", "all"], ["cycle:2027", "2027 cycle"]].concat(ss.map(x => [x, x]));
  season = fillOpts("f-season", sOpts,
    sOpts.some(o => o[0] === season) ? season : "all") || "all";
  spotlight();
}
function spotlight() {
  const hits = data.filter(p => OPEN.includes(p.status)
    && PRESTIGE.includes(p.company.toLowerCase()))
    .sort((a, b) => (b.date_posted || "").localeCompare(a.date_posted || "")).slice(0, 2);
  document.getElementById("spotlights").innerHTML = hits.map(p => `
    <div class="spot"><div class="co">&#9733; spotlight &middot; ${esc(p.company)}</div>
    <div class="t"><a href="${esc(p.canonical_url || p.url)}" target="_blank">${esc(p.title)}</a></div>
    <div class="co">${postedLabel(p)}</div></div>`).join("");
}
async function suggest() {
  const value = document.getElementById("sugval").value.trim();
  if (!value) return;
  const keywords = document.getElementById("sugkw").value.trim();
  const kind = value.startsWith("http") ? "url" : "company";
  await fetch("/api/suggest", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({kind, value, keywords}),
  });
  document.getElementById("sugmsg").textContent = "queued; processed next cycle";
  document.getElementById("sugval").value = ""; document.getElementById("sugkw").value = "";
  loadSuggestions();
}
async function loadSuggestions() {
  const sugs = await (await fetch("/api/suggestions")).json();
  document.getElementById("suglist").innerHTML = sugs.slice(0, 5).map(x =>
    `[${esc(x.status)}] ${esc(x.value.slice(0, 55))}${x.result ? " &rarr; " + esc(x.result) : ""}`).join("<br>");
}
async function load() {
  data = await (await fetch("/api/postings")).json();
  render();
  loadSuggestions();
}
load(); setInterval(load, 30000);
</script>
"""


def make_handler(db_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self._send(200, LANDING.encode(), "text/html; charset=utf-8")
            elif self.path.split("?")[0] == "/listings":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/postings"):
                store = Store(db_path)  # per-request connection: thread-safe
                rows = []
                for p in store.all_postings():
                    d = p.model_dump(mode="json")
                    d["countries"] = countries_of(p.locations)
                    d["role"] = classify_role(p.title)
                    rows.append(d)
                body = json.dumps(rows).encode()
                self._send(200, body, "application/json")
            elif self.path.startswith("/api/suggestions"):
                store = Store(db_path)
                self._send(200, json.dumps(store.recent_suggestions()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/suggest":
                body = self._json_body()
                if body is None:
                    return
                kind = body.get("kind", "company")
                value = str(body.get("value", "")).strip()
                if kind not in ("url", "company") or not value:
                    self._send(400, b"bad request", "text/plain")
                    return
                store = Store(db_path)
                sid = store.add_suggestion(
                    kind, value,
                    company=body.get("company") or None,
                    keywords=body.get("keywords") or None,
                )
                self._send(200, json.dumps({"id": sid}).encode(), "application/json")
            elif self.path == "/api/visit":
                body = self._json_body()
                if body is None:
                    return
                page = str(body.get("page", "unknown"))[:40]
                Store(db_path).record_visit(page, self.headers.get("User-Agent", "")[:200])
                self._send(200, b"{}", "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def _json_body(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, b"bad request", "text/plain")
                return None

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    return Handler


class V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(db_path: Path, port: int = 8000):
    """Listen on both loopback families. Browsers resolve `localhost` to ::1
    on many systems; an IPv4-only bind looks like the site is down."""
    handler = make_handler(db_path)
    servers = []
    for cls, host in ((ThreadingHTTPServer, "127.0.0.1"), (V6Server, "::1")):
        try:
            servers.append(cls((host, port), handler))
        except OSError:
            continue  # family unavailable or already bound
    if not servers:
        raise SystemExit(f"port {port} unavailable on both loopback families")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"intake frontend: http://localhost:{port}")
    servers[0].serve_forever()

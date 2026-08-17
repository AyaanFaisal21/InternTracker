"""Lazy localhost frontend. Zero dependencies beyond the stdlib.

Two pages, GitHub-styled:
  /          org-profile landing (brand header, pinned "listings" card)
  /listings  the board: sidebar filters, contribute banner, spotlights,
             search, label-pill rows expanding to qualifications

Routes:
  GET  /api/postings     JSON: all postings, newest first
  GET  /api/suggestions  JSON: recent suggestions with status
  GET  /api/companies    JSON: companies with live postings + open counts
  GET  /api/verify       confirm an email subscription (link from the email)
  GET  /api/unsubscribe  one-click unsubscribe (link from the email)
  POST /api/suggest      queue a suggestion {kind, value, company?, keywords?}
  POST /api/visit        record a page open {page}
  POST /api/subscribe    create a notification subscription
                         {channel, target, filters?}, filters narrowing on
                         companies, degrees and countries
  POST /api/unsubscribe  deactivate a subscription {token}, or ?token= one-click

The two GET links are clicked out of an email, so they answer with a small
HTML page rather than JSON and take their token from the query string:
a mail client can send neither a JSON body nor a header.

NOTE: PAGE strings are plain Python strings. Backslash escapes inside
embedded JS must be double-escaped or avoided; a stray \\n kills the script.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import get_args
from urllib.parse import parse_qs, urlsplit

from .locations import countries_of, is_remote
from .notify import build_filters, company_key
from .resolve import COMPANY_MAX, CTRL_RE, clean_company, valid_company
from .roles import classify_role
from .schema import DegreeLevel, Status
from .senders import clean_email, email_sender, recipient_key, valid_email
from .store import open_store

log = logging.getLogger("intake")

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
  <a class="brand" href="/"><span class="ru">Short</span>list</a>
  <input class="topsearch" placeholder="Type / to search" disabled>
</div>"""

TOPBAR_LISTINGS = """<div class="topbar">
  <span class="burger">&#9776;</span>
  <span class="brand"><a class="brand" href="/"><span class="ru">Short</span>list</a>
    <span class="crumb"> &middot; <a href="/listings" id="crumbname">Listings</a></span></span>
  <input class="topsearch" placeholder="Type / to search" disabled>
</div>"""

LANDING = """<!doctype html>
<meta charset="utf-8">
<title>Shortlist</title>
<style>""" + BASE_CSS + """
  .tabs { border-bottom:1px solid #2a2a2a; display:flex; gap:4px; padding:0 24px; }
  .tabs .tab { padding:9px 12px 11px; color:#e6edf3; font-size:14px; border-bottom:2px solid transparent;
               display:flex; gap:7px; align-items:center; }
  .tabs .tab.on { border-bottom-color:#f78166; font-weight:600; }
  .tabs .tab.dim { color:#8b949e; }
  .tabs .glyph { color:#8b949e; }
  .tabs .n { background:#363636; border-radius:2em; padding:0 8px; font-size:12px; color:#c9d1d9; }
  .wrap { max-width:1280px; margin:0 auto; padding:0 32px; }
  .profile { display:flex; gap:24px; align-items:flex-start; margin:36px 0 24px; }
  .avatar { width:100px; height:100px; border-radius:14px; background:#1e1e1e; border:1px solid #363636;
            display:flex; align-items:center; justify-content:center; font-size:34px; font-weight:700; }
  .avatar span { color:#f85149; }
  .pname { font-size:26px; font-weight:600; margin:2px 0 6px; }
  .verified { border:1px solid #3fb95055; color:#7ee787; border-radius:2em; padding:1px 10px;
              font-size:12px; display:inline-block; }
  .pmeta { color:#8b949e; font-size:14px; margin-top:12px; display:flex; gap:18px; flex-wrap:wrap; }
  .notif { margin-left:auto; background:#21262d; color:#e6edf3; border:1px solid #363636;
           border-radius:6px; padding:5px 16px; font-size:13px; font-weight:600; cursor:pointer;
           align-self:flex-start; }
  .notif:hover { background:#30363d; }
  .cols { display:flex; gap:40px; align-items:flex-start; padding-bottom:60px; }
  .left { flex:1; min-width:0; }
  .rail { width:280px; flex-shrink:0; }
  h3.sec { font-size:16px; font-weight:400; color:#e6edf3; margin:6px 0 14px; }
  .pins { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:28px; }
  .pin { border:1px solid #363636; border-radius:8px; background:#161616; padding:14px 16px; }
  .pin.dim { opacity:.55; }
  .pin .name { font-weight:600; }
  .pub { border:1px solid #363636; color:#8b949e; border-radius:2em; padding:0 8px;
         font-size:12px; margin-left:7px; }
  .pin .desc { color:#8b949e; font-size:13px; margin:7px 0 16px; min-height:20px; }
  .foot { color:#8b949e; font-size:12.5px; display:flex; gap:16px; align-items:center; }
  .gdot { color:#3fb950; } .ydot { color:#d29922; } .rdot { color:#f85149; }
  .findrow { display:flex; gap:10px; margin-bottom:14px; }
  .findrow input { flex:1; background:#161616; color:#e6edf3; border:1px solid #363636;
                   border-radius:6px; padding:6px 12px; font-size:14px; }
  .ghostbtn { background:#21262d; color:#c9d1d9; border:1px solid #363636; border-radius:6px;
              padding:5px 14px; font-size:13px; }
  .repolist { border:1px solid #363636; border-radius:8px; }
  .repo { border-top:1px solid #2a2a2a; padding:16px 18px; display:flex; gap:16px; }
  .repo:first-child { border-top:none; }
  .repo.dim { opacity:.55; }
  .rmain { flex:1; min-width:0; }
  .rname { font-weight:600; font-size:16px; }
  .rname.soon { color:#8b949e; }
  .rdesc { color:#8b949e; font-size:13.5px; margin:5px 0 10px; }
  .spark { width:155px; height:30px; align-self:center; flex-shrink:0; }
  .spark polyline { fill:none; stroke:#f85149aa; stroke-width:1.6; }
  .railsec { margin-bottom:28px; }
  .railhead { font-size:15px; font-weight:600; margin-bottom:12px; }
  .people { display:grid; grid-template-columns:repeat(6, 1fr); gap:8px; }
  .person { aspect-ratio:1; border-radius:8px; background:#1e1e1e; border:1px solid #363636;
            display:flex; align-items:center; justify-content:center; font-size:12px;
            font-weight:700; color:#c9d1d9; }
  .viewall { font-size:13px; margin-top:10px; display:inline-block; }
  .langrow { display:flex; gap:8px; align-items:center; color:#c9d1d9; font-size:13.5px;
             margin-bottom:6px; }
  .topics { display:flex; flex-wrap:wrap; gap:7px; }
  .topic { background:#1f6feb26; color:#79c0ff; border-radius:2em; padding:2px 12px;
           font-size:12.5px; }
  .abuse { color:#8b949e; font-size:12.5px; margin-top:34px; }
</style>
""" + TOPBAR_HOME + """
<div class="tabs">
  <span class="tab on"><span class="glyph">&#9783;</span>Overview</span>
  <span class="tab"><span class="glyph">&#128214;</span>Repositories <span class="n">8</span></span>
  <span class="tab dim"><span class="glyph">&#9638;</span>Projects</span>
  <span class="tab dim"><span class="glyph">&#128101;</span>People</span>
</div>
<div class="wrap">
  <div class="profile">
    <div class="avatar"><span>SL</span></div>
    <div>
      <div class="pname"><span style="color:#f85149">Short</span>list</div>
      <span class="verified">Verified</span>
      <div class="pmeta">
        <span>&#128101; <b id="followers">&hellip;</b> postings tracked</span>
        <span>&#128205; New Brunswick, NJ</span>
        <span>&#128279; <a href="/listings?repo=All+Listings&amp;degree=any&amp;country=all">shortlist/listings</a></span>
      </div>
    </div>
    <button class="notif" onclick="this.textContent='coming soon'">Notification settings</button>
  </div>
  <div class="cols">
  <div class="left">
    <h3 class="sec">Pinned</h3>
    <div class="pins">
      <div class="pin">
        <div><span>&#128214;</span> <a class="name" href="/listings?repo=All+Listings&amp;degree=any&amp;country=all">All Listings</a><span class="pub">Public</span></div>
        <div class="desc">What you're here for</div>
        <div class="foot"><span><span class="gdot">&#9679;</span> internships</span>
          <span>&#9733; <b data-star="all">&hellip;</b></span>
          <span>&#8916; <b data-fork="all">&hellip;</b> companies</span></div>
      </div>
      <div class="pin">
        <div><span>&#128214;</span> <a class="name" href="/listings?repo=%2727+Cycle&amp;degree=BS&amp;season=cycle:2027">'27 Cycle</a><span class="pub">Public</span></div>
        <div class="desc">Bachelors, every 2027 season</div>
        <div class="foot"><span><span class="gdot">&#9679;</span> internships</span>
          <span>&#9733; <b data-star="cycle27">&hellip;</b></span>
          <span>&#8916; <b data-fork="cycle27">&hellip;</b> companies</span></div>
      </div>
      <div class="pin dim">
        <div><span>&#128214;</span> <span class="name" style="color:#8b949e">events</span><span class="pub">Coming soon</span></div>
        <div class="desc">Company interest meetings, hackathons, recruiting events.
        Soft tunnels into the hiring pipeline.</div>
        <div class="foot"><span><span class="ydot">&#9679;</span> events</span></div>
      </div>
    </div>
    <h3 class="sec">Repositories</h3>
    <div class="findrow">
      <input id="findrepo" placeholder="Find a repository..." oninput="filterRepos()">
      <span class="ghostbtn">Type &#9662;</span>
      <span class="ghostbtn">Language &#9662;</span>
      <span class="ghostbtn">Sort &#9662;</span>
    </div>
    <div class="repolist" id="repolist"></div>
    <div class="abuse">Report abuse</div>
  </div>
  <div class="rail">
    <div class="railsec">
      <div class="railhead">People</div>
      <div class="people" id="people"></div>
      <a class="viewall" href="/listings?repo=All+Listings&amp;degree=any&amp;country=all">View all</a>
    </div>
    <div class="railsec">
      <div class="railhead">Top languages</div>
      <div class="langrow"><span class="gdot">&#9679;</span> internships</div>
      <div class="langrow"><span class="ydot">&#9679;</span> events</div>
      <div class="langrow"><span class="rdot">&#9679;</span> newgrad</div>
    </div>
    <div class="railsec">
      <div class="railhead">Most used topics</div>
      <div class="topics" id="topics"></div>
    </div>
  </div>
  </div>
</div>
<script>
fetch("/api/visit", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({page: "landing"})});

const OPEN = ["pending", "gated", "verified", "published"];
const deg = p => (p.verdict && (p.verdict.degree_levels || []).length
  ? p.verdict.degree_levels : (p.degree_levels || []));
const sea = p => (p.verdict && p.verdict.season) || p.season || null;
const bs = p => !deg(p).length || deg(p).includes("BS");
const grad = p => !deg(p).length || deg(p).includes("MS") || deg(p).includes("PhD");

const REPOS = [
  {name: "Summer '27", desc: "Undergraduate internships, Summer 2027", lang: "internships",
   href: "/listings?repo=Summer+%2727&degree=BS&season=Summer+2027",
   pred: p => bs(p) && (!sea(p) || sea(p) === "Summer 2027")},
  {name: "Spring '27", desc: "Undergraduate internships, Spring 2027", lang: "internships",
   href: "/listings?repo=Spring+%2727&degree=BS&season=Spring+2027",
   pred: p => bs(p) && (!sea(p) || sea(p) === "Spring 2027")},
  {name: "Fall '26", desc: "Undergraduate internships, Fall 2026", lang: "internships",
   href: "/listings?repo=Fall+%2726&degree=BS&season=Fall+2026",
   pred: p => bs(p) && (!sea(p) || sea(p) === "Fall 2026")},
  {name: "Winter '27", desc: "Undergraduate internships, Winter 2027", lang: "internships",
   href: "/listings?repo=Winter+%2727&degree=BS&season=Winter+2027",
   pred: p => bs(p) && (!sea(p) || sea(p) === "Winter 2027")},
  {name: "Bachelors", desc: "Everything open to undergraduates", lang: "internships",
   href: "/listings?repo=Bachelors&degree=BS",
   pred: p => bs(p)},
  {name: "Masters/PhD", desc: "Graduate-level internships", lang: "internships",
   href: "/listings?repo=Masters%2FPhD&degree=grad",
   pred: p => grad(p)},
  {name: "Programs", desc: "Fellowships, research programs, scholarships. Underclassmen friendly.",
   lang: "programs",
   href: "/listings?repo=Programs&type=programs&degree=any&country=all",
   pred: p => (p.category || "internship") !== "internship"},
  {name: "Summer '26", desc: "Undergraduate internships, Summer 2026 (season over)",
   lang: "internships", closed: true, pred: () => false},
  {name: "New Grad", desc: "Full-time new grad roles", lang: "newgrad", soon: true,
   pred: () => false},
];

function spark(rows) {
  const days = new Array(21).fill(0);
  const now = Date.now();
  rows.forEach(p => {
    const t = p.date_posted || p.first_seen;
    if (!t) return;
    const d = Math.floor((now - new Date(t).getTime()) / 864e5);
    if (d >= 0 && d < 21) days[20 - d] += 1;
  });
  const max = Math.max(1, ...days);
  const pts = days.map((v, i) => (i * (155 / 20)).toFixed(1) + "," + (27 - (v / max) * 24).toFixed(1));
  return `<svg class="spark" viewBox="0 0 155 30"><polyline points="${pts.join(" ")}"/></svg>`;
}

let DATA = [];
function renderRepos() {
  const q = (document.getElementById("findrepo").value || "").toLowerCase();
  const openRows = DATA.filter(p => OPEN.includes(p.status));
  document.getElementById("repolist").innerHTML = REPOS
    .filter(r => r.name.toLowerCase().includes(q))
    .map(r => {
      const rows = openRows.filter(r.pred);
      const dim = r.closed || r.soon;
      const badge = r.closed ? "Closed" : (r.soon ? "Coming soon" : "Public");
      const nm = dim ? `<span class="rname soon">${r.name}</span>`
                     : `<a class="rname" href="${r.href}">${r.name}</a>`;
      const dot = r.closed ? "rdot" : (r.soon ? "ydot" : "gdot");
      const stats = dim ? "" :
        ` <span>&#9733; ${rows.length}</span><span>Updated today</span>`;
      return `<div class="repo${dim ? " dim" : ""}">
        <div class="rmain">
          <div>${nm}<span class="pub">${badge}</span></div>
          <div class="rdesc">${r.desc}</div>
          <div class="foot"><span><span class="${dot}">&#9679;</span> ${r.lang}</span>${stats}</div>
        </div>
        ${dim ? "" : spark(rows)}
      </div>`;
    }).join("");
}
function filterRepos() { renderRepos(); }

function hue(s) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
fetch("/api/postings").then(r => r.json()).then(d => {
  DATA = d;
  const open = d.filter(p => OPEN.includes(p.status));
  document.getElementById("followers").textContent = open.length;
  const set = (attr, rows) => {
    const el = document.querySelector(`[data-star="${attr}"]`);
    const fk = document.querySelector(`[data-fork="${attr}"]`);
    if (el) el.textContent = rows.length;
    if (fk) fk.textContent = new Set(rows.map(p => p.company)).size;
  };
  set("all", open);
  set("cycle27", open.filter(p => bs(p) && (!sea(p) || sea(p).includes("2027"))));
  const events = open.filter(p => p.category === "event");
  if (events.length) {
    document.getElementById("eventspin").classList.remove("dim");
    document.getElementById("eventsbadge").textContent = "Public";
    document.getElementById("eventscount").innerHTML = "&#9733; " + events.length + " upcoming";
    document.getElementById("eventsname").outerHTML =
      `<a class="name" href="/listings?repo=events&type=event&degree=any&country=all">events</a>`;
  }
  renderRepos();
  const byCo = {};
  open.forEach(p => byCo[p.company] = (byCo[p.company] || 0) + 1);
  const top = Object.entries(byCo).sort((a, b) => b[1] - a[1]).slice(0, 12);
  document.getElementById("people").innerHTML = top.map(([co]) => {
    const init = co.split(/\\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
    return `<div class="person" title="${co}" style="border-color:hsl(${hue(co)} 40% 35%);
      color:hsl(${hue(co)} 60% 75%)">${init}</div>`;
  }).join("");
  const roles = {};
  open.forEach(p => roles[p.role] = (roles[p.role] || 0) + 1);
  document.getElementById("topics").innerHTML = Object.entries(roles)
    .sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([r]) => `<a class="topic" href="/listings?repo=${encodeURIComponent(r)}&role=${encodeURIComponent(r)}&degree=any&country=all">${r.toLowerCase()}</a>`).join("");
});
</script>
"""

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Shortlist &middot; Listings</title>
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
  .lbl.aud     { border-color:#db61a255; color:#ff9bce; background:#db61a21a; }
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
  <div class="side-section"><div class="side-head">Type</div>
    <select class="side-select" id="f-type" onchange="ctype=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Audience</div>
    <select class="side-select" id="f-aud" onchange="aud=this.value;render()"></select></div>
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
    country = "United States", season = "all", ctype = "all", aud = "all", data = [];

// Repo presets arrive as query params; they override the defaults above.
const params = new URLSearchParams(location.search);
if (params.get("status")) tab = params.get("status");
if (params.get("degree")) degree = params.get("degree");
if (params.get("role")) role = params.get("role");
if (params.get("fresh")) fresh = params.get("fresh");
if (params.get("country")) country = params.get("country");
if (params.get("season")) season = params.get("season");
if (params.get("type")) ctype = params.get("type");
if (params.get("audience")) aud = params.get("audience");
if (params.get("repo")) {
  document.getElementById("crumbname").textContent = params.get("repo");
}
if (params.get("q")) document.getElementById("q").value = params.get("q");

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
  if (ctype !== "all") {
    if (ctype === "programs") {
      if ((p.category || "internship") === "internship") return false;
    } else if ((p.category || "internship") !== ctype) return false;
  }
  if (aud !== "all" && !(p.audience || []).includes(aud)) return false;
  if (fresh !== "all") {
    const h = hoursAgo(p);
    if (h === null || h > FRESH.find(f => f[0] === fresh)[1]) return false;
  }
  if (country === "remote") {
    if (!p.remote) return false;
  } else if (country !== "all" && !p.remote) {
    // No derived country means the region is unknown, so the row passes
    // every region selection (same rule as unknown season below). Remote
    // rows (branch above) pass every region too.
    const cs = p.countries || [];
    if (cs.length && !cs.includes(country)) return false;
  }
  if (season !== "all") {
    const sv = seasonOf(p);
    // Unknown season passes every season filter: most postings never state
    // a cycle, and hiding them empties the presets. is_open verification
    // is what retires dead-season postings, not this tag.
    if (sv) {
      if (season.startsWith("cycle:")) {
        if (!sv.includes(season.slice(6))) return false;
      } else if (sv !== season) return false;
    }
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
    ((p.category || "internship") !== "internship"
      ? `<span class="lbl season">${esc(p.category)}</span> ` : "")
    + (p.audience || []).map(a => `<span class="lbl aud">${esc(a)}</span> `).join("")
    + `<span class="lbl role">${esc(p.role)}</span> `
    + `<span class="lbl deg">${degreesOf(p).join("/") || "any degree"}</span> `
    + (p.countries || []).slice(0, 3).map(c => `<span class="lbl country">${esc(c)}</span>`).join(" ") + " "
    + (seasonOf(p) ? `<span class="lbl season">${esc(seasonOf(p))}</span> `
       : (season !== "all" ? `<span class="lbl src">season unlisted</span> ` : ""))
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
      <div class="l2">${misc}</div>
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
  const cats = [...new Set(data.map(p => p.category || "internship"))].sort();
  const tOpts = [["all", "all (" + data.length + ")"]]
    .concat(cats.map(c => [c, c + " (" + data.filter(p => (p.category || "internship") === c).length + ")"]))
    .concat(cats.length > 1 ? [["programs", "non-internship"]] : []);
  ctype = fillOpts("f-type", tOpts, tOpts.some(o => o[0] === ctype) ? ctype : "all") || "all";
  const auds = [...new Set(data.flatMap(p => p.audience || []))].sort();
  const aOpts = [["all", "all"]].concat(auds.map(a =>
    [a, a + " (" + data.filter(p => (p.audience || []).includes(a)).length + ")"]));
  aud = fillOpts("f-aud", aOpts, aOpts.some(o => o[0] === aud) ? aud : "all") || "all";
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
  const cOpts = [["all", "all"], ["remote", "Remote"]].concat(cs.map(c => [c, c]));
  country = fillOpts("f-country", cOpts,
    cOpts.some(o => o[0] === country) ? country : "all") || "all";
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


# Landing page for a link clicked inside an email. No script and no fetch:
# a mail client's browser opens it cold, often with images and JS off, and
# the only job is to say what just happened.
NOTICE_HEAD = """<!doctype html>
<meta charset="utf-8">
<title>Shortlist</title>
<style>""" + BASE_CSS + """
  .card { max-width:520px; margin:12vh auto; border:1px solid #363636; border-radius:8px;
          background:#1e1e1e; padding:24px 26px; }
  .card h1 { font-size:20px; font-weight:600; margin:0 0 10px; }
  .card p { color:#8b949e; margin:0 0 10px; }
</style>
"""


def notice_page(heading: str, body: str) -> bytes:
    """One small self-contained page. Never echoes the token."""
    return (NOTICE_HEAD + f"""<div class="card">
  <h1>{heading}</h1>
  <p>{body}</p>
  <p><a href="/listings">Browse the board</a></p>
</div>
""").encode()


VERIFIED_PAGE = notice_page(
    "Alerts confirmed",
    "This address is confirmed. New postings that match your filters "
    "arrive in one message per poll cycle, and every message carries an "
    "unsubscribe link.",
)
UNSUBSCRIBED_PAGE = notice_page(
    "Unsubscribed",
    "This subscription is off. Nothing further will be sent to it, and "
    "there is nothing else to confirm.",
)
BAD_LINK_PAGE = notice_page(
    "Link not recognized",
    "This link is not valid. It may already have been used, or the "
    "subscription may have expired unconfirmed.",
)

# /api/companies scans every posting, so the answer is cached briefly and
# shared by every client. 60 s is short against a 120 s poll cycle: nothing
# a subscriber could pick is more than one cycle stale.
COMPANIES_TTL_S = 60.0
_companies_cache: tuple[float, list[dict]] | None = None
_companies_lock = threading.Lock()


def live_companies(store) -> list[dict]:
    """Companies with at least one live (non-rejected) posting, busiest first.

    Folded on notify.company_key, so one employer is one row whatever the
    detectors spelled it, and the row shows the spelling most postings
    carry. This is the list the subscribe form autocompletes against, and
    that is the whole point: a name picked from here provably matches rows,
    which is what stops someone typing "Googel" and waiting weeks for mail
    that was never going to come.
    """
    global _companies_cache
    now = time.monotonic()
    with _companies_lock:
        if _companies_cache and now - _companies_cache[0] < COMPANIES_TTL_S:
            return _companies_cache[1]

    spellings: dict[str, dict[str, int]] = {}
    for p in postings_snapshot(store):  # shared read; never its own table scan
        if p.status == Status.REJECTED:
            continue
        seen = spellings.setdefault(company_key(p.company), {})
        seen[p.company] = seen.get(p.company, 0) + 1
    rows = [
        # max over a sorted list of spellings: ties resolve alphabetically
        # rather than by dict order, so the answer does not wobble.
        {"name": max(sorted(seen), key=lambda s: seen[s]), "postings": sum(seen.values())}
        for key, seen in spellings.items() if key
    ]
    rows.sort(key=lambda r: (-r["postings"], r["name"].lower()))
    with _companies_lock:
        _companies_cache = (now, rows)
    return rows


def reset_companies_cache() -> None:
    """Drop the cached list. For tests, which share this process-wide state."""
    global _companies_cache
    with _companies_lock:
        _companies_cache = None


# Degree labels a subscription may name. Read off the posting schema rather
# than restated, so the endpoint cannot start accepting a level the postings
# it filters do not have.
DEGREE_CHOICES = frozenset(get_args(DegreeLevel))
# Countries per subscription, and the length of one name. Ten regions is
# already wider than a job search, and 60 characters clears the longest real
# country name with room to spare. Both refuse rather than truncate: a
# silently shortened name would match nothing and look like a dead feed.
COUNTRY_LIMIT = 10
COUNTRY_MAX = 60

_WS_RE = re.compile(r"\s+")


def clean_country(value: str) -> str:
    """Untrusted text off the subscribe form, flattened to one line. The
    same treatment clean_company gives a suggested name: control characters
    become spaces, runs collapse, ends are trimmed. Collapsing matters
    because the result is compared for equality against the country names
    locations.countries_of derives."""
    return _WS_RE.sub(" ", CTRL_RE.sub(" ", value or "")).strip()


def valid_degrees(raw) -> list[str] | None:
    """The degree levels a subscribe request asked for, deduplicated, or
    None when the request is malformed. An empty list is valid and means no
    constraint, so absence and emptiness read the same."""
    if not isinstance(raw, list) or len(raw) > len(DEGREE_CHOICES):
        return None
    if any(not isinstance(d, str) or d not in DEGREE_CHOICES for d in raw):
        return None
    return sorted(set(raw))


def valid_countries(raw) -> list[str] | None:
    """The countries a subscribe request asked for, cleaned and
    deduplicated, or None when the request is malformed. Empty is valid and
    means no constraint, matching valid_degrees."""
    if not isinstance(raw, list) or len(raw) > COUNTRY_LIMIT:
        return None
    out: list[str] = []
    for c in raw:
        if not isinstance(c, str):
            return None
        name = clean_country(c)
        if not name or len(name) > COUNTRY_MAX:
            return None
        if name not in out:
            out.append(name)
    return out


def company_matches(store, companies: list[str]) -> dict[str, int]:
    """Live posting count per requested company, keyed by the subscriber's
    own spelling.

    Zero is a legitimate answer, not an error: someone may want a company we
    have not detected yet, and the subscription is created either way. The
    number exists so the UI can say "we track no open postings there yet"
    instead of leaving a person to conclude it from silence.
    """
    live = {company_key(r["name"]): r["postings"] for r in live_companies(store)}
    return {c: live.get(company_key(c), 0) for c in companies}


class RateLimiter:
    """Sliding-window per-key limiter. In-process is enough: one web
    container, and the goal is abuse resistance, not precision."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window_s]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self) -> None:
        """Drop every window. For tests, which share this process-wide state."""
        with self._lock:
            self._hits.clear()


SUGGEST_LIMIT = RateLimiter(limit=5, window_s=60.0)
VISIT_LIMIT = RateLimiter(limit=30, window_s=60.0)
SUBSCRIBE_LIMIT = RateLimiter(limit=5, window_s=60.0)
UNSUBSCRIBE_LIMIT = RateLimiter(limit=30, window_s=60.0)
# A full scan behind a cache, and a token-guessing surface. Both are cheap
# per request and neither is a thing one client needs 30 times a minute.
COMPANIES_LIMIT = RateLimiter(limit=30, window_s=60.0)
VERIFY_LIMIT = RateLimiter(limit=30, window_s=60.0)
# Confirmations are limited per recipient as well as per client, because the
# two limits stop different things. A rotating client passes the per-IP
# window while mailing one victim over and over, and a stream of unwanted
# confirmations is both a mail bomb and the shortest path to the complaint
# rate that costs an SES account its sending. Keyed on a hash of the
# address, so the limiter's memory holds no addresses.
CONFIRM_LIMIT = RateLimiter(limit=2, window_s=3600.0)

# A rate limiter caps one client; it does not stop a rotating one. Collapsing
# repeat submissions is what keeps the resolver's cost proportional to
# distinct companies rather than to request volume, so the window matches the
# resolution cache TTL: inside it, a repeat is pure duplicate work.
SUGGEST_DEDUPE_DAYS = 30

# Self-declared automation. Crawlers are recorded as device='bot' rather than
# dropped, so the filter's effect stays visible in the numbers instead of
# silently deciding what counts as a visitor.
BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|headlesschrome|phantomjs|lighthouse"
    r"|curl|wget|python-|go-http-client|java/|okhttp|libwww|scrapy"
    r"|facebookexternalhit|feedfetcher|uptime|monitoring",
    re.I,
)
MOBILE_UA_RE = re.compile(r"mobi|android|iphone|ipad|ipod|tablet", re.I)


def classify_device(ua: str) -> str:
    """Coarse class, the only thing kept from the user agent: the raw string
    is a fingerprint on its own. An absent agent is automation too; every
    browser sends one."""
    if not ua or BOT_UA_RE.search(ua):
        return "bot"
    return "mobile" if MOBILE_UA_RE.search(ua) else "desktop"


def visitor_hash(salt: str, ip: str, ua: str) -> str:
    """Per-day visitor identity: sha256(salt + ip + user agent), 16 hex chars.

    The same person on the same day lands on the same value, so uniques are
    countable; the salt rotates at the UTC boundary and is deleted two days
    later, so the same person on two days is two unrelated values and no
    stored row can be walked back to an address, by us or anyone else.
    """
    return hashlib.sha256(f"{salt}{ip}{ua}".encode()).hexdigest()[:16]

# Every consumer of the whole table shares one snapshot. Against a hosted
# Postgres a full read is metered egress, and the board refetches on a timer
# from every open tab, so reading per request costs GB/day. Two guards: inside
# the TTL the cached rows are served without touching the database at all, and
# after it a small version query decides whether a re-read is needed, so data
# that has not changed is never sent twice.
SNAPSHOT_TTL_S = 15.0  # cheap to re-validate: only a changed token re-reads
# (built_at, cache_key, version, rows). cache_key pins the cache to the store
# it was read from, so a process serving a different database never sees
# another one's rows.
_snapshot: tuple[float, str, str | None, list] | None = None
_snapshot_lock = threading.Lock()


def postings_snapshot(store) -> list:
    """All postings, cached process-wide. See SNAPSHOT_TTL_S."""
    global _snapshot
    now = time.monotonic()
    key = getattr(store, "cache_key", "")
    with _snapshot_lock:
        fresh = _snapshot is not None and _snapshot[1] == key
        if fresh and now - _snapshot[0] < SNAPSHOT_TTL_S:
            return _snapshot[3]
        version = store.data_version()
        if fresh and version is not None and version == _snapshot[2]:
            _snapshot = (now, key, version, _snapshot[3])  # unchanged: keep rows
            return _snapshot[3]
        rows = store.all_postings()
        _snapshot = (now, key, version, rows)
        return rows


_postings_cache: tuple[list, bytes, str] | None = None
_postings_lock = threading.Lock()


def postings_payload(db_path: Path) -> tuple[bytes, str]:
    """Serialized /api/postings payload and its ETag.

    Keyed on the snapshot's identity, so the JSON is rebuilt exactly when the
    underlying rows are replaced and reused on every request in between. The
    ETag lets a polling client revalidate for a few hundred bytes instead of
    re-downloading 1.5 MB it already has.
    """
    global _postings_cache
    rows_in = postings_snapshot(open_store(db_path))
    with _postings_lock:
        if _postings_cache and _postings_cache[0] is rows_in:
            return _postings_cache[1], _postings_cache[2]
        rows = []
        for p in rows_in:
            d = p.model_dump(mode="json")
            d["countries"] = countries_of(p.locations)
            d["remote"] = is_remote(p.locations)
            d["role"] = classify_role(p.title)
            rows.append(d)
        body = json.dumps(rows).encode()
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        _postings_cache = (rows_in, body, etag)
        return body, etag


def make_handler(db_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz":
                # Liveness only: never touches the database. The container
                # healthcheck runs every 30 s, and pointing it at a data
                # endpoint bills a full table read each time.
                self._send(200, b"ok", "text/plain")
            elif path == "/":
                self._send(200, LANDING.encode(), "text/html; charset=utf-8")
            elif path == "/listings":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/postings":
                body, etag = postings_payload(db_path)
                if self.headers.get("If-None-Match") == etag:
                    self._send_304(etag)  # ~200 bytes instead of 1.5 MB
                    return
                self._send(200, body, "application/json",
                           {"ETag": etag, "Cache-Control": "no-cache"})
            elif path == "/api/suggestions":
                store = open_store(db_path)
                self._send(200, json.dumps(store.recent_suggestions()).encode(), "application/json")
            elif path == "/api/companies":
                if not COMPANIES_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                rows = live_companies(open_store(db_path))
                self._send(200, json.dumps(rows).encode(), "application/json")
            elif path == "/api/verify":
                if not VERIFY_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                token = self._query_token()
                # The click, its time and its address are the half of the
                # opt-in evidence the signup could not record: they prove
                # someone at that address confirmed, not merely that someone
                # typed it in. Stored on the row, never logged or echoed.
                ok = bool(token) and open_store(db_path).verify_by_token(
                    token, self._client_ip()
                )
                self._send_page(ok, VERIFIED_PAGE)
            elif path == "/api/unsubscribe":
                # A link click, so it acts immediately and asks nothing: an
                # unsubscribe has to be free and simple (CAN-SPAM), and a
                # confirmation step is neither. Idempotent by nature, since
                # deactivating an inactive row is the same write.
                if not UNSUBSCRIBE_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                token = self._query_token()
                ok = bool(token) and open_store(db_path).deactivate_by_token(token)
                self._send_page(ok, UNSUBSCRIBED_PAGE)
            else:
                self._send(404, b"not found", "text/plain")

        def _query_token(self) -> str:
            """The token from the query string, or "" when absent or absurd.
            Email clients can send a link click and nothing else, so verify
            and unsubscribe carry theirs here."""
            q = parse_qs(urlsplit(self.path).query)
            token = (q.get("token") or [""])[0].strip()
            return token if 0 < len(token) <= 64 else ""

        def _send_page(self, ok: bool, page: bytes) -> None:
            self._send(200 if ok else 404, page if ok else BAD_LINK_PAGE,
                       "text/html; charset=utf-8")

        def _client_ip(self) -> str:
            fwd = self.headers.get("X-Forwarded-For", "")
            return fwd.split(",")[0].strip() if fwd else self.client_address[0]

        def do_POST(self):
            if self.path == "/api/suggest":
                if not SUGGEST_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                body = self._json_body()
                if body is None:
                    return
                kind = body.get("kind", "company")
                value = str(body.get("value", "")).strip()
                if kind not in ("url", "company") or not value or len(value) > 500:
                    self._send(400, b"bad request", "text/plain")
                    return
                if kind == "company":
                    # A company name reaches an LLM prompt holding a web
                    # search tool and costs money to resolve, so it is
                    # validated here rather than queued as-is: 80 chars max
                    # (refused, not truncated, so the submitter knows),
                    # control characters stripped, and nothing that cannot
                    # plausibly be a company (URL-shaped, digits, punctuation).
                    if len(value) > COMPANY_MAX or not valid_company(value):
                        self._send(400, b"bad request", "text/plain")
                        return
                    value = clean_company(value)
                store = open_store(db_path)
                dup = store.duplicate_suggestion(kind, value, SUGGEST_DEDUPE_DAYS)
                if dup is not None:
                    # Same success shape: the submitter sees their suggestion
                    # accepted, and no second unit of work exists.
                    seen = json.dumps({"id": dup["id"], "duplicate": True}).encode()
                    self._send(200, seen, "application/json")
                    return
                sid = store.add_suggestion(
                    kind, value,
                    company=(str(body.get("company") or "").strip()[:COMPANY_MAX] or None),
                    keywords=(str(body.get("keywords") or "").strip()[:120] or None),
                )
                self._send(200, json.dumps({"id": sid}).encode(), "application/json")
            elif self.path == "/api/visit":
                if not VISIT_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                body = self._json_body()
                if body is None:
                    return
                page = str(body.get("page", "unknown"))[:40]
                ua = self.headers.get("User-Agent", "")
                store = open_store(db_path)
                # The address is hash input and nothing else: it reaches no
                # column and no log line, here or downstream.
                store.record_visit(
                    page,
                    visitor_hash(store.visit_salt(), self._client_ip(), ua),
                    classify_device(ua),
                )
                self._send(200, b"{}", "application/json")
            elif self.path == "/api/subscribe":
                if not SUBSCRIBE_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                body = self._json_body()
                if body is None:
                    return
                channel = body.get("channel")
                target = str(body.get("target", "")).strip()
                filters = body.get("filters") or {}
                if not isinstance(filters, dict):
                    self._send(400, b"bad request", "text/plain")
                    return
                companies = filters.get("companies") or []
                # None from either means the request named something this
                # endpoint does not offer. Each dimension is optional and an
                # empty one narrows nothing, so a request that sets neither
                # is the subscribe-to-everything case, not a malformed one.
                degrees = valid_degrees(filters.get("degrees") or [])
                countries = valid_countries(filters.get("countries") or [])
                if (
                    channel not in ("push", "email")
                    or not target or len(target) > 500
                    or degrees is None or countries is None
                    or not isinstance(companies, list) or len(companies) > 20
                    or any(
                        not isinstance(c, str) or not c.strip() or len(c) > 80
                        # A name that normalizes to nothing ("...") would
                        # store an empty key, and an empty key set means
                        # every company: refuse it rather than silently
                        # subscribing someone to the entire board.
                        or not company_key(c)
                        for c in companies
                    )
                ):
                    self._send(400, b"bad request", "text/plain")
                    return
                if channel == "email":
                    # An address is typed by whoever is at the keyboard, who
                    # may not be its owner, so it is validated at the door
                    # and confirmed by mail before anything is sent to it.
                    target = clean_email(target)
                    if not valid_email(target):
                        self._send(400, b"bad request", "text/plain")
                        return
                companies = [c.strip() for c in companies]
                store = open_store(db_path)
                # The address is consent evidence here, not analytics: it
                # records where this opt-in came from, which is what answers
                # a later "who asked for this mail". It reaches this column
                # and nothing else, unlike a page view, which stores only a
                # daily-salted hash because counting needs no identity.
                sid, token, verify_token = store.add_subscription(
                    channel, target,
                    build_filters(companies, degrees, countries),
                    self._client_ip(),
                )
                sent = False
                if channel == "email":
                    sender = email_sender()
                    recipient = recipient_key(target)
                    if sender is None:
                        log.warning(
                            "subscription %s created but no email channel is "
                            "configured, so no confirmation was sent", sid,
                        )
                    elif not CONFIRM_LIMIT.allow(recipient):
                        # The row still exists and its link still works if the
                        # earlier confirmation was wanted; if it was not, the
                        # prune deletes it. Either way this address is not
                        # mailed again right now.
                        log.warning(
                            "subscription %s: confirmation suppressed, this "
                            "address was mailed one recently", sid,
                        )
                    else:
                        sent = sender.send_confirmation({
                            "id": sid, "target": target,
                            "token": token, "verify_token": verify_token,
                        })
                        if sent:
                            # Stamped only on acceptance. An unstamped row is
                            # a signup nobody was asked to confirm, which the
                            # prune spares and the poller mails later.
                            store.mark_confirmation_sent(sid)
                self._send(200, json.dumps({
                    "id": sid,
                    "token": token,
                    "channel": channel,
                    # Email waits for the click; push carries the browser's
                    # own permission grant and is live immediately.
                    "pending_verification": channel == "email",
                    "verification_sent": sent,
                    # Companies only, on purpose. This answers "does this
                    # employer exist on the board", which is the pick a
                    # subscriber can get wrong by typing a name we do not
                    # carry. Degrees and countries are chosen from fixed
                    # lists and cannot be wrong that way, and folding their
                    # counts in here would turn one clear answer into a
                    # number nobody could act on.
                    "matches": company_matches(store, companies),
                }).encode(), "application/json")
            elif self.path.split("?")[0] == "/api/unsubscribe":
                if not UNSUBSCRIBE_LIMIT.allow(self._client_ip()):
                    self._send(429, b"slow down", "text/plain")
                    return
                # Two callers. The frontend posts {token} as JSON. A mail
                # client doing RFC 8058 one-click posts the emailed URL with
                # a form body we have no use for, so the query string wins
                # when it carries a token and the body is drained unread.
                token = self._query_token()
                if token:
                    self._drain_body()
                else:
                    body = self._json_body()
                    if body is None:
                        return
                    token = str(body.get("token", "")).strip()
                if not token or len(token) > 64:
                    self._send(400, b"bad request", "text/plain")
                    return
                ok = open_store(db_path).deactivate_by_token(token)
                self._send(200, json.dumps({"ok": ok}).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def _json_body(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, b"bad request", "text/plain")
                return None

        def _drain_body(self) -> None:
            """Read and discard the request body. A one-click unsubscribe
            carries a form body this endpoint ignores, and an unread body
            desynchronizes the next request on a keep-alive connection."""
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return
            if length > 0:
                self.rfile.read(min(length, 8192))

        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_304(self, etag: str):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def log_message(self, *args):  # quiet
            pass

    return Handler


class V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(db_path: Path, port: int = 8000):
    """Local default: both loopback families (browsers resolve localhost to
    ::1 on some systems; an IPv4-only bind looks down). In a container set
    INTAKE_BIND=0.0.0.0: Docker's port mapping connects to the container's
    ethernet interface, which a loopback-only bind never hears."""
    import os

    handler = make_handler(db_path)
    bind = os.environ.get("INTAKE_BIND")
    targets = (
        [(ThreadingHTTPServer, bind)] if bind
        else [(ThreadingHTTPServer, "127.0.0.1"), (V6Server, "::1")]
    )
    servers = []
    for cls, host in targets:
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

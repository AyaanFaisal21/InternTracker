"""Lazy localhost frontend. Zero dependencies beyond the stdlib.

GitHub-issues-style layout: left sidebar filters, contribute banner wired
to suggestion intake, spotlight cards, search, label-pill rows expanding
to qualifications.

Routes:
  GET  /                 HTML dashboard (auto-refreshes)
  GET  /api/postings     JSON: all postings, newest first
  GET  /api/suggestions  JSON: recent suggestions with status
  POST /api/suggest      queue a suggestion {kind, value, company?, keywords?}

NOTE: PAGE is a plain Python string. Backslash escapes inside embedded JS
must be double-escaped or avoided; a stray \\n kills the whole script.
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

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>RUemployed</title>
<style>
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         margin: 0; background:#0d1117; color:#e6edf3; }
  a { color:#4493f8; text-decoration:none; } a:hover { text-decoration:underline; }
  .layout { display:flex; gap:24px; max-width:1400px; margin:0 auto; padding:24px; }
  .sidebar { width:230px; flex-shrink:0; }
  .main { flex:1; min-width:0; }
  .side-section { margin-bottom:18px; }
  .side-head { color:#8b949e; font-size:12px; font-weight:600; text-transform:uppercase;
               letter-spacing:.4px; margin-bottom:6px; }
  .side-item { display:flex; justify-content:space-between; padding:5px 10px; border-radius:6px;
               cursor:pointer; color:#e6edf3; font-size:13.5px; }
  .side-item:hover { background:#161b22; }
  .side-item.on { background:#1f6feb33; border-left:2px solid #4493f8; font-weight:600; }
  .side-item .n { color:#8b949e; font-size:12px; }
  .side-select { width:100%; background:#161b22; color:#e6edf3; border:1px solid #30363d;
                 border-radius:6px; padding:5px 8px; font-size:13px; }
  .banner { border:1px solid #30363d; border-radius:8px; padding:14px 18px; margin-bottom:14px;
            background:#161b22; }
  .banner h3 { margin:0 0 6px; font-size:15px; }
  .banner .muted { color:#8b949e; font-size:13px; }
  .banner input { background:#0d1117; color:#e6edf3; border:1px solid #30363d;
                  border-radius:6px; padding:5px 10px; margin-right:6px; font-size:13px; }
  .banner button { background:#238636; color:#fff; border:1px solid #2ea04366; border-radius:6px;
                   padding:5px 14px; cursor:pointer; font-size:13px; font-weight:600; }
  .spotlights { display:flex; gap:14px; margin-bottom:14px; }
  .spot { flex:1; border:1px solid #30363d; border-radius:8px; padding:12px 16px; background:#161b22; }
  .spot .co { color:#8b949e; font-size:12px; }
  .spot .t { font-weight:600; }
  .searchrow { display:flex; gap:10px; margin-bottom:12px; }
  .searchrow input { flex:1; background:#0d1117; color:#e6edf3; border:1px solid #30363d;
                     border-radius:6px; padding:7px 12px; font-size:14px; }
  .listhead { border:1px solid #30363d; border-radius:8px 8px 0 0; background:#161b22;
              padding:10px 16px; display:flex; gap:18px; align-items:center; font-size:13.5px; }
  .listhead .tab { cursor:pointer; color:#8b949e; font-weight:600; }
  .listhead .tab.on { color:#e6edf3; }
  .listhead .right { margin-left:auto; color:#8b949e; }
  .rows { border:1px solid #30363d; border-top:none; border-radius:0 0 8px 8px; }
  .issue { border-top:1px solid #21262d; padding:10px 16px; }
  .issue:first-child { border-top:none; }
  .issue summary { list-style:none; cursor:pointer; }
  .issue summary::-webkit-details-marker { display:none; }
  .l1 { display:flex; flex-wrap:wrap; gap:7px; align-items:baseline; }
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
  .lbl.src     { border-color:#30363d; color:#8b949e; background:transparent; }
  .posted { margin-left:auto; color:#8b949e; font-size:12.5px; white-space:nowrap; }
  .l2 { color:#8b949e; font-size:12.5px; margin-top:3px; padding-left:22px; }
  .body { margin:10px 0 4px 22px; color:#9da7b3; font-size:13px; border-left:2px solid #30363d;
          padding-left:14px; white-space:pre-wrap; }
  .sugstat { color:#8b949e; font-size:12px; margin-top:8px; }
</style>
<div class="layout">
<div class="sidebar">
  <div class="side-section"><div class="side-head">Status</div><div id="side-status"></div></div>
  <div class="side-section"><div class="side-head">Degree</div><div id="side-degree"></div></div>
  <div class="side-section"><div class="side-head">Role</div><div id="side-role"></div></div>
  <div class="side-section"><div class="side-head">Posted</div><div id="side-fresh"></div></div>
  <div class="side-section"><div class="side-head">Country</div>
    <select class="side-select" id="country" onchange="country=this.value;render()"></select></div>
  <div class="side-section"><div class="side-head">Season</div>
    <select class="side-select" id="season" onchange="season=this.value;render()"></select></div>
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
const DEGREES = ["any", "BS", "MS", "PhD"];
const FRESH = [["all", Infinity], ["2h", 2], ["8h", 8], ["24h", 24], ["2d", 48], ["3d", 72], ["1w", 168]];
const PRESTIGE = ["jane street", "openai", "anthropic", "google", "apple", "nvidia", "stripe",
  "citadel", "hudson river trading", "two sigma", "palantir", "databricks", "microsoft", "meta"];
let tab = "open", degree = "BS", role = "all", fresh = "all",
    country = "United States", season = "all", data = [];

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
  if (degree !== "any" && d.length && !d.includes(degree)) return false;
  if (role !== "all" && p.role !== role) return false;
  if (fresh !== "all") {
    const h = hoursAgo(p);
    if (h === null || h > FRESH.find(f => f[0] === fresh)[1]) return false;
  }
  if (country !== "all" && !(p.countries || []).includes(country)) return false;
  if (season !== "all" && seasonOf(p) !== season) return false;
  const q = document.getElementById("q").value.trim().toLowerCase();
  if (q) {
    const hay = (p.title + " " + p.company + " " + (p.qualifications || "")).toLowerCase();
    if (!q.split(/\\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}
function sideList(el, items, current, setter) {
  document.getElementById(el).innerHTML = items.map(([label, count]) =>
    `<div class="side-item ${label === current ? "on" : ""}" onclick="${setter}('${label}')">
       <span>${label}</span><span class="n">${count ?? ""}</span></div>`).join("");
}
function setDegree(v) { degree = v; render(); }
function setRole(v) { role = v; render(); }
function setFresh(v) { fresh = v; render(); }
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

  const statuses = ["all-status"].concat(OPEN).concat(["rejected"]);
  sideList("side-degree", DEGREES.map(d => [d, data.filter(p => {
    const dd = degreesOf(p); return d === "any" || !dd.length || dd.includes(d);
  }).length]), degree, "setDegree");
  const roles = ["all"].concat([...new Set(data.map(p => p.role))].sort());
  sideList("side-role", roles.map(r => [r, r === "all" ? data.length : data.filter(p => p.role === r).length]), role, "setRole");
  sideList("side-fresh", FRESH.map(f => [f[0], null]), fresh, "setFresh");
  sideList("side-status", [["open", openN], ["closed", data.length - openN]], tab,
    "(t=>{tab=t;render();})");
  fillSelect("country", [...new Set(data.flatMap(p => p.countries || []))].sort(), () => country, v => country = v);
  fillSelect("season", [...new Set(data.map(seasonOf).filter(Boolean))].sort(), () => season, v => season = v);
  spotlight();
}
function fillSelect(id, values, get, set) {
  const sel = document.getElementById(id);
  const keep = sel.value || get();
  sel.innerHTML = `<option value="all">all</option>` + values.map(v => `<option>${esc(v)}</option>`).join("");
  sel.value = values.includes(keep) || keep === "all" ? keep : "all";
  set(sel.value);
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
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    kind = body.get("kind", "company")
                    value = str(body.get("value", "")).strip()
                    if kind not in ("url", "company") or not value:
                        raise ValueError("bad suggestion")
                except (ValueError, json.JSONDecodeError):
                    self._send(400, b"bad request", "text/plain")
                    return
                store = Store(db_path)
                sid = store.add_suggestion(
                    kind, value,
                    company=body.get("company") or None,
                    keywords=body.get("keywords") or None,
                )
                self._send(200, json.dumps({"id": sid}).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

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

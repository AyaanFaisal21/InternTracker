"""Lazy localhost frontend. Zero dependencies beyond the stdlib.

Purpose: inspect exactly what the pipeline is acquiring, per status, with
links that go to the resolved employer page. This is a debug surface, not
the product frontend.

Routes:
  GET /               HTML dashboard (auto-refreshes)
  GET /api/postings   JSON: all postings, newest first
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .locations import countries_of
from .store import Store

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>RUemployed intake</title>
<style>
  body { font: 14px/1.5 -apple-system, system-ui, monospace; margin: 2rem; background:#111; color:#ddd; }
  h1 { font-size: 1.2rem; } .muted { color:#888; }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #2a2a2a; vertical-align: top; }
  th { color:#888; font-weight: 600; position: sticky; top: 0; background:#111; }
  a { color:#7ab8ff; text-decoration: none; } a:hover { text-decoration: underline; }
  .pill { padding: 1px 8px; border-radius: 9px; font-size: 12px; }
  .published, .verified { background:#1d3b1d; color:#8fdc8f; }
  .rejected { background:#3b1d1d; color:#dc8f8f; }
  .pending, .gated { background:#3b331d; color:#dccf8f; }
  .filters button { background:#222; color:#ddd; border:1px solid #333; padding:4px 10px; margin-right:6px; cursor:pointer; border-radius:4px; }
  .filters button.on { background:#345; }
  .filters select { background:#222; color:#ddd; border:1px solid #333; padding:4px 8px; border-radius:4px; }
  .reasons { color:#888; font-size: 12px; }
  .issue { border:1px solid #2a2a2a; border-radius:6px; margin-top:8px; padding:10px 14px; }
  .issue summary { cursor:pointer; list-style:none; display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
  .issue summary::-webkit-details-marker { display:none; }
  .ititle { font-weight:600; font-size:15px; }
  .tag { padding:1px 9px; border-radius:10px; font-size:11.5px; border:1px solid #333; background:#1c1c1c; color:#bbb; }
  .tag.deg { border-color:#2c4a6e; color:#9cc4ee; }
  .tag.season { border-color:#5a4a1e; color:#e2c76e; }
  .tag.src { border-color:#3a3a3a; }
  .tag.country { border-color:#2e4a2e; color:#9ed69e; }
  .posted { color:#888; font-size:12px; margin-left:auto; }
  .body { margin-top:10px; color:#aaa; font-size:13px; border-top:1px solid #222; padding-top:8px; white-space:pre-wrap; }
</style>
<h1>RUemployed intake <span class="muted" id="count"></span></h1>
<div class="filters" id="filters"></div>
<div class="filters" style="margin-top:6px">
  country: <select id="country" onchange="country=this.value;render()"><option value="all">all</option></select>
  season: <select id="season" onchange="season=this.value;render()"><option value="all">all</option></select>
</div>
<div id="rows"></div>
<script>
const STATUSES = ["all","pending","gated","verified","published","rejected"];
const DEGREES = ["any","BS","MS","PhD"];
const FRESH = [["all",Infinity],["2h",2],["8h",8],["24h",24],["2d",48],["3d",72],["1w",168]];
let filter = "all", degree = "BS", country = "United States", fresh = "all", season = "all", data = [];
function degreesOf(p) {
  const v = p.verdict && (p.verdict.degree_levels || []).length
    ? p.verdict.degree_levels : (p.degree_levels || []);
  return v;  // [] -> no requirement found -> open to all
}
function degreeOk(p) {
  if (degree === "any") return true;
  const d = degreesOf(p);
  return d.length === 0 || d.includes(degree);
}
function freshOk(p) {
  if (fresh === "all") return true;
  if (!p.date_posted) return false;
  const hrs = (Date.now() - new Date(p.date_posted).getTime()) / 3.6e6;
  return hrs <= FRESH.find(f => f[0] === fresh)[1];
}
function seasonOf(p) {
  return (p.verdict && p.verdict.season) || p.season || null;
}
function postedLabel(p) {
  if (p.date_posted) {
    const hrs = (Date.now() - new Date(p.date_posted).getTime()) / 3.6e6;
    if (hrs < 1) return "just now";
    if (hrs < 24) return `${Math.floor(hrs)}h ago`;
    if (hrs < 24 * 14) return `${Math.floor(hrs / 24)}d ago`;
    return p.date_posted.slice(0, 10);
  }
  return p.date_posted_text || "date unknown";
}
function render() {
  const rows = data.filter(p =>
    (filter === "all" || p.status === filter) && degreeOk(p) && freshOk(p)
    && (season === "all" || seasonOf(p) === season)
    && (country === "all" || (p.countries || []).includes(country)));
  document.getElementById("count").textContent = `— ${rows.length} shown / ${data.length} total`;
  document.getElementById("rows").innerHTML = rows.map(p => `
    <details class="issue">
      <summary>
        <span class="ititle"><a href="${p.canonical_url || p.url}" target="_blank">${p.title}</a></span>
        <span class="tag src">${p.company}</span>
        <span class="pill ${p.status}">${p.status}</span>
        <span class="tag deg">${degreesOf(p).join("/") || "any degree"}</span>
        ${seasonOf(p) ? `<span class="tag season">${seasonOf(p)}</span>` : ""}
        ${(p.countries || []).map(c => `<span class="tag country">${c}</span>`).join("")}
        <span class="posted">${postedLabel(p)}</span>
      </summary>
      <div class="body">${detailsOf(p)}</div>
    </details>`).join("");
  document.getElementById("filters").innerHTML =
    STATUSES.map(s =>
      `<button class="${s === filter ? "on" : ""}" onclick="filter='${s}';render()">${s}</button>`).join("")
    + `<span style="margin:0 8px;color:#555">|</span>`
    + DEGREES.map(d =>
      `<button class="${d === degree ? "on" : ""}" onclick="degree='${d}';render()">${d}</button>`).join("")
    + `<span style="margin:0 8px;color:#555">|</span>`
    + FRESH.map(f =>
      `<button class="${f[0] === fresh ? "on" : ""}" onclick="fresh='${f[0]}';render()">${f[0]}</button>`).join("");
  const ssel = document.getElementById("season");
  const seasons = [...new Set(data.map(seasonOf).filter(Boolean))].sort();
  const skeep = ssel.value || "all";
  ssel.innerHTML = `<option value="all">all</option>` + seasons.map(x => `<option>${x}</option>`).join("");
  ssel.value = seasons.includes(skeep) || skeep === "all" ? skeep : "all";
  season = ssel.value;
}
function detailsOf(p) {
  const parts = [];
  if (p.qualifications) parts.push("Qualifications:\\n" + p.qualifications);
  if (p.verdict) parts.push("Verifier: " + (p.verdict.reasons || []).join("; "));
  if (p.reject_reason) parts.push("Rejected: " + p.reject_reason);
  parts.push("Locations: " + (p.locations || []).slice(0, 6).join("; "));
  parts.push("Sources: " + p.sources.join(", ") + "   first seen: " + (p.first_seen || "").slice(0, 16));
  return parts.join("\\n\\n");
}
function refreshCountryOptions() {
  const sel = document.getElementById("country");
  const seen = [...new Set(data.flatMap(p => p.countries || []))].sort();
  const keep = sel.value;
  sel.innerHTML = `<option value="all">all</option>` +
    seen.map(c => `<option value="${c}">${c}</option>`).join("");
  const want = keep || country;
  sel.value = seen.includes(want) || want === "all" ? want : "all";
  country = sel.value;
}
async function load() {
  data = await (await fetch("/api/postings")).json();
  refreshCountryOptions();
  render();
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
                    rows.append(d)
                body = json.dumps(rows).encode()
                self._send(200, body, "application/json")
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

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
  .reasons { color:#888; font-size: 12px; max-width: 34ch; }
</style>
<h1>RUemployed intake <span class="muted" id="count"></span></h1>
<div class="filters" id="filters"></div>
<table>
  <thead><tr><th>company</th><th>title</th><th>status</th><th>sources</th>
  <th>degrees</th><th>locations</th><th>first seen</th><th>notes</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
const STATUSES = ["all","pending","gated","verified","published","rejected"];
const DEGREES = ["any","BS","MS","PhD"];
let filter = "all", degree = "any", data = [];
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
function render() {
  const rows = data.filter(p => (filter === "all" || p.status === filter) && degreeOk(p));
  document.getElementById("count").textContent = `— ${rows.length} shown / ${data.length} total`;
  document.getElementById("rows").innerHTML = rows.map(p => `
    <tr>
      <td>${p.company}</td>
      <td><a href="${p.canonical_url || p.url}" target="_blank">${p.title}</a></td>
      <td><span class="pill ${p.status}">${p.status}</span></td>
      <td>${p.sources.join(", ")}</td>
      <td>${degreesOf(p).join("/") || "any"}</td>
      <td>${p.locations.slice(0,3).join("; ")}</td>
      <td>${(p.first_seen || "").slice(0, 16).replace("T", " ")}</td>
      <td class="reasons">${p.reject_reason || (p.verdict ? p.verdict.reasons.join("; ") : "")}</td>
    </tr>`).join("");
  document.getElementById("filters").innerHTML =
    STATUSES.map(s =>
      `<button class="${s === filter ? "on" : ""}" onclick="filter='${s}';render()">${s}</button>`).join("")
    + `<span style="margin:0 8px;color:#555">|</span>`
    + DEGREES.map(d =>
      `<button class="${d === degree ? "on" : ""}" onclick="degree='${d}';render()">${d}</button>`).join("");
}
async function load() {
  data = await (await fetch("/api/postings")).json();
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
                body = json.dumps(
                    [p.model_dump(mode="json") for p in store.all_postings()]
                ).encode()
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

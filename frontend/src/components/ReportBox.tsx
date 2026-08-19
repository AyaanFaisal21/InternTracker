// "Report an issue" for the listings board. Anonymous by design: nothing
// identifying is collected or asked for, so the form is a kind and a message.

import { useState } from "react";
import { submitReport } from "../api";
import type { ReportKind } from "../api";

const MAX = 2000; // matches REPORT_MAX on the server

export default function ReportBox({ context }: { context: string }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ReportKind>("issue");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);

  async function send() {
    const body = text.trim();
    if (!body) {
      setMsg("Say what went wrong first.");
      return;
    }
    setSending(true);
    try {
      const ok = await submitReport(kind, body.slice(0, MAX), context);
      // The server answers a repeat submission with success on purpose, so
      // this branch means refused: too many too fast, or the daily cap.
      setMsg(ok ? "Thanks, logged." : "Too many reports for now. Try later.");
      if (ok) setText("");
    } catch {
      setMsg("Could not send. Check your connection.");
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button className="reportlink" onClick={() => setOpen(true)}>
        Report an issue
      </button>
    );
  }

  return (
    <div className="reportbox">
      <div className="reportrow">
        <select
          aria-label="report type"
          value={kind}
          onChange={(e) => setKind(e.target.value as ReportKind)}
        >
          <option value="issue">Something is wrong</option>
          <option value="fix">Suggest a change</option>
        </select>
        <button onClick={() => setOpen(false)} aria-label="close report form">
          Close
        </button>
      </div>
      <textarea
        rows={3}
        maxLength={MAX}
        aria-label="what happened"
        placeholder={
          kind === "issue"
            ? "Wrong season, dead link, duplicate listing…"
            : "What would you change, and why?"
        }
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="reportrow">
        <button disabled={sending} onClick={() => void send()}>
          {sending ? "Sending…" : "Send"}
        </button>
        <span className="muted">{msg || "Anonymous. No account, no email."}</span>
      </div>
    </div>
  );
}

// The email-alert panel on the board. Collapsed to a single row until the
// reader asks for it, then it expands in place: company picker, address,
// submit. Nothing is subscribed until the reader opens the confirmation link
// the server mails, so the success state leads with that.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { fetchCompanies, subscribe } from "../api";
import type { Company, SubscribeRefusal } from "../api";
import CompanyPicker from "./CompanyPicker";
import type { ListState } from "./CompanyPicker";
import "../styles/subscribe.css";

// Matches the backend's per-subscription company cap.
const MAX_COMPANIES = 20;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

const REFUSALS: Record<SubscribeRefusal, string> = {
  invalid:
    "The server rejected that. Check the address and your company list, then try again.",
  rate_limited:
    "Too many requests from this network. Wait a minute and try again.",
  server: "The server had a problem saving that. Try again in a moment.",
};

const UNREACHABLE =
  "Could not reach the server. Check your connection and try again.";

export default function SubscribePanel() {
  const [open, setOpen] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [listState, setListState] = useState<ListState>("loading");
  const [selected, setSelected] = useState<string[]>([]);
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // Only the match map and the address are kept from the response. The
  // confirmation token it also carries is never stored and never rendered.
  const [matches, setMatches] = useState<Record<string, number> | null>(null);
  const [sentTo, setSentTo] = useState("");

  const uid = useId();
  const bodyId = `${uid}-body`;
  const emailId = `${uid}-email`;
  const emailErrId = `${uid}-email-err`;

  // One controller for the panel's whole life: unmounting aborts whatever
  // is in flight, as the board does with its 30s refresh.
  const abortRef = useRef<AbortController | null>(null);
  if (abortRef.current === null) abortRef.current = new AbortController();
  useEffect(() => {
    const ctrl = abortRef.current;
    return () => ctrl?.abort();
  }, []);

  // Monotonic request ids: a slow response is applied only while it is still
  // the newest request, so a retry cannot be overwritten by its predecessor.
  const listReq = useRef(0);
  const sendReq = useRef(0);
  const asked = useRef(false);

  function loadCompanies() {
    const id = ++listReq.current;
    setListState("loading");
    fetchCompanies(abortRef.current?.signal)
      .then((c) => {
        if (id !== listReq.current) return;
        setCompanies(c);
        setListState("ready");
      })
      .catch(() => {
        if (id !== listReq.current || abortRef.current?.signal.aborted) return;
        setListState("failed");
      });
  }

  function toggle() {
    setOpen((was) => !was);
    if (asked.current) return;
    asked.current = true; // fetch once, filter client-side from here on
    loadCompanies();
  }

  const address = email.trim();
  const emailOk = EMAIL_RE.test(address);
  const showEmailError = touched && !emailOk && address.length > 0;
  const ready = emailOk && selected.length > 0 && !sending;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!ready) return;
    setSending(true);
    setError("");
    const id = ++sendReq.current;
    try {
      const res = await subscribe(
        {
          channel: "email",
          target: address,
          filters: { companies: selected },
        },
        abortRef.current?.signal,
      );
      if (id !== sendReq.current || abortRef.current?.signal.aborted) return;
      setSending(false);
      if (res.ok) {
        setMatches(res.subscription.matches);
        setSentTo(address);
      } else {
        setError(REFUSALS[res.reason]);
      }
    } catch {
      if (id !== sendReq.current || abortRef.current?.signal.aborted) return;
      setSending(false);
      setError(UNREACHABLE);
    }
  }

  function reset() {
    setMatches(null);
    setSentTo("");
    setSelected([]);
    setEmail("");
    setTouched(false);
    setError("");
  }

  // The picker takes a neutral {name, count} list; the API answers in its
  // own shape, and only this caller should know that shape.
  const options = useMemo(
    () => companies.map((c) => ({ name: c.name, count: c.postings })),
    [companies],
  );

  const untracked =
    listState === "ready"
      ? selected.filter(
          (s) => !companies.some((c) => c.name.toLowerCase() === s.toLowerCase()),
        ).length
      : 0;

  const confirmed = matches
    ? Object.entries(matches).sort((a, b) => b[1] - a[1])
    : [];

  return (
    <section className="sub" aria-label="Email alerts">
      <button
        type="button"
        className="sub-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={toggle}
      >
        <span className="sub-kicker">Alerts</span>
        <span className="sub-line">Email me when a company posts</span>
        <span className="sub-cue">{open ? "close" : "set up"}</span>
      </button>

      <div className="sub-body" id={bodyId} hidden={!open}>
        {matches ? (
          <div className="sub-done">
            <h4>Check your inbox</h4>
            <p className="sub-note">
              We sent a confirmation link to <b>{sentTo}</b>. The alerts do not
              start until you open it, so the mail has to arrive before
              anything else happens.
            </p>
            <ul className="sub-confirm">
              {confirmed.map(([name, n]) => (
                <li key={name} className={n > 0 ? "" : "new"}>
                  <span>{name}</span>
                  <span className="cp-dot" aria-hidden="true">
                    ·
                  </span>
                  <span className="cp-n">
                    {n > 0
                      ? `${n} open now`
                      : "not tracked yet, we will watch for it"}
                  </span>
                </li>
              ))}
            </ul>
            <button type="button" className="sub-again" onClick={reset}>
              Set up another alert
            </button>
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)} noValidate>
            <p className="sub-note sub-intro">
              We email you when a new posting from one of these companies
              clears verification.
            </p>

            <CompanyPicker
              label="Companies to watch"
              placeholder="start typing a company name"
              help="Type to search the board. Arrow keys move, Enter adds, Backspace removes the last one."
              unit="open"
              options={options}
              listState={listState}
              selected={selected}
              max={MAX_COMPANIES}
              allowUnknown
              disabled={sending}
              onChange={setSelected}
            />

            {untracked > 0 && (
              <p className="sub-warn">
                {untracked === 1
                  ? "1 name is not tracked yet - you will be notified if it appears."
                  : `${untracked} names are not tracked yet - you will be notified if they appear.`}
              </p>
            )}

            {listState === "failed" && (
              <p className="sub-warn">
                The company list did not load, so names cannot be checked here.{" "}
                <button type="button" className="sub-retry" onClick={loadCompanies}>
                  retry
                </button>
              </p>
            )}

            <div className="sub-field">
              <label className="cp-lab" htmlFor={emailId}>
                Where to send it
              </label>
              <div className="sub-send">
                <input
                  id={emailId}
                  className="sub-email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@school.edu"
                  value={email}
                  disabled={sending}
                  aria-invalid={showEmailError}
                  aria-describedby={showEmailError ? emailErrId : undefined}
                  onChange={(e) => setEmail(e.target.value)}
                  onBlur={() => setTouched(true)}
                />
                <button className="sub-btn" type="submit" disabled={!ready}>
                  {sending ? "Sending" : "Email me matches"}
                </button>
              </div>
              {showEmailError && (
                <p className="sub-warn" id={emailErrId}>
                  That does not look like an email address.
                </p>
              )}
            </div>
          </form>
        )}

        <p className="sub-live sub-ok" role="status">
          {matches ? `Request accepted. Confirmation email sent to ${sentTo}.` : ""}
        </p>
        <p className="sub-live sub-err" role="alert">
          {error}
        </p>
      </div>
    </section>
  );
}

// The email-alert panel on the board. Collapsed to a single row until the
// reader asks for it, then it expands in place: company picker, degree and
// location narrowing, address, submit. Nothing is subscribed until the
// reader opens the confirmation link the server mails, so the success state
// leads with that. When the server saved the row but mailed nothing
// (verification_sent false, e.g. no email channel is configured yet), the
// success state says so instead: the picks are held and the confirmation
// goes out when alerts start.
//
// Degree and location are checkbox groups rather than typeaheads. Both are
// short fixed sets, so a control that has to be searched costs more than it
// gives, and neither can be typed wrong: a free-text country would let
// someone subscribe to a spelling no posting carries and then wait on mail
// that was never coming. Both default to nothing picked, which the server
// reads as no constraint, and the summary line says so in words before
// anyone submits.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { fetchCompanies, subscribe } from "../api";
import type { Company, DegreeLevel, SubscribeRefusal } from "../api";
import CompanyPicker from "./CompanyPicker";
import type { ListState } from "./CompanyPicker";
import "../styles/subscribe.css";

// Matches the backend's per-subscription company cap.
const MAX_COMPANIES = 20;

// Matches the backend's per-subscription country cap.
const MAX_COUNTRIES = 10;

// [stored value, checkbox label, the word the summary line uses].
const DEGREE_OPTIONS: [DegreeLevel, string, string][] = [
  ["BS", "Bachelors", "undergraduate"],
  ["MS", "Masters", "masters"],
  ["PhD", "PhD", "PhD"],
];

// Always offered whatever the board currently carries: they are this
// audience's default search, and the "US & Canada" shortcut below is only
// meaningful if both of its halves are pickable on their own.
const HOME_COUNTRIES = ["United States", "Canada"];

// Shorter forms for the summary line only. The stored value is always the
// server's own country name, which is what the fan-out compares against.
const SHORT_COUNTRY: Record<string, string> = {
  "United States": "the US",
  "United Kingdom": "the UK",
  "United Arab Emirates": "the UAE",
};

/** "a", "a and b", "a, b and c". */
function series(parts: string[]): string {
  if (parts.length < 3) return parts.join(" and ");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * One line saying what this selection delivers, so the consequence is
 * readable before the address is typed rather than inferred from silence
 * afterwards. Nothing picked has to read as everything, since that is what
 * the server does with it.
 */
function summarize(degrees: DegreeLevel[], countries: string[]): string {
  const words = DEGREE_OPTIONS.filter(([v]) => degrees.includes(v)).map(
    ([, , word]) => word,
  );
  const level = words.length
    ? `${capitalize(series(words))} roles`
    : "Roles at any degree level";
  const where = countries.length
    ? ` in ${series(countries.map((c) => SHORT_COUNTRY[c] ?? c))}`
    : ", anywhere";
  return `${level}${where}.`;
}

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

/**
 * @param boardCountries countries the board currently derives, so the
 * location choices are the ones postings actually carry. The caller already
 * has them, which keeps this panel to its one request (/api/companies).
 */
export default function SubscribePanel({
  boardCountries = [],
}: {
  boardCountries?: string[];
}) {
  const [open, setOpen] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [listState, setListState] = useState<ListState>("loading");
  const [selected, setSelected] = useState<string[]>([]);
  const [degrees, setDegrees] = useState<DegreeLevel[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // Only the match map, the address and whether a confirmation was mailed
  // are kept from the response. The confirmation token it also carries is
  // never stored and never rendered.
  const [matches, setMatches] = useState<Record<string, number> | null>(null);
  const [sentTo, setSentTo] = useState("");
  const [mailed, setMailed] = useState(false);

  const uid = useId();
  const bodyId = `${uid}-body`;
  const emailId = `${uid}-email`;
  const emailErrId = `${uid}-email-err`;
  const summaryId = `${uid}-summary`;

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
          // An empty dimension is left out rather than sent empty: absent
          // is exactly how the server stores "no constraint", so the wire
          // shape and the stored shape say the same thing.
          filters: {
            companies: selected,
            ...(degrees.length ? { degrees } : {}),
            ...(countries.length ? { countries } : {}),
          },
        },
        abortRef.current?.signal,
      );
      if (id !== sendReq.current || abortRef.current?.signal.aborted) return;
      setSending(false);
      if (res.ok) {
        setMatches(res.subscription.matches);
        setSentTo(address);
        setMailed(res.subscription.verification_sent);
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
    setMailed(false);
    setSelected([]);
    setDegrees([]);
    setCountries([]);
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

  // The two home countries first, then whatever else the board carries,
  // alphabetically. Anything already picked stays on the list even after the
  // board's last posting there closes: the board refreshes every 30s under a
  // reader who may be mid-selection, and a pick with no box left is one they
  // can still see in the summary but no longer undo.
  const places = useMemo(
    () => [
      ...HOME_COUNTRIES,
      ...[...new Set([...boardCountries, ...countries])]
        .filter((c) => !HOME_COUNTRIES.includes(c))
        .sort((a, b) => a.localeCompare(b)),
    ],
    [boardCountries, countries],
  );

  const full = countries.length >= MAX_COUNTRIES;
  // Derived rather than stored: "US & Canada" is a shortcut onto the two
  // real names, so it cannot disagree with the boxes underneath it.
  const homeOn = HOME_COUNTRIES.every((c) => countries.includes(c));

  function toggleDegree(level: DegreeLevel) {
    setDegrees((was) =>
      was.includes(level)
        ? was.filter((d) => d !== level)
        : DEGREE_OPTIONS.map(([v]) => v).filter(
            (v) => v === level || was.includes(v),
          ),
    );
  }

  function toggleCountry(name: string) {
    setCountries((was) =>
      was.includes(name)
        ? was.filter((c) => c !== name)
        : was.length >= MAX_COUNTRIES
          ? was
          : places.filter((c) => c === name || was.includes(c)),
    );
  }

  // Turning the shortcut on can add two names at once, so it has its own
  // read of the cap rather than borrowing the single-country one.
  const homeFull =
    !homeOn &&
    countries.length + HOME_COUNTRIES.filter((c) => !countries.includes(c)).length >
      MAX_COUNTRIES;

  function toggleHome() {
    setCountries((was) =>
      HOME_COUNTRIES.every((c) => was.includes(c))
        ? was.filter((c) => !HOME_COUNTRIES.includes(c))
        : places.filter((c) => HOME_COUNTRIES.includes(c) || was.includes(c)),
    );
  }

  const summary = summarize(degrees, countries);

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
            {mailed ? (
              <>
                <h4>Check your inbox</h4>
                <p className="sub-note">
                  We sent a confirmation link to <b>{sentTo}</b>. The alerts do
                  not start until you open it, so the mail has to arrive before
                  anything else happens.
                </p>
              </>
            ) : (
              <>
                <h4>
                  Saved{" "}
                  <span className="sub-flag">not live yet</span>
                </h4>
                <p className="sub-note">
                  We saved your list for <b>{sentTo}</b>. Alerts do not run yet,
                  and we will email you to confirm when they start.
                </p>
              </>
            )}
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
            <p className="sub-summary">
              <span className="sub-summary-lab">You will get</span> {summary}
            </p>
            <button type="button" className="sub-again" onClick={reset}>
              Set up another alert
            </button>
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)} noValidate>
            <p className="sub-note sub-intro">
              We email you when a new posting clears verification and matches
              everything you pick below.
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

            <fieldset className="sub-group" aria-describedby={summaryId}>
              <legend className="cp-lab">Degree levels</legend>
              <div className="sub-choices">
                {DEGREE_OPTIONS.map(([value, text]) => (
                  <label
                    key={value}
                    className={
                      degrees.includes(value) ? "sub-choice on" : "sub-choice"
                    }
                  >
                    <input
                      type="checkbox"
                      checked={degrees.includes(value)}
                      disabled={sending}
                      onChange={() => toggleDegree(value)}
                    />
                    {text}
                  </label>
                ))}
              </div>
              <p className="cp-help">
                Leave every box clear for all of them. A posting that never
                states a level comes through whatever you pick here.
              </p>
            </fieldset>

            <fieldset className="sub-group" aria-describedby={summaryId}>
              {/* Direct child of the fieldset: a legend nested inside
                  anything else stops naming the group. */}
              <legend className="cp-lab">Where</legend>
              <div className="sub-choices">
                <label className={homeOn ? "sub-choice on" : "sub-choice"}>
                  <input
                    type="checkbox"
                    checked={homeOn}
                    disabled={sending || homeFull}
                    onChange={toggleHome}
                  />
                  US &amp; Canada
                </label>
                {places.map((name) => (
                  <label
                    key={name}
                    className={
                      countries.includes(name) ? "sub-choice on" : "sub-choice"
                    }
                  >
                    <input
                      type="checkbox"
                      checked={countries.includes(name)}
                      disabled={sending || (full && !countries.includes(name))}
                      onChange={() => toggleCountry(name)}
                    />
                    {name}
                  </label>
                ))}
              </div>
              <p className="cp-help">
                Leave every box clear for anywhere. Remote roles, and postings
                whose location we could not read, come through whatever you
                pick here.
                {countries.length > 0 &&
                  ` ${countries.length} of ${MAX_COUNTRIES} picked.`}
              </p>
            </fieldset>

            <p className="sub-summary" id={summaryId}>
              <span className="sub-summary-lab">You will get</span> {summary}
            </p>

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
          {matches
            ? mailed
              ? `Request accepted. Confirmation email sent to ${sentTo}.`
              : `Request saved for ${sentTo}. Alerts are not live yet, so no email was sent.`
            : ""}
        </p>
        <p className="sub-live sub-err" role="alert">
          {error}
        </p>
      </div>
    </section>
  );
}

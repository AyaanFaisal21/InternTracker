// The "/listings" board: one master list, dark GitHub-issue styling.
// Mounted with key=location.search (see main.tsx), so every filter URL
// starts from fresh state, like the Python server's full page loads.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  fetchPostings,
  fetchSuggestions,
  recordVisit,
  submitSuggestion,
} from "../api";
import type { Posting, Suggestion } from "../api";
import {
  degreesOf,
  hoursAgo,
  isOpen,
  postedLabel,
  seasonOf,
} from "../postings";
import Masthead from "../components/Masthead";
import SubscribePanel from "../components/SubscribePanel";
import CompanyPicker, { companyKey } from "../components/CompanyPicker";
import type { CompanyOption } from "../components/CompanyPicker";
import "../styles/listings.css";

import type { KeyboardEvent, ReactNode } from "react";

type Opt = [string, string];

/** Enter/Space activation for clickable elements that are not buttons. */
function keyActivate(fn: () => void) {
  return (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

const DEGREES: Opt[] = [
  ["any", "any"],
  ["BS", "BS"],
  ["MS", "MS"],
  ["PhD", "PhD"],
  ["grad", "MS/PhD"],
];

const FRESH: [string, number][] = [
  ["all", Infinity],
  ["2h", 2],
  ["8h", 8],
  ["24h", 24],
  ["2d", 48],
  ["3d", 72],
  ["1w", 168],
];

const FRESH_OPTS: Opt[] = FRESH.map((f) => [f[0], f[0]]);

// Default country filter. Sentinel value, not a data string: it ORs the two
// exact country names the server derives ("United States", "Canada").
const US_CANADA = "us-ca";

// "Remote" select option. Also a sentinel: it filters on the derived
// boolean `remote`, not on membership in the countries list.
const REMOTE = "remote";

// Season terms in start order within a calendar year, each with the month
// it starts. The year is the calendar year the term mostly falls in, so
// January and February are Winter of that same year (Winter 2027).
const TERMS: [string, number][] = [
  ["Winter", 1],
  ["Spring", 3],
  ["Summer", 6],
  ["Fall", 9],
];

// Four terms is the whole recruiting horizon; nothing beyond it is open to
// apply to yet.
const WINDOW = 4;

// Season sentinel: the postings whose source stated no season at all.
const UNLISTED = "unlisted";

const SEASON_WORDS: Record<string, string> = {
  summer: "Summer",
  fall: "Fall",
  autumn: "Fall", // one term, two source vocabularies
  winter: "Winter",
  spring: "Spring",
};

/**
 * The terms worth offering on `now`: those whose start month has not passed.
 * Read from the clock, never from the data's distinct strings and never
 * from a literal year, so the list is right every August without an edit.
 */
function seasonWindow(now: Date): string[] {
  const from = now.getFullYear() * 12 + now.getMonth() + 1;
  const out: string[] = [];
  for (let y = now.getFullYear(); out.length < WINDOW; y++)
    for (const [name, month] of TERMS) {
      if (y * 12 + month < from) continue;
      out.push(`${name} ${y}`);
      if (out.length === WINDOW) break;
    }
  return out;
}

/**
 * The term a stored season string names, or null when it names none. The
 * stored strings are free text from many sources, so case and punctuation
 * are folded and the first season word plus a four-digit year decide. A
 * bare year names no term and buys no bucket: which term it meant is a
 * guess, and a wrong guess hides the posting from the term it belongs to.
 */
function seasonBucket(raw: string | null): string | null {
  if (!raw) return null;
  const text = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/([a-z])(\d)/g, "$1 $2");
  const year = /\b(20\d{2})\b/.exec(text);
  const word = /\b(summer|fall|autumn|winter|spring)\b/.exec(text);
  return year && word ? `${SEASON_WORDS[word[1]]} ${year[1]}` : null;
}

/** Companies from ?company=: comma separated, blanks and repeats dropped. */
function parseCompanies(raw: string | null): string[] {
  const out: string[] = [];
  for (const part of (raw ?? "").split(",")) {
    const name = part.trim();
    if (name && !out.some((n) => companyKey(n) === companyKey(name)))
      out.push(name);
  }
  return out;
}

function SideSelect({
  head,
  opts,
  value,
  onChange,
}: {
  head: string;
  opts: Opt[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="side-section">
      <div className="side-head">{head}</div>
      <select
        className="side-select"
        aria-label={head}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {opts.map(([v, label]) => (
          <option key={v} value={v}>{label}</option>
        ))}
      </select>
    </div>
  );
}

function Row({ p, seasonFilter }: { p: Posting; seasonFilter: string }) {
  const cls =
    p.status === "rejected"
      ? "closed"
      : p.status === "pending" || p.status === "gated"
        ? "pend"
        : "open";

  // Leading type tag: internships and events must be tellable apart at a
  // glance; program/scholarship/research share a neutral treatment.
  const cat = p.category || "internship";
  const tagCls =
    cat === "internship" ? "tag t-intern" : cat === "event" ? "tag t-event" : "tag t-plain";

  const pills: ReactNode[] = [<span className={tagCls}>{cat}</span>];
  (p.audience ?? [])
    .filter((a) => a !== "diversity")
    .forEach((a) => pills.push(<span className="lbl aud">{a}</span>));
  pills.push(<span className="lbl role">{p.role}</span>);
  pills.push(
    <span className="lbl deg">{degreesOf(p).join("/") || "any degree"}</span>,
  );
  (p.countries ?? []).slice(0, 3).forEach((c) =>
    pills.push(<span className="lbl country">{c}</span>),
  );
  const sv = seasonOf(p);
  if (sv) pills.push(<span className="lbl season">{sv}</span>);
  else if (seasonFilter !== "all")
    pills.push(<span className="lbl note">season unlisted</span>);

  const bodyParts: ReactNode[] = [];
  if (p.qualifications)
    bodyParts.push(<><b>Qualifications</b><br />{p.qualifications}</>);
  if (p.verdict)
    bodyParts.push(
      <><b>Verifier</b><br />{(p.verdict.reasons ?? []).join("; ") || "approved"}</>,
    );
  if (p.reject_reason)
    bodyParts.push(<><b>Rejected</b><br />{p.reject_reason}</>);
  bodyParts.push(
    <><b>Locations</b><br />{(p.locations ?? []).slice(0, 6).join("; ") || "unlisted"}</>,
  );

  return (
    <details className={`issue ${cls}`}>
      <summary>
        <div className="l1">
          <span className="dot">●</span>
          <span className="cobox">{p.company}</span>
          <span className="t">
            <a href={p.canonical_url || p.url} target="_blank" rel="noopener">
              {p.title}
            </a>
          </span>
          <span className="posted">{postedLabel(p)}</span>
        </div>
        <div className="l2">
          {pills.map((pill, i) => (
            <Fragment key={i}>
              {i > 0 && " "}
              {pill}
            </Fragment>
          ))}
        </div>
      </summary>
      <div className="body">
        {bodyParts.map((part, i) => (
          <Fragment key={i}>
            {i > 0 && <><br /><br /></>}
            {part}
          </Fragment>
        ))}
      </div>
    </details>
  );
}

export default function Listings() {
  const { search } = useLocation();
  const params = new URLSearchParams(search);

  // Filter values may arrive as query params; they override the defaults.
  const [tab, setTab] = useState(params.get("status") || "open");
  const [degree, setDegree] = useState(params.get("degree") || "BS");
  const [role, setRole] = useState(params.get("role") || "all");
  const [fresh, setFresh] = useState(params.get("fresh") || "all");
  const [country, setCountry] = useState(params.get("country") || US_CANADA);
  const [season, setSeason] = useState(params.get("season") || "all");
  const [ctype, setCtype] = useState(params.get("type") || "all");
  const [aud, setAud] = useState(params.get("audience") || "all");
  const [q, setQ] = useState(params.get("q") || "");
  // Multi-select, so it carries a list where the others carry one value.
  const [firms, setFirms] = useState(() => parseCompanies(params.get("company")));

  const [data, setData] = useState<Posting[]>([]);
  const [sugs, setSugs] = useState<Suggestion[]>([]);
  const [loaded, setLoaded] = useState(false);

  const [sugval, setSugval] = useState("");
  const [sugkw, setSugkw] = useState("");
  const [sugmsg, setSugmsg] = useState("");

  useEffect(() => {
    document.title = "Shortlist · Listings";
    recordVisit("listings");
  }, []);

  // Monotonic request ids: a response is applied only while it is still the
  // newest request for its resource, so a slow response from an earlier 30s
  // tick cannot overwrite data from a later one. Refs, not state: they must
  // update synchronously and never cause renders.
  const postingsReq = useRef(0);
  const sugsReq = useRef(0);

  // Shared by the 30s refresh and the post-submit refresh in suggest(), so
  // those two callers cannot interleave stale suggestion lists either.
  // Reads only refs and setters, hence safe to call from any render.
  const loadSugs = (signal?: AbortSignal) => {
    const id = ++sugsReq.current;
    fetchSuggestions(signal)
      .then((s) => {
        if (id === sugsReq.current) setSugs(s);
      })
      .catch(() => {});
  };

  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => {
      const id = ++postingsReq.current;
      fetchPostings(ctrl.signal)
        .then((d) => {
          if (id !== postingsReq.current) return; // stale response
          setData(d);
          setLoaded(true);
        })
        .catch(() => {});
      loadSugs(ctrl.signal);
    };
    load();
    // The server answers an unchanged board with a 304, so this costs a few
    // hundred bytes rather than a re-download of the whole list.
    const t = setInterval(load, 30000);
    return () => {
      clearInterval(t);
      ctrl.abort(); // cancel in-flight requests on unmount
    };
  }, []);

  // Everything here depends on `data` alone, so memoize it: without this the
  // option lists and counts are rebuilt (O(options × n)) on every keystroke
  // in the search and suggestion inputs.
  const {
    openN,
    statusOpts,
    typeOpts,
    audOpts,
    degreeOpts,
    roleOpts,
    countries: boardCountries,
    countryOpts,
    seasonOpts,
    firmOpts,
  } = useMemo(() => {
    const openN = data.filter(isOpen).length;

    const cats = [...new Set(data.map((p) => p.category || "internship"))].sort();
    // The diversity audience is filtered out of the UI entirely.
    const auds = [...new Set(data.flatMap((p) => p.audience ?? []))]
      .filter((a) => a !== "diversity")
      .sort();
    const roles = [...new Set(data.map((p) => p.role))].sort();
    const countries = [...new Set(data.flatMap((p) => p.countries ?? []))].sort();

    // Companies come from the postings already loaded rather than from
    // /api/companies: the counts then agree with every other count in this
    // sidebar, no second request can disagree with the board, and the
    // control works before the API answers or without it.
    const spellings = new Map<string, Map<string, number>>();
    for (const p of data) {
      const key = companyKey(p.company);
      if (!key) continue;
      const seen = spellings.get(key);
      if (seen) seen.set(p.company, (seen.get(p.company) ?? 0) + 1);
      else spellings.set(key, new Map([[p.company, 1]]));
    }

    // Only terms still being recruited for, and only those the data can
    // answer, so the list stays short instead of collecting every distinct
    // string the sources ever spelled.
    const termN = new Map<string, number>();
    let unlistedN = 0;
    for (const p of data) {
      const raw = seasonOf(p);
      if (!raw) {
        unlistedN++;
        continue;
      }
      const term = seasonBucket(raw);
      if (term) termN.set(term, (termN.get(term) ?? 0) + 1);
    }

    const statusOpts: Opt[] = [
      ["open", `open (${openN})`],
      ["closed", `closed (${data.length - openN})`],
    ];
    const typeOpts: Opt[] = [
      ["all", `all (${data.length})`],
      ...cats.map((c): Opt => [
        c,
        `${c} (${data.filter((p) => (p.category || "internship") === c).length})`,
      ]),
      ...(cats.length > 1 ? [["programs", "non-internship"] as Opt] : []),
    ];
    const audOpts: Opt[] = [
      ["all", "all"],
      ...auds.map((a): Opt => [
        a,
        `${a} (${data.filter((p) => (p.audience ?? []).includes(a)).length})`,
      ]),
    ];
    const degreeOpts: Opt[] = DEGREES.map(([v, label]) => [
      v,
      v === "any"
        ? "any"
        : `${label} (${
            data.filter((p) => {
              const dd = degreesOf(p);
              if (v === "grad")
                return !dd.length || dd.includes("MS") || dd.includes("PhD");
              return !dd.length || dd.includes(v);
            }).length
          })`,
    ]);
    const roleOpts: Opt[] = [
      ["all", `all (${data.length})`],
      ...roles.map((r): Opt => [
        r,
        `${r} (${data.filter((p) => p.role === r).length})`,
      ]),
    ];
    const countryOpts: Opt[] = [
      ["all", "all"],
      [US_CANADA, "US & Canada"],
      [REMOTE, "Remote"],
      ...countries.map((c): Opt => [c, c]),
    ];
    // No counts here, unlike the other option lists: a term's count would
    // exclude the unlisted postings that a term selection also shows, so
    // the number would contradict the board.
    const seasonOpts: Opt[] = [
      ["all", "all"],
      ...seasonWindow(new Date())
        .filter((t) => termN.has(t))
        .map((t): Opt => [t, t]),
      ...(unlistedN ? [[UNLISTED, "unlisted"] as Opt] : []),
    ];
    const firmOpts: CompanyOption[] = [...spellings.values()].map((seen) => {
      // The commonest spelling names the row, ties alphabetically, so the
      // label cannot wobble between two spellings across refreshes.
      const byUse = [...seen.entries()].sort(
        (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
      );
      return {
        name: byUse[0][0],
        count: byUse.reduce((n, s) => n + s[1], 0),
      };
    });
    // Busiest first, then by name, as /api/companies answers.
    firmOpts.sort(
      (a, b) =>
        b.count - a.count ||
        a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
    );

    return {
      openN,
      statusOpts,
      typeOpts,
      audOpts,
      degreeOpts,
      roleOpts,
      // Handed to the subscribe panel as well as spent on countryOpts: the
      // alert offers the regions the board carries, so a subscriber cannot
      // pick one no posting has ever named.
      countries,
      countryOpts,
      seasonOpts,
      firmOpts,
    };
  }, [data]);

  // Mirror of web.py's fillOpts fallback: when the current filter value is
  // not among the options the data offers, reset it to "all" (web.py tests
  // membership in the same option lists). Runs only after a successful load
  // so URL params survive an unreachable backend. Degree and Posted are
  // never reset, as in web.py.
  useEffect(() => {
    if (!loaded) return;
    const values = (opts: Opt[]) => new Set(opts.map(([v]) => v));
    const types = values(typeOpts);
    const audiences = values(audOpts);
    const roles = values(roleOpts);
    const countries = values(countryOpts);
    const seasons = values(seasonOpts);
    setCtype((v) => (types.has(v) ? v : "all"));
    setAud((v) => (audiences.has(v) ? v : "all"));
    setRole((v) => (roles.has(v) ? v : "all"));
    setCountry((v) => (countries.has(v) ? v : "all"));
    setSeason((v) => (seasons.has(v) ? v : "all"));
    // Same rule for the company chips: a ?company= name the board does not
    // carry, or one whose last posting has just gone, is dropped rather
    // than left as a chip that filters to nothing. Survivors take the
    // board's own spelling, and the identical list is returned unchanged
    // so a refresh that changes nothing cannot cause a render.
    const spelled = new Map(firmOpts.map((c) => [companyKey(c.name), c.name]));
    setFirms((v) => {
      const next: string[] = [];
      for (const name of v) {
        const hit = spelled.get(companyKey(name));
        if (hit && !next.includes(hit)) next.push(hit);
      }
      return next.length === v.length && next.every((n, i) => n === v[i])
        ? v
        : next;
    });
  }, [loaded, typeOpts, audOpts, roleOpts, countryOpts, seasonOpts, firmOpts]);

  // Built once per render, not per posting: matches() runs across the whole
  // board and the set is the same for every row.
  const firmKeys = new Set(firms.map(companyKey));

  function matches(p: Posting): boolean {
    if (tab === "open" ? !isOpen(p) : p.status !== "rejected") return false;
    // OR within the company set, AND against every other filter. An empty
    // set is every company, like every other filter's "all".
    if (firmKeys.size && !firmKeys.has(companyKey(p.company))) return false;
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
    if (aud !== "all" && !(p.audience ?? []).includes(aud)) return false;
    if (fresh !== "all") {
      const limit = FRESH.find((f) => f[0] === fresh)?.[1];
      // web.py would throw on an unknown value; treat it as "all" instead.
      if (limit !== undefined) {
        const h = hoursAgo(p);
        if (h === null || h > limit) return false;
      }
    }
    if (country === REMOTE) {
      if (!p.remote) return false;
    } else if (country !== "all" && !p.remote) {
      // Region selections narrow only rows that name a non-matching
      // country. Rows with no derived country pass every region (mirror of
      // the unknown-season rule below), and remote rows — the branch
      // above — pass every region as well.
      const cs = p.countries ?? [];
      if (cs.length) {
        if (country === US_CANADA) {
          if (!cs.includes("United States") && !cs.includes("Canada"))
            return false;
        } else if (!cs.includes(country)) {
          return false;
        }
      }
    }
    // Unknown season passes every term: the backend maps a flexible posting
    // ("Summer/Fall 2027", "year-round") to null on purpose, most postings
    // never state a cycle at all, and hiding them empties the board.
    // is_open verification is what retires dead-season postings, not this
    // tag. A stated season that names no term in the window is a mismatch
    // like any other, and stays reachable under "all".
    if (season !== "all") {
      const sv = seasonOf(p);
      if (season === UNLISTED) {
        if (sv) return false;
      } else if (sv && seasonBucket(sv) !== season) return false;
    }
    const query = q.trim().toLowerCase();
    if (query) {
      const hay = (
        p.title + " " + p.company + " " + (p.qualifications || "")
      ).toLowerCase();
      if (!query.split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  const shown = data.filter(matches);

  async function suggest() {
    const value = sugval.trim();
    if (!value) return;
    const keywords = sugkw.trim();
    const kind = value.startsWith("http") ? "url" : "company";
    try {
      await submitSuggestion({ kind, value, keywords });
    } catch {
      return; // network failure: keep inputs, no message (as in web.py)
    }
    setSugmsg("queued; processed next cycle");
    setSugval("");
    setSugkw("");
    loadSugs();
  }

  return (
    <div className="page-listings">
      <Masthead />
      <div className="layout">
        <div className="sidebar">
          {/* First in the column: its popup is clipped by the column's own
              scroll box, so it needs the other controls beneath it to open
              over, and it is the one control that spans the mobile grid. */}
          <CompanyPicker
            label="Company"
            placeholder="search companies"
            help="Type to search. Enter adds, Backspace removes."
            options={firmOpts}
            listState={loaded ? "ready" : "loading"}
            selected={firms}
            max={Infinity}
            allowUnknown={false}
            onChange={setFirms}
          />
          <SideSelect head="Status" opts={statusOpts} value={tab} onChange={setTab} />
          <SideSelect head="Type" opts={typeOpts} value={ctype} onChange={setCtype} />
          <SideSelect head="Audience" opts={audOpts} value={aud} onChange={setAud} />
          <SideSelect head="Degree" opts={degreeOpts} value={degree} onChange={setDegree} />
          <SideSelect head="Role" opts={roleOpts} value={role} onChange={setRole} />
          <SideSelect head="Posted" opts={FRESH_OPTS} value={fresh} onChange={setFresh} />
          <SideSelect head="Country" opts={countryOpts} value={country} onChange={setCountry} />
          <SideSelect head="Season" opts={seasonOpts} value={season} onChange={setSeason} />
        </div>
        <div className="main">
          <div className="banner">
            <h3>Want to contribute?</h3>
            <div className="muted">
              Know a posting we have not indexed, or a company we should watch?
              Paste a link or a company name. Every submission is validated
              before it goes live.
            </div>
            <div style={{ marginTop: 8 }}>
              <input
                placeholder="posting URL or company name"
                aria-label="posting URL or company name"
                size={38}
                value={sugval}
                onChange={(e) => setSugval(e.target.value)}
              />{" "}
              <input
                placeholder="keywords (optional)"
                aria-label="keywords (optional)"
                size={20}
                value={sugkw}
                onChange={(e) => setSugkw(e.target.value)}
              />{" "}
              <button onClick={() => void suggest()}>Submit</button>{" "}
              <span className="muted">{sugmsg}</span>
            </div>
            <div className="sugstat">
              {sugs.slice(0, 5).map((x, i) => (
                <Fragment key={x.id}>
                  {i > 0 && <br />}
                  [{x.status}] {x.value.slice(0, 55)}
                  {x.result ? ` → ${x.result}` : ""}
                </Fragment>
              ))}
            </div>
          </div>
          <SubscribePanel boardCountries={boardCountries} />
          <div className="searchrow">
            <input
              placeholder="Search title, company, qualifications"
              aria-label="Search title, company, qualifications"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div className="listhead">
            <span
              className={tab === "open" ? "tab on" : "tab"}
              role="button"
              tabIndex={0}
              aria-pressed={tab === "open"}
              onClick={() => setTab("open")}
              onKeyDown={keyActivate(() => setTab("open"))}
            >
              Open <span>{openN}</span>
            </span>
            <span
              className={tab === "closed" ? "tab on" : "tab"}
              role="button"
              tabIndex={0}
              aria-pressed={tab === "closed"}
              onClick={() => setTab("closed")}
              onKeyDown={keyActivate(() => setTab("closed"))}
            >
              Closed <span>{data.length - openN}</span>
            </span>
            <span className="right">{shown.length} shown</span>
          </div>
          <div className="rows">
            {shown.length ? (
              shown.map((p) => <Row key={p.id} p={p} seasonFilter={season} />)
            ) : (
              <div className="issue" style={{ color: "#8b949e" }}>no matches</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Port of the "/listings" board page from web.py (PAGE).
// The component is mounted with key=location.search (see main.tsx), so every
// preset URL starts from fresh state, like the Python server's full page loads.

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
import TopBar from "../components/TopBar";
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

const PRESTIGE = [
  "jane street", "openai", "anthropic", "google", "apple", "nvidia", "stripe",
  "citadel", "hudson river trading", "two sigma", "palantir", "databricks",
  "microsoft", "meta",
];

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

  const pills: ReactNode[] = [];
  if ((p.category || "internship") !== "internship")
    pills.push(<span className="lbl season">{p.category}</span>);
  (p.audience ?? []).forEach((a) =>
    pills.push(<span className="lbl aud">{a}</span>),
  );
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
    pills.push(<span className="lbl src">season unlisted</span>);
  p.sources.forEach((s) => pills.push(<span className="lbl src">{s}</span>));

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
          {"   "}
          {p.status}
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
  const repo = params.get("repo");

  // Repo presets arrive as query params; they override the defaults.
  const [tab, setTab] = useState(params.get("status") || "open");
  const [degree, setDegree] = useState(params.get("degree") || "BS");
  const [role, setRole] = useState(params.get("role") || "all");
  const [fresh, setFresh] = useState(params.get("fresh") || "all");
  const [country, setCountry] = useState(params.get("country") || "United States");
  const [season, setSeason] = useState(params.get("season") || "all");
  const [ctype, setCtype] = useState(params.get("type") || "all");
  const [aud, setAud] = useState(params.get("audience") || "all");
  const [q, setQ] = useState(params.get("q") || "");

  const [data, setData] = useState<Posting[]>([]);
  const [sugs, setSugs] = useState<Suggestion[]>([]);
  const [loaded, setLoaded] = useState(false);

  const [pinned, setPinned] = useState(() => {
    // localStorage throws when storage is blocked (e.g. cookies disabled);
    // fall back to unpinned instead of crashing the page.
    try {
      return localStorage.getItem("sbpin") === "1";
    } catch {
      return false;
    }
  });
  const [edge, setEdge] = useState(false);

  const [sugval, setSugval] = useState("");
  const [sugkw, setSugkw] = useState("");
  const [sugmsg, setSugmsg] = useState("");

  useEffect(() => {
    document.title = "Shortlist · Listings";
    recordVisit("listings:" + (repo || "all"));
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
    const t = setInterval(load, 30000);
    return () => {
      clearInterval(t);
      ctrl.abort(); // cancel in-flight requests on unmount
    };
  }, []);

  // Sidebar must react even when the cursor sits in the far-left window
  // gutter outside the centered layout.
  useEffect(() => {
    const onMove = (e: MouseEvent) =>
      setEdge((prev) => (e.clientX < 44 ? true : e.clientX > 300 ? false : prev));
    document.addEventListener("mousemove", onMove);
    return () => document.removeEventListener("mousemove", onMove);
  }, []);

  // Everything here depends on `data` alone, so memoize it: without this the
  // option lists, counts, and spotlight sort are rebuilt (O(options × n)) on
  // every keystroke in the search and suggestion inputs and on every sidebar
  // edge-hover render.
  const {
    openN,
    statusOpts,
    typeOpts,
    audOpts,
    degreeOpts,
    roleOpts,
    countryOpts,
    seasonOpts,
    spots,
  } = useMemo(() => {
    const openN = data.filter(isOpen).length;

    const cats = [...new Set(data.map((p) => p.category || "internship"))].sort();
    const auds = [...new Set(data.flatMap((p) => p.audience ?? []))].sort();
    const roles = [...new Set(data.map((p) => p.role))].sort();
    const countries = [...new Set(data.flatMap((p) => p.countries ?? []))].sort();
    const seasons = [
      ...new Set(data.map(seasonOf).filter((s): s is string => Boolean(s))),
    ].sort();

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
      ...countries.map((c): Opt => [c, c]),
    ];
    const seasonOpts: Opt[] = [
      ["all", "all"],
      ["cycle:2027", "2027 cycle"],
      ...seasons.map((s): Opt => [s, s]),
    ];

    const spots = data
      .filter((p) => isOpen(p) && PRESTIGE.includes(p.company.toLowerCase()))
      .sort((a, b) => (b.date_posted || "").localeCompare(a.date_posted || ""))
      .slice(0, 2);

    return {
      openN,
      statusOpts,
      typeOpts,
      audOpts,
      degreeOpts,
      roleOpts,
      countryOpts,
      seasonOpts,
      spots,
    };
  }, [data]);

  // Mirror of web.py's fillOpts fallback: when the current filter value is
  // not among the options the data offers, reset it to "all" (web.py tests
  // membership in the same option lists). Runs only after a successful load
  // so URL presets survive an unreachable backend. Degree and Posted are
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
  }, [loaded, typeOpts, audOpts, roleOpts, countryOpts, seasonOpts]);

  const togglePin = () => {
    const on = !pinned;
    setPinned(on);
    try {
      localStorage.setItem("sbpin", on ? "1" : "");
    } catch {
      // storage blocked: the pin still applies for this page's lifetime
    }
  };

  function matches(p: Posting): boolean {
    if (tab === "open" ? !isOpen(p) : p.status !== "rejected") return false;
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
    if (country !== "all" && !(p.countries ?? []).includes(country)) return false;
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

  const layoutCls =
    "layout" + (pinned ? " pinned" : "") + (edge ? " edge" : "");

  return (
    <div className="page-listings">
      <TopBar crumb={repo || "Listings"} />
      <div className={layoutCls}>
        <div className="sidebar">
          <div className="rail">⚙</div>
          <div className="side-inner">
            <SideSelect head="Status" opts={statusOpts} value={tab} onChange={setTab} />
            <SideSelect head="Type" opts={typeOpts} value={ctype} onChange={setCtype} />
            <SideSelect head="Audience" opts={audOpts} value={aud} onChange={setAud} />
            <SideSelect head="Degree" opts={degreeOpts} value={degree} onChange={setDegree} />
            <SideSelect head="Role" opts={roleOpts} value={role} onChange={setRole} />
            <SideSelect head="Posted" opts={FRESH_OPTS} value={fresh} onChange={setFresh} />
            <SideSelect head="Country" opts={countryOpts} value={country} onChange={setCountry} />
            <SideSelect head="Season" opts={seasonOpts} value={season} onChange={setSeason} />
            <div
              className="side-collapse"
              role="button"
              tabIndex={0}
              onClick={togglePin}
              onKeyDown={keyActivate(togglePin)}
            >
              📌 {pinned ? "Unpin sidebar" : "Pin sidebar open"}
            </div>
          </div>
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
          <div className="spotlights">
            {spots.map((p) => (
              <div className="spot" key={p.id}>
                <div className="co">★ spotlight · {p.company}</div>
                <div className="t">
                  <a href={p.canonical_url || p.url} target="_blank" rel="noopener">
                    {p.title}
                  </a>
                </div>
                <div className="co">{postedLabel(p)}</div>
              </div>
            ))}
          </div>
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

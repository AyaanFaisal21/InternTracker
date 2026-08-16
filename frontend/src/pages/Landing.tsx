// Port of the "/" org-profile landing page from web.py (LANDING).

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPostings, recordVisit } from "../api";
import type { Posting } from "../api";
import { allowsBS, allowsGrad, isOpen, seasonOf } from "../postings";
import TopBar from "../components/TopBar";
import "../styles/landing.css";

const ALL_LISTINGS = "/listings?repo=All+Listings&degree=any&country=all";

interface Repo {
  name: string;
  desc: string;
  lang: string;
  href?: string;
  closed?: boolean;
  soon?: boolean;
  pred: (p: Posting) => boolean;
}

const REPOS: Repo[] = [
  {
    name: "Summer '27",
    desc: "Undergraduate internships, Summer 2027",
    lang: "internships",
    href: "/listings?repo=Summer+%2727&degree=BS&season=Summer+2027",
    pred: (p) => allowsBS(p) && (!seasonOf(p) || seasonOf(p) === "Summer 2027"),
  },
  {
    name: "Spring '27",
    desc: "Undergraduate internships, Spring 2027",
    lang: "internships",
    href: "/listings?repo=Spring+%2727&degree=BS&season=Spring+2027",
    pred: (p) => allowsBS(p) && (!seasonOf(p) || seasonOf(p) === "Spring 2027"),
  },
  {
    name: "Fall '26",
    desc: "Undergraduate internships, Fall 2026",
    lang: "internships",
    href: "/listings?repo=Fall+%2726&degree=BS&season=Fall+2026",
    pred: (p) => allowsBS(p) && (!seasonOf(p) || seasonOf(p) === "Fall 2026"),
  },
  {
    name: "Winter '27",
    desc: "Undergraduate internships, Winter 2027",
    lang: "internships",
    href: "/listings?repo=Winter+%2727&degree=BS&season=Winter+2027",
    pred: (p) => allowsBS(p) && (!seasonOf(p) || seasonOf(p) === "Winter 2027"),
  },
  {
    name: "Bachelors",
    desc: "Everything open to undergraduates",
    lang: "internships",
    href: "/listings?repo=Bachelors&degree=BS",
    pred: (p) => allowsBS(p),
  },
  {
    name: "Masters/PhD",
    desc: "Graduate-level internships",
    lang: "internships",
    href: "/listings?repo=Masters%2FPhD&degree=grad",
    pred: (p) => allowsGrad(p),
  },
  {
    name: "Programs",
    desc: "Fellowships, research programs, scholarships. Underclassmen friendly.",
    lang: "programs",
    href: "/listings?repo=Programs&type=programs&degree=any&country=all",
    pred: (p) => (p.category || "internship") !== "internship",
  },
  {
    name: "Summer '26",
    desc: "Undergraduate internships, Summer 2026 (season over)",
    lang: "internships",
    closed: true,
    pred: () => false,
  },
  {
    name: "New Grad",
    desc: "Full-time new grad roles",
    lang: "newgrad",
    soon: true,
    pred: () => false,
  },
];

/** 21-day activity sparkline points, one x step per day. */
function sparkPoints(rows: Posting[]): string {
  const days = new Array<number>(21).fill(0);
  const now = Date.now();
  rows.forEach((p) => {
    const t = p.date_posted || p.first_seen;
    if (!t) return;
    const d = Math.floor((now - new Date(t).getTime()) / 864e5);
    if (d >= 0 && d < 21) days[20 - d] += 1;
  });
  const max = Math.max(1, ...days);
  return days
    .map(
      (v, i) =>
        (i * (155 / 20)).toFixed(1) + "," + (27 - (v / max) * 24).toFixed(1),
    )
    .join(" ");
}

/** Stable hue for a company name; drives the People grid colors. */
function hue(s: string): number {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

export default function Landing() {
  const [data, setData] = useState<Posting[] | null>(null);
  const [findQ, setFindQ] = useState("");
  const [findTouched, setFindTouched] = useState(false);
  const [notif, setNotif] = useState("Notification settings");

  useEffect(() => {
    const ctrl = new AbortController();
    document.title = "RUemployed";
    recordVisit("landing");
    fetchPostings(ctrl.signal)
      .then(setData)
      .catch(() => {});
    return () => ctrl.abort(); // cancel the in-flight request on unmount
  }, []);

  const loaded = data !== null;

  // Aggregates depend on `data` alone; memoized so typing in the repo-find
  // box does not rebuild them on every keystroke.
  const { open, cycle27, events, people, topics } = useMemo(() => {
    const open = (data ?? []).filter(isOpen);
    const cycle27 = open.filter((p) => {
      const s = seasonOf(p);
      return allowsBS(p) && (!s || s.includes("2027"));
    });
    const events = open.filter((p) => p.category === "event");

    const byCompany = new Map<string, number>();
    open.forEach((p) =>
      byCompany.set(p.company, (byCompany.get(p.company) ?? 0) + 1),
    );
    const people = [...byCompany.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12);

    const byRole = new Map<string, number>();
    open.forEach((p) => byRole.set(p.role, (byRole.get(p.role) ?? 0) + 1));
    const topics = [...byRole.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);

    return { open, cycle27, events, people, topics };
  }, [data]);

  const eventsLive = events.length > 0;

  const show = (n: number) => (loaded ? String(n) : "…");
  const companiesOf = (rows: Posting[]) => new Set(rows.map((p) => p.company)).size;

  const q = findQ.toLowerCase();
  const shownRepos = REPOS.filter((r) => r.name.toLowerCase().includes(q));

  return (
    <div className="page-landing">
      <TopBar />
      <div className="tabs">
        <span className="tab on"><span className="glyph">☷</span>Overview</span>
        <span className="tab"><span className="glyph">📖</span>Repositories <span className="n">8</span></span>
        <span className="tab dim"><span className="glyph">▦</span>Projects</span>
        <span className="tab dim"><span className="glyph">👥</span>People</span>
      </div>
      <div className="wrap">
        <div className="profile">
          <div className="avatar"><span>RU</span></div>
          <div>
            <div className="pname"><span style={{ color: "#f85149" }}>RU</span>employed</div>
            <span className="verified">Verified</span>
            <div className="pmeta">
              <span>👥 <b>{show(open.length)}</b> postings tracked</span>
              <span>📍 New Brunswick, NJ</span>
              <span>🔗 <Link to={ALL_LISTINGS}>ruemployed/listings</Link></span>
            </div>
          </div>
          <button className="notif" onClick={() => setNotif("coming soon")}>
            {notif}
          </button>
        </div>
        <div className="cols">
          <div className="left">
            <h3 className="sec">Pinned</h3>
            <div className="pins">
              <div className="pin">
                <div>
                  <span>📖</span>{" "}
                  <Link className="name" to={ALL_LISTINGS}>All Listings</Link>
                  <span className="pub">Public</span>
                </div>
                <div className="desc">What you're here for</div>
                <div className="foot">
                  <span><span className="gdot">●</span> internships</span>
                  <span>★ <b>{show(open.length)}</b></span>
                  <span>⋔ <b>{show(companiesOf(open))}</b> companies</span>
                </div>
              </div>
              <div className="pin">
                <div>
                  <span>📖</span>{" "}
                  <Link className="name" to="/listings?repo=%2727+Cycle&degree=BS&season=cycle:2027">'27 Cycle</Link>
                  <span className="pub">Public</span>
                </div>
                <div className="desc">Bachelors, every 2027 season</div>
                <div className="foot">
                  <span><span className="gdot">●</span> internships</span>
                  <span>★ <b>{show(cycle27.length)}</b></span>
                  <span>⋔ <b>{show(companiesOf(cycle27))}</b> companies</span>
                </div>
              </div>
              <div className={eventsLive ? "pin" : "pin dim"}>
                <div>
                  <span>📖</span>{" "}
                  {eventsLive ? (
                    <Link className="name" to="/listings?repo=events&type=event&degree=any&country=all">events</Link>
                  ) : (
                    <span className="name" style={{ color: "#8b949e" }}>events</span>
                  )}
                  <span className="pub">{eventsLive ? "Public" : "Coming soon"}</span>
                </div>
                <div className="desc">
                  Company interest meetings, hackathons, recruiting events. Soft tunnels into the hiring pipeline.
                </div>
                <div className="foot">
                  <span><span className="ydot">●</span> events</span>
                  {eventsLive && <span>★ {events.length} upcoming</span>}
                </div>
              </div>
            </div>
            <h3 className="sec">Repositories</h3>
            <div className="findrow">
              <input
                placeholder="Find a repository..."
                aria-label="Find a repository"
                value={findQ}
                onChange={(e) => {
                  setFindQ(e.target.value);
                  setFindTouched(true);
                }}
              />
              <span className="ghostbtn">Type ▾</span>
              <span className="ghostbtn">Language ▾</span>
              <span className="ghostbtn">Sort ▾</span>
            </div>
            <div className="repolist">
              {(loaded || findTouched) &&
                shownRepos.map((r) => {
                  const rows = open.filter(r.pred);
                  const dim = Boolean(r.closed || r.soon);
                  const badge = r.closed ? "Closed" : r.soon ? "Coming soon" : "Public";
                  const dot = r.closed ? "rdot" : r.soon ? "ydot" : "gdot";
                  return (
                    <div className={dim ? "repo dim" : "repo"} key={r.name}>
                      <div className="rmain">
                        <div>
                          {!dim && r.href ? (
                            <Link className="rname" to={r.href}>{r.name}</Link>
                          ) : (
                            <span className="rname soon">{r.name}</span>
                          )}
                          <span className="pub">{badge}</span>
                        </div>
                        <div className="rdesc">{r.desc}</div>
                        <div className="foot">
                          <span><span className={dot}>●</span> {r.lang}</span>
                          {!dim && (
                            <>
                              <span>★ {rows.length}</span>
                              <span>Updated today</span>
                            </>
                          )}
                        </div>
                      </div>
                      {!dim && (
                        <svg className="spark" viewBox="0 0 155 30">
                          <polyline points={sparkPoints(rows)} />
                        </svg>
                      )}
                    </div>
                  );
                })}
            </div>
            <div className="abuse">Report abuse</div>
          </div>
          <div className="rail">
            <div className="railsec">
              <div className="railhead">People</div>
              <div className="people">
                {people.map(([co]) => {
                  const init = co
                    .split(/\s+/)
                    .map((w) => w[0])
                    .join("")
                    .slice(0, 2)
                    .toUpperCase();
                  return (
                    <div
                      key={co}
                      className="person"
                      title={co}
                      style={{
                        borderColor: `hsl(${hue(co)} 40% 35%)`,
                        color: `hsl(${hue(co)} 60% 75%)`,
                      }}
                    >
                      {init}
                    </div>
                  );
                })}
              </div>
              <Link className="viewall" to={ALL_LISTINGS}>View all</Link>
            </div>
            <div className="railsec">
              <div className="railhead">Top languages</div>
              <div className="langrow"><span className="gdot">●</span> internships</div>
              <div className="langrow"><span className="ydot">●</span> events</div>
              <div className="langrow"><span className="rdot">●</span> newgrad</div>
            </div>
            <div className="railsec">
              <div className="railhead">Most used topics</div>
              <div className="topics">
                {topics.map(([r]) => (
                  <Link
                    key={r}
                    className="topic"
                    to={`/listings?repo=${encodeURIComponent(r)}&role=${encodeURIComponent(r)}&degree=any&country=all`}
                  >
                    {r.toLowerCase()}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

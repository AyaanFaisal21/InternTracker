// "/" landing page: light editorial identity, unlike the dark board.
// Static content; its only backend call is the page-open beacon.

import { useEffect } from "react";
import { Link } from "react-router-dom";
import { recordVisit } from "../api";
import LightShell from "../components/LightShell";
import "../styles/landing.css";

export default function Landing() {
  useEffect(() => {
    document.title = "Shortlist";
    recordVisit("landing");
  }, []);

  return (
    <LightShell page="page-landing">
      <section className="hero">
        <div className="eyebrow">Software engineering internships</div>
        <h1>
          Get yourself on the <span className="sl">ShortList</span>
        </h1>
        <div className="cta">
          <Link className="btn" to="/practice">practice technicals</Link>
          <Link className="btn solid" to="/listings">The List</Link>
        </div>
      </section>
      <section className="mission">
        <div className="mission-label">The mission</div>
        <p className="mission-body">
          Reaching the shortlist in SWE recruiting means applying early.
          Shortlist aggregates all your favorite CS repos, sites, and direct
          job boards. It covers every posting that can be covered
          autonomously. Be sure to check frequently.
        </p>
      </section>
    </LightShell>
  );
}

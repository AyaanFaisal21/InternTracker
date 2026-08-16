// "/practice" page: skeleton for the video-explanation grader. Placeholder
// zones only; no recording, upload, or analysis is wired up yet.

import { useEffect } from "react";
import { recordVisit } from "../api";
import LightShell from "../components/LightShell";
import "../styles/practice.css";

const ZONES = [
  {
    no: "01",
    title: "Problem prompt",
    frame:
      "An algorithm problem will appear here: statement, constraints, and examples.",
  },
  {
    no: "02",
    title: "Record or upload",
    frame:
      "Camera recording and file upload will live here. You talk through your solution as if an interviewer were across the table.",
  },
  {
    no: "03",
    title: "Analysis report",
    frame:
      "The graded report lands here: how you framed the problem, justified the approach, and handled complexity.",
  },
];

export default function Practice() {
  useEffect(() => {
    document.title = "Shortlist · Practice";
    recordVisit("practice");
  }, []);

  return (
    <LightShell page="page-practice">
      <div className="phead">
        <div className="eyebrow">In development</div>
        <h1>Practice technicals</h1>
        <p className="lede">
          Pick a problem, hit record, and explain your solution out loud the
          way you would in a real interview. An LLM-powered footage analyzer
          then grades the explanation itself: how you frame the problem,
          justify the approach, and walk the time and space complexity. Reps
          for the talking half of technical interviews.
        </p>
      </div>
      {ZONES.map((z) => (
        <section className="zone" key={z.no}>
          <div className="zone-head">
            <span className="zone-no">{z.no}</span>
            <h2>{z.title}</h2>
            <span className="soon">coming soon</span>
          </div>
          <div className="zone-frame">{z.frame}</div>
        </section>
      ))}
    </LightShell>
  );
}

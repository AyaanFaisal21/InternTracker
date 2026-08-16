// The one masthead every page shares: logo mark, ShortList wordmark, nav.
// LightShell renders it on the light pages; the dark board renders it
// directly. light.css scopes its styling under .masthead (not .page-light),
// so the paper band is identical wherever it sits.

import { Link } from "react-router-dom";
import "../styles/light.css";

export default function Masthead() {
  return (
    <header className="masthead">
      <div className="shell-inner masthead-row">
        <Link className="wordmark" to="/">
          {/* Mark: a margin-ruled list whose last line runs short. */}
          <svg className="mark" viewBox="0 0 20 20" aria-hidden="true">
            <rect className="m" x="1" y="2" width="2.4" height="16" />
            <rect x="7" y="3.2" width="12" height="2.4" />
            <rect x="7" y="8.8" width="12" height="2.4" />
            <rect x="7" y="14.4" width="6.5" height="2.4" />
          </svg>
          ShortList
        </Link>
        <nav className="mastnav">
          <Link to="/listings">The List</Link>
          <Link to="/practice">practice technicals</Link>
        </nav>
      </div>
    </header>
  );
}

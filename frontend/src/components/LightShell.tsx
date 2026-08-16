// Shared shell for the light pages (/ and /practice): paper background,
// logo masthead with nav, colophon footer. The dark board keeps TopBar.

import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import "../styles/light.css";

export default function LightShell({
  page,
  children,
}: {
  page: string;
  children: ReactNode;
}) {
  return (
    <div className={`page-light ${page}`}>
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
      <main className="shell-inner">{children}</main>
      <footer className="colophon">
        <div className="shell-inner">ShortList · New Brunswick, NJ</div>
      </footer>
    </div>
  );
}

// Shared shell for the light pages (/ and /practice): paper background,
// the shared masthead, colophon footer. The dark board renders Masthead
// on its own, without this shell.

import type { ReactNode } from "react";
import Masthead from "./Masthead";
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
      <Masthead />
      <main className="shell-inner">{children}</main>
      <footer className="colophon">
        <div className="shell-inner">ShortList · New Brunswick, NJ</div>
      </footer>
    </div>
  );
}

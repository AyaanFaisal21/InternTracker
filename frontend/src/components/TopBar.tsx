import { Link } from "react-router-dom";

/**
 * Top bar. Without `crumb` it renders the landing variant; with `crumb` it
 * renders the listings variant (brand plus " · <crumb>" breadcrumb).
 * The search input is a disabled placeholder, as in web.py.
 */
export default function TopBar({ crumb }: { crumb?: string }) {
  return (
    <div className="topbar">
      <span className="burger">☰</span>
      {crumb === undefined ? (
        <Link className="brand" to="/">
          <span className="ru">RU</span>employed
        </Link>
      ) : (
        <span className="brand">
          <Link className="brand" to="/">
            <span className="ru">RU</span>employed
          </Link>{" "}
          <span className="crumb">
            {" · "}
            <Link to="/listings">{crumb}</Link>
          </span>
        </span>
      )}
      <input className="topsearch" placeholder="Type / to search" disabled />
    </div>
  );
}

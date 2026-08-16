// Dark top bar for the board page: brand link home, a static crumb back to
// the master list, and the disabled search placeholder from web.py.

import { Link } from "react-router-dom";

export default function TopBar() {
  return (
    <div className="topbar">
      <span className="burger">☰</span>
      <span className="brand">
        <Link className="brand" to="/">
          <span className="ru">Short</span>list
        </Link>{" "}
        <span className="crumb">
          {" · "}
          <Link to="/listings">The List</Link>
        </span>
      </span>
      <input
        className="topsearch"
        placeholder="Type / to search"
        aria-label="Search"
        disabled
      />
    </div>
  );
}

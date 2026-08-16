import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import Landing from "./pages/Landing";
import Listings from "./pages/Listings";
import "./styles/base.css";

// Key the board by its query string: each preset URL mounts a fresh page,
// matching the full page loads of the Python server.
function ListingsRoute() {
  const { search } = useLocation();
  return <Listings key={search} />;
}

// No StrictMode: it double-fires effects in dev, which would double the
// /api/visit beacon that web.py sends once per page open.
createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/listings" element={<ListingsRoute />} />
      <Route path="*" element={<pre>not found</pre>} />
    </Routes>
  </BrowserRouter>,
);

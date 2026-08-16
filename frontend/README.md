# frontend

React port of the intake dashboard. Replaces the HTML that lives as strings in `src/intake/web.py`.

Two routes. `/` is the org profile. `/listings` is the board. Look and behavior match the Python pages.

## Stack

Vite, React, TypeScript, react-router-dom. No other runtime dependencies.

## Run

    npm install
    npm run dev

Dev server listens on http://localhost:5173.

## API

All data comes from the intake web server. The app calls four relative endpoints: `GET /api/postings`, `GET /api/suggestions`, `POST /api/suggest`, `POST /api/visit`.

`src/api.ts` holds every call and every payload type. To switch backends (for example a direct Supabase client), replace that one file.

In dev, Vite proxies `/api/*` to the backend. The target comes from the `VITE_API_TARGET` environment variable. Default is `http://localhost:8642`.

    # PowerShell
    $env:VITE_API_TARGET = "http://localhost:8000"; npm run dev

## Build

    npm run build

The build type-checks first, then bundles. Output lands in `dist/`.

Serve `dist/` from any static host. Two requirements:

1. Route `/api/*` to the intake server.
2. Serve `index.html` for unknown paths (SPA fallback), so `/listings` resolves on direct load.

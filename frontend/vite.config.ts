import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app always calls relative /api/* URLs. In production a reverse proxy
// routes them to the intake server. In dev, Vite proxies them to
// VITE_API_TARGET (default: the local intake server).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8642",
        changeOrigin: true,
      },
    },
  },
});

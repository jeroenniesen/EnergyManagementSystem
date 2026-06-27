import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Build output goes to ems/web/static/dist, which FastAPI serves (no runtime CDN).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
  },
  server: {
    // Dev server proxies API/health to the FastAPI backend (SPEC §11.6 fast UI iteration).
    proxy: {
      "/api": "http://localhost:8080",
      "/health": "http://localhost:8080",
    },
  },
});

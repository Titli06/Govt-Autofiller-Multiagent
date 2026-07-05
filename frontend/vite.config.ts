import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks to the API same-origin: Vite proxies /api -> backend, so the httpOnly
// refresh cookie stays SameSite=Lax with no CORS credential dance (SPEC.md §2, §11).
// In docker-compose the backend is reachable as http://api:8000; locally it's localhost.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});

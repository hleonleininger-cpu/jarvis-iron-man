/// <reference types="vitest/config" />
// Vite: Entwicklungsserver mit Proxy zum Core und Testumgebung (Vitest, jsdom).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Der Core prüft die Herkunft jeder Anfrage (CSRF-Schutz, ADR-018) und lehnt fremde Origins ab.
// Im Entwicklungsmodus läuft das HUD auf localhost:5173, also entfernt der Proxy die Browser-
// Header Origin und Sec-Fetch-*. Das gilt nur für `npm run dev` auf diesem Rechner; die
// Desktop-App (Block B) ruft den Core aus dem Rust-Teil und hat keinen Browser-Origin.
const CORE = process.env.JARVIS_CORE_URL ?? "http://127.0.0.1:8765";
const BROWSER_HEADERS = ["origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer"];

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // nur dieser Rechner, nie das Netz
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": {
        target: CORE,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => {
            for (const name of BROWSER_HEADERS) request.removeHeader(name);
          });
        },
      },
    },
  },
  preview: { host: "127.0.0.1", port: 5173, strictPort: true },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
  },
});

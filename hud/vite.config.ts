/// <reference types="vitest/config" />
// Vite: Entwicklungsserver mit Proxy zum Core und Testumgebung (Vitest, jsdom).
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { isOwnRequest } from "./src/dev/originGuard";

// Der Core prüft die Herkunft jeder Anfrage (CSRF-Schutz, ADR-018) und lehnt fremde Origins ab.
// Im Entwicklungsmodus läuft das HUD auf 127.0.0.1:5173. Damit der Core es annimmt, entfernt der
// Proxy die Browser-Header, aber NUR für Anfragen, die nachweislich vom HUD selbst kommen
// (originGuard); alle anderen bekommen 403, bevor sie den Proxy erreichen. Die Desktop-App
// (Block B) ruft den Core aus dem Rust-Teil und hat keinen Browser-Origin.
const CORE = process.env.JARVIS_CORE_URL ?? "http://127.0.0.1:8765";
const BROWSER_HEADERS = ["origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer"];

// Inhaltsrichtlinie für den gebauten Stand (im Entwicklungsmodus braucht Vite Inline-Skripte).
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; " +
  "base-uri 'none'; form-action 'none'; object-src 'none'";

function originGuard(): Plugin {
  return {
    name: "jarvis-origin-guard",
    // Ohne zurückgegebene Funktion läuft die Middleware VOR Vites eigenen (also vor dem Proxy).
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.startsWith("/v1") && !isOwnRequest(request.headers)) {
          response.statusCode = 403;
          response.end("Anfrage von einer fremden Seite.");
          return;
        }
        next();
      });
    },
  };
}

function contentSecurityPolicy(): Plugin {
  return {
    name: "jarvis-csp",
    // Nur für den Browser-Bau. In der Desktop-App setzt Tauri die CSP (tauri.conf.json); ein
    // zweites Meta-Tag würde dort die IPC (ipc:, http://ipc.localhost) sperren.
    apply: (_config, { command }) => command === "build" && process.env.TAURI_ENV_PLATFORM === undefined,
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: CSP }, injectTo: "head-prepend" },
    ],
  };
}

export default defineConfig({
  plugins: [originGuard(), react(), contentSecurityPolicy()],
  server: {
    host: "127.0.0.1", // nur dieser Rechner, nie das Netz
    port: 5173,
    strictPort: true,
    cors: false, // keine fremde Seite darf Antworten des Dev-Servers lesen
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
  preview: { host: "127.0.0.1", port: 5173, strictPort: true, cors: false },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
  },
});

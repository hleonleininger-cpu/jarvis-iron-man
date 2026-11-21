import { describe, expect, it } from "vitest";
import capabilities from "../src-tauri/capabilities/default.json";
import config from "../src-tauri/tauri.conf.json";

describe("Desktop-App: Sicherheitseinstellungen", () => {
  it("die Webview darf nur die Tauri-IPC erreichen, nichts im Netz", () => {
    const csp = config.app.security.csp;
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src ipc: http://ipc.localhost;");
    expect(csp).not.toMatch(/https?:\/\/(?!ipc\.localhost)/);
    expect(csp).not.toContain("unsafe");
  });

  it("die Webview darf nur Ereignisse empfangen, keine Plugin-Befehle aufrufen", () => {
    expect(capabilities.permissions).toEqual(["core:event:allow-listen", "core:event:allow-unlisten"]);
    expect(config.app.withGlobalTauri).toBe(false);
  });
});

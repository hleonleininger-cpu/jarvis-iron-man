import { describe, expect, it } from "vitest";
import { isOwnRequest } from "../src/dev/originGuard";

describe("Herkunftsprüfung des Entwicklungs-Proxys", () => {
  it("lässt das HUD selbst und Programme ohne Browser durch", () => {
    expect(isOwnRequest({ origin: "http://127.0.0.1:5173", "sec-fetch-site": "same-origin" })).toBe(true);
    expect(isOwnRequest({ origin: "http://localhost:5173" })).toBe(true);
    expect(isOwnRequest({ "sec-fetch-site": "same-origin" })).toBe(true); // GET ohne Origin
    expect(isOwnRequest({})).toBe(true); // curl, Sprachclient
  });

  it("weist fremde Seiten ab, auch auf localhost", () => {
    expect(isOwnRequest({ origin: "https://boese.example" })).toBe(false);
    expect(isOwnRequest({ origin: "http://localhost:3000" })).toBe(false);
    expect(isOwnRequest({ "sec-fetch-site": "cross-site" })).toBe(false); // no-cors ohne Origin
    expect(isOwnRequest({ "sec-fetch-site": "same-site" })).toBe(false); // anderer localhost-Port
    expect(isOwnRequest({ origin: "null" })).toBe(false); // Sandbox, data:-Seiten
    expect(isOwnRequest({ origin: ["https://boese.example"] })).toBe(false);
  });
});

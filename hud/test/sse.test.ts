import { describe, expect, it } from "vitest";
import { SseParser } from "../src/api/sse";

describe("SseParser", () => {
  it("setzt Ereignisse aus beliebig geschnittenen Stücken zusammen", () => {
    const parser = new SseParser();
    expect(parser.feed('event: delta\ndata: {"te')).toEqual([]);
    expect(parser.feed('xt":"Hal"}\n\nevent: delta\ndata: {"text":"lo"}\n')).toEqual([
      { event: "delta", data: { text: "Hal" } },
    ]);
    expect(parser.feed("\n")).toEqual([{ event: "delta", data: { text: "lo" } }]);
  });

  it("übergeht Lebenszeichen, CRLF und kaputte Daten", () => {
    const parser = new SseParser();
    const events = parser.feed(': ping\r\n\r\nevent: x\r\ndata: kein json\r\n\r\nevent: ok\r\ndata: {"a":1}\r\n\r\n');
    expect(events).toEqual([{ event: "ok", data: { a: 1 } }]);
  });

  it("nimmt nur Objekte an, keine Listen oder Zahlen", () => {
    expect(new SseParser().feed("event: a\ndata: [1]\n\nevent: b\ndata: 3\n\n")).toEqual([]);
  });
});

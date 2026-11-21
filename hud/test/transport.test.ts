import { describe, expect, it, vi } from "vitest";
import { createFetchTransport, type ServerEvent } from "../src/api/transport";

function sseResponse(text: string, status = 200): Response {
  const bytes = new TextEncoder().encode(text);
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, 7));
      controller.enqueue(bytes.slice(7));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

describe("fetch-Transport", () => {
  it("liest den Chat-Strom und schickt die Sitzung mit", async () => {
    const fetcher = vi.fn(async () => sseResponse('event: delta\ndata: {"text":"Hi"}\n\nevent: done\ndata: {"session_id":"s"}\n\n'));
    const events: ServerEvent[] = [];
    await createFetchTransport("", fetcher).chat("Hallo", "s0", (e) => events.push(e));
    expect(events.map((e) => e.event)).toEqual(["delta", "done"]);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/v1/chat");
    expect(JSON.parse(init.body as string)).toEqual({ message: "Hallo", session_id: "s0" });
  });

  it("meldet einen Fehler statt zu werfen", async () => {
    const events: ServerEvent[] = [];
    await createFetchTransport("", vi.fn(async () => sseResponse("", 403))).chat("x", null, (e) => events.push(e));
    expect(events[0]?.event).toBe("error");
    await createFetchTransport("", vi.fn(async () => Promise.reject(new Error("weg")))).chat("x", null, (e) => events.push(e));
    expect(events[1]?.data.message).toBe("Der Core ist nicht erreichbar.");
  });

  it("Not-Aus: true bei Erfolg, false wenn der Core nicht antwortet", async () => {
    expect(await createFetchTransport("", vi.fn(async () => new Response("{}"))).stop()).toBe(true);
    const hanging = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new Error("abgebrochen")))),
    );
    expect(await createFetchTransport("", hanging as unknown as typeof fetch).stop(20)).toBe(false);
  });

  it("abonniert /v1/events und meldet die Verbindung", async () => {
    const fetcher = vi.fn(async () => sseResponse('event: ready\ndata: {}\n\nevent: snapshot\ndata: {}\n\n'));
    const events: string[] = [];
    const connection: boolean[] = [];
    const stop = createFetchTransport("", fetcher).subscribe(
      (e) => events.push(e.event),
      (up) => connection.push(up),
    );
    await vi.waitFor(() => expect(events).toEqual(["ready", "snapshot"]));
    await vi.waitFor(() => expect(connection).toEqual([true, false])); // Strom zu Ende: getrennt
    stop();
    expect((fetcher.mock.calls[0] as unknown as [string])[0]).toBe("/v1/events");
  });
});

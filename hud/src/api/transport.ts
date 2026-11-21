// Transportschicht des HUD (PLAN-M3 §4): eine Schnittstelle, zwei Umsetzungen.
// Block A: `fetch` gegen den Core (im Entwicklungsmodus über den Vite-Proxy).
// Block B: Tauri (`invoke`/`listen`), dort ruft der Rust-Teil den Core.

import { SseParser, type ServerEvent } from "./sse";

export type { ServerEvent } from "./sse";

export type Listener = (event: ServerEvent) => void;

export interface Transport {
  /** Dauerstrom /v1/events (Status, Rückfragen, Erinnerungen); verbindet sich selbst neu. */
  subscribe(onEvent: Listener, onConnection: (connected: boolean, problem?: string) => void): () => void;
  /** Eine Chat-Anfrage; Ereignisse wie bei /v1/chat. Endet mit `done` oder `error`. */
  chat(message: string, sessionId: string | null, onEvent: Listener, signal?: AbortSignal): Promise<void>;
  confirm(id: string, approve: boolean): Promise<boolean>;
  /** Not-Aus. false, wenn der Core nicht in `timeoutMs` antwortet. */
  stop(timeoutMs?: number): Promise<boolean>;
}

const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 10_000;

function problemOf(status: number): string {
  if (status === 401) return "Der Core verlangt ein Zugangstoken; das HUD spricht nur mit localhost ohne Token.";
  if (status === 403) return "Der Core lehnt die Herkunft ab (CSRF-Schutz). Bitte über `npm run dev` öffnen.";
  return `Der Core antwortet mit Status ${status}.`;
}

async function readStream(response: Response, onEvent: Listener, signal?: AbortSignal): Promise<void> {
  const body = response.body;
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    while (!signal?.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.feed(decoder.decode(value, { stream: true }))) onEvent(event);
    }
  } finally {
    reader.releaseLock();
  }
}

export function createFetchTransport(base = "", fetcher: typeof fetch = fetch.bind(globalThis)): Transport {
  return {
    subscribe(onEvent, onConnection) {
      const controller = new AbortController();
      let delay = RETRY_MIN_MS;
      const loop = async (): Promise<void> => {
        while (!controller.signal.aborted) {
          let problem = "Der Core ist nicht erreichbar.";
          try {
            const response = await fetcher(`${base}/v1/events`, { signal: controller.signal });
            if (response.ok) {
              onConnection(true);
              delay = RETRY_MIN_MS;
              await readStream(response, onEvent, controller.signal);
              problem = "Die Verbindung zum Core ist abgerissen.";
            } else {
              problem = problemOf(response.status);
            }
          } catch {
            // Netzfehler oder Abbruch; unten entscheidet das Signal
          }
          if (controller.signal.aborted) return;
          onConnection(false, problem);
          await new Promise((resolve) => setTimeout(resolve, delay));
          delay = Math.min(delay * 2, RETRY_MAX_MS);
        }
      };
      void loop();
      return () => controller.abort();
    },

    async chat(message, sessionId, onEvent, signal) {
      const body: Record<string, string> = { message };
      if (sessionId) body.session_id = sessionId;
      let response: Response;
      try {
        response = await fetcher(`${base}/v1/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          ...(signal ? { signal } : {}),
        });
      } catch {
        if (!signal?.aborted) onEvent({ event: "error", data: { message: "Der Core ist nicht erreichbar." } });
        return;
      }
      if (!response.ok) {
        onEvent({ event: "error", data: { message: problemOf(response.status) } });
        return;
      }
      try {
        await readStream(response, onEvent, signal);
      } catch {
        // Not-Aus bricht den Strom absichtlich ab; sonst ist die Verbindung abgerissen.
        if (!signal?.aborted) onEvent({ event: "error", data: { message: "Die Verbindung zum Core ist abgerissen." } });
      }
    },

    async confirm(id, approve) {
      try {
        const response = await fetcher(`${base}/v1/confirm/${encodeURIComponent(id)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approve }),
        });
        return response.ok;
      } catch {
        return false;
      }
    },

    async stop(timeoutMs = 5000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetcher(`${base}/v1/stop`, { method: "POST", signal: controller.signal });
        return response.ok;
      } catch {
        return false;
      } finally {
        clearTimeout(timer);
      }
    },
  };
}

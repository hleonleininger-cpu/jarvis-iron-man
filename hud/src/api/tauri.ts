// Transport für die Desktop-App (Block B): der Rust-Teil spricht mit dem Core, die Oberfläche
// bekommt nur Ereignisse. Die Webview selbst darf keine Netzverbindung öffnen (CSP, ADR-024).

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { ServerEvent } from "./sse";
import type { DesktopHandlers, Listener, Transport } from "./transport";

interface Connection {
  connected: boolean;
  problem: string | null;
}

interface ChatEvent extends ServerEvent {
  id: number;
}

/** Meldet alle Abos ab, auch solche, die beim Abmelden noch nicht fertig angemeldet waren. */
function cleanup(pending: Promise<UnlistenFn>[]): () => void {
  return () => {
    for (const promise of pending) void promise.then((unlisten) => unlisten());
  };
}

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function createTauriTransport(): Transport {
  let nextChat = 1;
  return {
    subscribe(onEvent, onConnection) {
      const pending = [
        listen<ServerEvent>("hud://server", (e) => onEvent(e.payload)),
        listen<Connection>("hud://connection", (e) => onConnection(e.payload.connected, e.payload.problem ?? undefined)),
      ];
      // Erst zuhören, dann den Strom starten (sonst gehen ready/snapshot verloren).
      void Promise.all(pending).then(() => invoke("events_start"));
      return cleanup(pending);
    },

    async chat(message, sessionId, onEvent: Listener, signal) {
      const id = nextChat++;
      let finish: () => void = () => {};
      const ended = new Promise<void>((resolve) => (finish = resolve));
      const pending = [
        listen<ChatEvent>("hud://chat", (e) => {
          if (e.payload.id === id) onEvent({ event: e.payload.event, data: e.payload.data });
        }),
        listen<number>("hud://chat_end", (e) => {
          if (e.payload === id) finish();
        }),
      ];
      const stopListening = cleanup(pending);
      const cancel = () => void invoke("chat_cancel", { id }).finally(finish);
      try {
        await Promise.all(pending);
        if (signal?.aborted) return;
        signal?.addEventListener("abort", cancel, { once: true });
        await invoke("chat_start", { id, message, sessionId });
        await ended;
      } catch {
        if (!signal?.aborted) onEvent({ event: "error", data: { message: "Die Anfrage konnte nicht gestartet werden." } });
      } finally {
        signal?.removeEventListener("abort", cancel);
        stopListening();
      }
    },

    async confirm(id, approve) {
      try {
        return await invoke<boolean>("confirm", { id, approve });
      } catch {
        return false;
      }
    },

    async stop(timeoutMs = 6000) {
      const timeout = new Promise<boolean>((resolve) => setTimeout(() => resolve(false), timeoutMs));
      try {
        return await Promise.race([invoke<boolean>("stop"), timeout]);
      } catch {
        return false;
      }
    },

    desktop(handlers: DesktopHandlers) {
      const pending = [
        listen<boolean>("hud://overlay", (e) => handlers.overlay(e.payload)),
        listen<string>("hud://notice", (e) => handlers.notice(e.payload)),
        listen<boolean>("hud://stop_result", (e) => handlers.stopResult(e.payload)),
      ];
      // Hinweise vom Start (bevor die Oberfläche zuhörte) einmal nachholen.
      void invoke<string[]>("shortcut_problems")
        .then((problems) => problems.forEach(handlers.notice))
        .catch(() => {});
      return cleanup(pending);
    },
  };
}

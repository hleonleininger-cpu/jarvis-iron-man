import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Transport } from "./api/transport";
import { ActivityList } from "./components/ActivityList";
import { ChatPanel } from "./components/ChatPanel";
import { ConfirmCard } from "./components/ConfirmCard";
import { StateRing } from "./components/StateRing";
import { StopButton } from "./components/StopButton";
import { initialState, micOf, reducer, ringOf } from "./state";

const TICK_MS = 2000; // prüft, ob der Sprachclient noch lebt
const STOP_NOTICE_MS = 4000;

export function App({ transport, now = Date.now }: { transport: Transport; now?: () => number }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [clock, setClock] = useState(now);
  const chatAbort = useRef<AbortController | null>(null);

  useEffect(
    () =>
      transport.subscribe(
        (event) => dispatch({ type: "server", event, now: now() }),
        (connected, problem) => dispatch({ type: "connection", connected, problem }),
      ),
    [transport, now],
  );

  useEffect(() => {
    const timer = setInterval(() => setClock(now()), TICK_MS);
    return () => clearInterval(timer);
  }, [now]);

  const stop = useCallback(() => {
    chatAbort.current?.abort();
    void transport.stop().then((ok) => dispatch({ type: "stop_result", ok, now: now() }));
  }, [transport, now]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === ".") {
        event.preventDefault();
        stop();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [stop]);

  const send = (message: string) => {
    const controller = new AbortController();
    chatAbort.current = controller;
    dispatch({ type: "chat_start", message });
    void transport
      .chat(message, state.sessionId, (event) => dispatch({ type: "chat", event }), controller.signal)
      .finally(() => dispatch({ type: "chat_end" }));
  };

  const answer = (id: string, approve: boolean) => {
    dispatch({ type: "confirm_answered", id });
    void transport.confirm(id, approve);
  };

  const current = Math.max(clock, now());
  const stopped = state.stoppedAt !== null && current - state.stoppedAt < STOP_NOTICE_MS;

  return (
    <div className="hud">
      <header className="top">
        <h1>J.A.R.V.I.S.</h1>
        <span className={`link link-${state.connected ? "up" : "down"}`}>
          {state.connected ? "Core verbunden" : (state.problem ?? "Verbinde …")}
        </span>
        <StopButton onStop={stop} failed={state.stopFailed} />
      </header>
      {stopped ? (
        <div className="stopped" role="alert">
          Not-Aus: alles angehalten.
        </div>
      ) : null}
      <main>
        <div className="center">
          <StateRing ring={ringOf(state, current)} mic={micOf(state, current)} />
          <div className="confirms">
            {state.confirms.map((item) => (
              <ConfirmCard key={item.id} item={item} onAnswer={answer} />
            ))}
          </div>
        </div>
        <ActivityList activities={state.activities} />
        <ChatPanel messages={state.messages} busy={state.chatting} onSend={send} />
      </main>
    </div>
  );
}

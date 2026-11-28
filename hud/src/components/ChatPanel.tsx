import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Message } from "../state";

interface Props {
  messages: Message[];
  busy: boolean;
  onSend: (text: string) => void;
}

/** Gespräch mit Jarvis per Tastatur. Antworten erscheinen als schlichter Text. */
export function ChatPanel({ messages, busy, onSend }: Props) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [messages]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    onSend(message);
    setText("");
  };

  return (
    <section className="panel chat" aria-label="Gespräch">
      <h2>Gespräch</h2>
      <div className="log" aria-live="polite">
        {messages.map((m, index) => (
          <div key={index} className={`msg msg-${m.role}`}>
            {m.text}
          </div>
        ))}
        <div ref={end} />
      </div>
      <form onSubmit={submit}>
        <input
          aria-label="Nachricht an Jarvis"
          value={text}
          maxLength={20000}
          placeholder={busy ? "Jarvis antwortet …" : "Nachricht an Jarvis"}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" disabled={busy || !text.trim()}>
          Senden
        </button>
      </form>
    </section>
  );
}

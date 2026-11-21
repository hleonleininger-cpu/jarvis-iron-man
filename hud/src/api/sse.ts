// Liest Server-Sent Events stückweise. EventSource reicht nicht: /v1/chat ist ein POST, und
// Stücke können mitten in einer Zeile enden.

export interface ServerEvent {
  event: string;
  data: Record<string, unknown>;
}

export class SseParser {
  private buffer = "";

  /** Nimmt ein Textstück an und gibt alle darin vollständigen Ereignisse zurück. */
  feed(chunk: string): ServerEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const events: ServerEvent[] = [];
    let end = this.buffer.indexOf("\n\n");
    while (end !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const parsed = parseBlock(block);
      if (parsed) events.push(parsed);
      end = this.buffer.indexOf("\n\n");
    }
    return events;
  }
}

function parseBlock(block: string): ServerEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // Lebenszeichen (": ping")
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (data.length === 0) return null;
  try {
    const value: unknown = JSON.parse(data.join("\n"));
    if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
    return { event, data: value as Record<string, unknown> };
  } catch {
    return null; // kaputtes Ereignis überspringen, den Strom nicht abbrechen
  }
}

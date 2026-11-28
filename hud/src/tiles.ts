// Live-Kacheln (M3.6) vom Core: GET /v1/tiles. Typen und Lesen ohne Vertrauen in die Form.

export interface Missing {
  ok: false;
  grund: string;
}

export interface SystemTile {
  ok: true;
  cpu: number;
  ram: number;
  akku: { prozent: number; laedt: boolean } | null;
}

export interface EventEntry {
  titel: string;
  start: string; // ISO mit Zeitzone
  ganztaegig: boolean;
  ort: string | null;
}

export interface TaskEntry {
  text: string;
  faellig: string | null;
  ueberfaellig: boolean;
}

export interface Tiles {
  stand: string | null;
  aktuell: boolean;
  system: SystemTile | Missing;
  termine: { ok: true; eintraege: EventEntry[] } | Missing;
  missionen: { ok: true; anzahl: number; eintraege: TaskEntry[] } | Missing;
  fokus: Missing;
}

const missing = (grund: string): Missing => ({ ok: false, grund });
const obj = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const text = (value: unknown): string => (typeof value === "string" ? value : "");
const num = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
const list = (value: unknown): Record<string, unknown>[] => (Array.isArray(value) ? value.map(obj) : []);

function reason(value: Record<string, unknown>): Missing {
  return missing(text(value.grund) || "keine Daten");
}

/** Liest die Antwort des Core; alles Unerwartete wird zu "keine Daten", nie zu erfundenen Werten. */
export function parseTiles(raw: unknown): Tiles {
  const data = obj(raw);
  const system = obj(data.system);
  const termine = obj(data.termine);
  const missionen = obj(data.missionen);
  const akku = system.akku === null || system.akku === undefined ? null : obj(system.akku);
  return {
    stand: text(data.stand) || null,
    aktuell: data.aktuell === true,
    system:
      system.ok === true
        ? {
            ok: true,
            cpu: num(system.cpu),
            ram: num(system.ram),
            akku: akku ? { prozent: num(akku.prozent), laedt: akku.laedt === true } : null,
          }
        : reason(system),
    termine:
      termine.ok === true
        ? {
            ok: true,
            eintraege: list(termine.eintraege).map((e) => ({
              titel: text(e.titel),
              start: text(e.start),
              ganztaegig: e.ganztaegig === true,
              ort: text(e.ort) || null,
            })),
          }
        : reason(termine),
    missionen:
      missionen.ok === true
        ? {
            ok: true,
            anzahl: num(missionen.anzahl),
            eintraege: list(missionen.eintraege).map((e) => ({
              text: text(e.text),
              faellig: text(e.faellig) || null,
              ueberfaellig: e.ueberfaellig === true,
            })),
          }
        : reason(missionen),
    fokus: reason(obj(data.fokus)),
  };
}

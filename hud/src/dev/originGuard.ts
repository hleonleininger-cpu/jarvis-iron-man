// Prüfung für den Entwicklungs-Proxy (nur `npm run dev`). Der Proxy entfernt Origin und
// Sec-Fetch-* vor der Weitergabe an den Core; vorher muss feststehen, dass die Anfrage vom HUD
// selbst kommt. Sonst könnte jede Webseite im Browser über den Proxy den Not-Aus auslösen oder
// Anfragen stellen (CSRF), denn der Core sieht die Herkunft danach nicht mehr.

export const HUD_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"] as const;

type HeaderValue = string | string[] | undefined;

const first = (value: HeaderValue): string | undefined => (Array.isArray(value) ? value[0] : value);

/** true, wenn die Anfrage vom HUD selbst oder von einem Programm ohne Browser stammt. */
export function isOwnRequest(headers: Record<string, HeaderValue>): boolean {
  const origin = first(headers["origin"]);
  const site = first(headers["sec-fetch-site"]);
  if (site !== undefined && site !== "same-origin" && site !== "none") return false;
  if (origin !== undefined && !(HUD_ORIGINS as readonly string[]).includes(origin)) return false;
  return true;
}

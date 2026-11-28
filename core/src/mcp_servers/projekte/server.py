"""MCP-Server `projekte`: Ideen und Aufträge für Nebeneinkünfte, lokal (ADR-016).

Hält fest, was der Nutzer vorhat (Produkt-, App- und Auftragsideen) und welche Aufträge laufen
(Kunde, Frist, Stand, Betrag), und legt Entwürfe als Textdateien ab. Es wird nichts veröffentlicht,
gesendet oder bezahlt: JARVIS bereitet vor, der Nutzer macht den Rest.

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Kein Netz. Inhalte
bleiben in der Datei; ins Audit-Log kommt wie immer nur der Werkzeugname.
"""

import argparse
import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from mcp_servers.projekte.store import (
    IDEA,
    IDEA_STATUS,
    LEAD,
    LEAD_STATUS,
    Item,
    ProjectStore,
)

TZ = ZoneInfo("Europe/Berlin")
DEFAULT_FILE = Path.home() / ".jarvis" / "projekte.sqlite3"
DEFAULT_DRAFTS = Path.home() / ".jarvis" / "entwuerfe"
CATEGORIES = ("produkt", "app", "auftrag")
MAX_TITLE_LENGTH = 200
MAX_DETAIL_LENGTH = 2000
MAX_DRAFT_LENGTH = 50_000
MAX_LISTED = 50
MAX_AMOUNT_EUR = Decimal("1000000")
FIRST_YEAR, LAST_YEAR = 1970, 2200

mcp = MCPServer("projekte", log_level="WARNING")
_store: ProjectStore | None = None
_drafts_dir: Path = DEFAULT_DRAFTS


def store() -> ProjectStore:
    """Wird in Tests ersetzt; im Betrieb von main() gesetzt."""
    global _store
    if _store is None:
        _store = ProjectStore(DEFAULT_FILE)
    return _store


def _fail(message: str, reason: str = "invalid_input") -> dict[str, Any]:
    return {"ok": False, "fehler": message, "grund": reason}


def _text(value: str | None, what: str, limit: int, required: bool = False) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise ValueError(f"{what} fehlt.")
        return None
    if len(cleaned) > limit:
        raise ValueError(f"{what} darf höchstens {limit} Zeichen lang sein.")
    return cleaned


def _due(frist: str | None) -> date | None:
    if frist is None or not frist.strip():
        return None
    try:
        due = date.fromisoformat(frist.strip()[:10])
    except ValueError:
        raise ValueError(f"Ungültiges Datum '{frist}'. Erwartet JJJJ-MM-TT.") from None
    if not FIRST_YEAR <= due.year <= LAST_YEAR:
        raise ValueError(f"Das Datum {due:%Y-%m-%d} ist nicht plausibel.")
    return due


def _decimal_text(text: str) -> str:
    """Deutsche Schreibweise lesen: '1.234,56' und '1,5' ebenso wie '1234.56'. Ein Punkt mit genau
    drei Ziffern dahinter ('1.500') gilt als Tausendertrenner, nicht als Komma."""
    text = text.strip().replace(" ", "")
    if "," in text:
        return text.replace(".", "").replace(",", ".")
    if re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", text):
        return text.replace(".", "")
    return text


def to_cents(betrag_eur: str | float | None) -> int | None:
    """Betrag in Euro (z. B. 250 oder '249,90') -> Cent. Negativ, unlesbar oder riesig: Fehler."""
    if betrag_eur is None or str(betrag_eur).strip() == "":
        return None
    try:
        amount = Decimal(_decimal_text(str(betrag_eur)))
    except InvalidOperation:
        raise ValueError(f"Ungültiger Betrag '{betrag_eur}'. Erwartet eine Zahl in Euro.") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT_EUR:
        raise ValueError("Der Betrag muss zwischen 0 und 1.000.000 Euro liegen.")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _euro(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


def _item_dict(item: Item, today: date) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": item.id, "titel": item.title, "status": item.status}
    if item.category:
        entry["kategorie"] = item.category
    if item.detail:
        entry["notiz"] = item.detail
    if item.due:
        entry["frist"] = item.due.isoformat()
        if item.status in ("offen", "angebot") and item.due < today:
            entry["ueberfaellig"] = True
    if item.amount_cents is not None:
        entry["betrag"] = _euro(item.amount_cents)
    return entry


def add_idea_data(
    titel: str, kategorie: str | None = None, notiz: str | None = None
) -> dict[str, Any]:
    try:
        title = _text(titel, "Der Titel", MAX_TITLE_LENGTH, required=True)
        detail = _text(notiz, "Die Notiz", MAX_DETAIL_LENGTH)
        category = (kategorie or "").strip().lower() or None
        if category is not None and category not in CATEGORIES:
            raise ValueError(
                f"Unbekannte Kategorie '{kategorie}'. Erlaubt: {', '.join(CATEGORIES)}."
            )
    except ValueError as exc:
        return _fail(str(exc))
    assert title is not None
    item = store().add(IDEA, title, detail, category, None, None)
    return {"ok": True, "gespeichert": f"Idee: {item.title}", "id": item.id}


def add_lead_data(
    kunde: str,
    beschreibung: str | None = None,
    frist: str | None = None,
    betrag_eur: str | float | None = None,
) -> dict[str, Any]:
    try:
        title = _text(kunde, "Der Kunde", MAX_TITLE_LENGTH, required=True)
        detail = _text(beschreibung, "Die Beschreibung", MAX_DETAIL_LENGTH)
        due, cents = _due(frist), to_cents(betrag_eur)
    except ValueError as exc:
        return _fail(str(exc))
    assert title is not None
    item = store().add(LEAD, title, detail, None, due, cents)
    return {"ok": True, "gespeichert": f"Auftrag: {item.title}", "id": item.id}


def list_ideas_data(status: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    return _list(IDEA, status, IDEA_STATUS, now)


def list_leads_data(status: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    result = _list(LEAD, status, LEAD_STATUS, now)
    if result["ok"]:
        leads = store().entries(LEAD)
        paid = sum(i.amount_cents or 0 for i in leads if i.status == "bezahlt")
        to_collect = sum(i.amount_cents or 0 for i in leads if i.status == "gewonnen")
        result["summe_bezahlt"] = _euro(paid)
        result["summe_gewonnen_unbezahlt"] = _euro(to_collect)
    return result


def _list(
    kind: str, status: str | None, allowed: tuple[str, ...], now: datetime | None
) -> dict[str, Any]:
    wanted = (status or "").strip().lower()
    if wanted and wanted not in allowed:
        return _fail(f"Unbekannter Status '{status}'. Erlaubt: {', '.join(allowed)}.")
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    items = [i for i in store().entries(kind) if not wanted or i.status == wanted]
    result: dict[str, Any] = {
        "ok": True,
        "anzahl": len(items),
        "eintraege": [_item_dict(i, today) for i in items[:MAX_LISTED]],
    }
    if len(items) > MAX_LISTED:
        result["hinweis"] = f"Nur die ersten {MAX_LISTED} von {len(items)} Einträgen gezeigt."
    return result


def set_status_data(id: int, status: str) -> dict[str, Any]:
    item = store().get(id)
    if item is None:
        return _fail(f"Es gibt keinen Eintrag mit der Nummer {id}.", "not_found")
    allowed = IDEA_STATUS if item.kind == IDEA else LEAD_STATUS
    wanted = status.strip().lower()
    if wanted not in allowed:
        return _fail(f"Unbekannter Status '{status}'. Erlaubt: {', '.join(allowed)}.")
    updated = store().set_status(id, wanted)
    assert updated is not None
    return {"ok": True, "id": id, "titel": updated.title, "status": updated.status}


# Unter Windows sind diese Namen (auch mit Endung) Geräte, keine Dateien.
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{n}" for n in range(10)),
    *(f"lpt{n}" for n in range(10)),
}


def _slug(title: str) -> str:
    ascii_title = (
        unicodedata.normalize("NFKD", title.replace("ß", "ss")).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60] or "entwurf"
    return f"entwurf-{slug}" if slug in _RESERVED else slug


def save_draft_data(titel: str, text: str) -> dict[str, Any]:
    """Legt einen Entwurf als Markdown-Datei im Entwurfsordner ab. Überschreibt nie etwas."""
    try:
        title = _text(titel, "Der Titel", MAX_TITLE_LENGTH, required=True)
        body = _text(text, "Der Text", MAX_DRAFT_LENGTH, required=True)
    except ValueError as exc:
        return _fail(str(exc))
    assert title is not None and body is not None
    _drafts_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(title)
    for number in range(1, 1000):
        name = f"{base}.md" if number == 1 else f"{base}-{number}.md"
        path = _drafts_dir / name
        try:
            with path.open("x", encoding="utf-8") as fh:  # "x": nie eine vorhandene Datei
                fh.write(f"# {title}\n\n{body}\n")
        except FileExistsError:
            continue
        return {"ok": True, "gespeichert": f"Entwurf: {title}", "datei": str(path)}
    return _fail("Es gibt schon zu viele Entwürfe mit diesem Titel.", "too_many")


@mcp.tool()
def add_idea(titel: str, kategorie: str | None = None, notiz: str | None = None) -> dict[str, Any]:
    """Merkt eine Idee für Nebeneinkünfte. kategorie: produkt, app oder auftrag."""
    return add_idea_data(titel, kategorie, notiz)


@mcp.tool()
def list_ideas(status: str | None = None) -> dict[str, Any]:
    """Liest die Ideen. status optional: neu, in_arbeit, veroeffentlicht, verworfen."""
    return list_ideas_data(status)


@mcp.tool()
def add_lead(
    kunde: str,
    beschreibung: str | None = None,
    frist: str | None = None,
    betrag_eur: str | float | None = None,
) -> dict[str, Any]:
    """Merkt einen Auftrag. frist 'JJJJ-MM-TT', betrag_eur in Euro. Für eine Meldung zur Frist
    zusätzlich add_note mit erinnern_um nutzen."""
    return add_lead_data(kunde, beschreibung, frist, betrag_eur)


@mcp.tool()
def list_leads(status: str | None = None) -> dict[str, Any]:
    """Liest die Aufträge samt Summen. status optional: offen, angebot, gewonnen, bezahlt,
    abgelehnt."""
    return list_leads_data(status)


@mcp.tool()
def set_project_status(id: int, status: str) -> dict[str, Any]:
    """Setzt den Stand einer Idee oder eines Auftrags (Werte wie bei list_ideas/list_leads); id
    aus der jeweiligen Liste."""
    return set_status_data(id, status)


@mcp.tool()
def save_draft(titel: str, text: str) -> dict[str, Any]:
    """Legt einen Entwurf (Gliederung, Text, Angebot) als Datei ab. Überschreibt nie etwas."""
    return save_draft_data(titel, text)


def main() -> None:
    global _store, _drafts_dir
    parser = argparse.ArgumentParser(prog="mcp_servers.projekte")
    parser.add_argument("--datei", type=Path, default=DEFAULT_FILE, help="SQLite-Datei")
    parser.add_argument("--entwuerfe", type=Path, default=DEFAULT_DRAFTS, help="Entwurfsordner")
    args = parser.parse_args()
    _store = ProjectStore(args.datei.expanduser())
    _drafts_dir = args.entwuerfe.expanduser()
    mcp.run(transport="stdio")

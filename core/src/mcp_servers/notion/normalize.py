"""Normalisierung: rohe Notion-Seiten -> kompakte, modellfreundliche Einträge.

Rohantworten der API gehen nie direkt ans Modell. Schema-Wissen (aus dem alten Projekt jarvis-os,
notionAdapter.js): Missionen (Mission, Status, Priorität, Bereich, Fällig), Reading Vault (Buch,
Kapitel, Fortschritt, Status), Schulmissionen (Aufgabe, Fach (Klartext)/Fach, Eintragstyp, Fällig,
Uhrzeit, Status). Die Logik ist schema-unabhängig: Titel und Typen werden über den Property-TYP
erkannt, bekannte Property-NAMEN dienen nur der Auswahl der wichtigsten Felder. Fehlende oder
unbekannte Felder werden übersprungen, nie geraten.
"""

import json
import re
from typing import Any

from mcp_servers.notion.jsonutil import as_dict, as_list

MAX_TEXT_LENGTH = 300
MAX_FIELDS = 6
OUTPUT_BUDGET = 3500  # Zeichen (JSON) je Werkzeugantwort

DATE_NAMES = ("fällig", "datum", "date", "due", "termin")
IMPORTANT_FIELDS = (
    "priorität", "bereich", "eintragstyp", "fach (klartext)", "fach", "uhrzeit", "kapitel",
    "fortschritt", "buch", "autor", "tags",
)  # fmt: skip
DONE_STATUS = {"erledigt", "archiv", "archiviert", "abgeschlossen", "fertig", "done", "complete"}
NEGATIONS = {"nicht", "noch", "not", "kein", "keine", "un"}
PRIORITY_RANK = {"hoch": 0, "mittel": 1, "niedrig": 2}

Value = str | float | bool | list[str]


def _plain(rich: object) -> str:
    return "".join(str(as_dict(part).get("plain_text", "")) for part in as_list(rich))


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT_LENGTH else text[:MAX_TEXT_LENGTH] + "…"


def _name(obj: object) -> str | None:
    value = as_dict(obj).get("name")
    return value if isinstance(value, str) else None


def _names(items: object) -> list[str]:
    return [n for n in (_name(o) for o in as_list(items)) if n]


def property_value(prop: object) -> Value | None:
    """Wert einer Property nach ihrem Typ; unbekannte Typen und leere Werte -> None."""
    data = as_dict(prop)
    kind = data.get("type")
    if not isinstance(kind, str):
        return None
    inner: Any = data.get(kind)
    value: Value | None = None
    if kind in ("title", "rich_text"):
        text = _plain(inner)
        value = _clip(text) if text else None
    elif kind in ("select", "status"):
        value = _name(inner)
    elif kind in ("multi_select", "people"):
        value = _names(inner) or None
    elif kind == "date":
        start = as_dict(inner).get("start")
        value = start if isinstance(start, str) else None
    elif kind == "number":
        value = (
            float(inner) if isinstance(inner, int | float) and not isinstance(inner, bool) else None
        )
    elif kind == "checkbox":
        value = inner if isinstance(inner, bool) else None
    elif kind in ("url", "email", "phone_number"):
        value = inner if isinstance(inner, str) and inner else None
    elif kind in ("created_time", "last_edited_time"):
        value = inner if isinstance(inner, str) else None
    elif kind == "unique_id":
        unique = as_dict(inner)
        number = unique.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            prefix = unique.get("prefix")
            value = f"{prefix}-{number}" if isinstance(prefix, str) and prefix else str(number)
    elif kind == "relation":
        value = f"{len(as_list(inner))} verknüpft" if as_list(inner) else None
    elif kind == "formula":
        formula = as_dict(inner)
        result = formula.get(str(formula.get("type")))
        if isinstance(result, str):
            value = result
        elif isinstance(result, int | float) and not isinstance(result, bool):
            value = int(result) if float(result).is_integer() else float(result)
    return value


def flatten_page(page: dict[str, Any]) -> tuple[str | None, dict[str, Value]]:
    """(Titel, alle lesbaren Properties) einer Seite."""
    title: str | None = None
    values: dict[str, Value] = {}
    for name, prop in as_dict(page.get("properties")).items():
        value = property_value(prop)
        if as_dict(prop).get("type") == "title":
            title = value if isinstance(value, str) else None
        elif value is not None:
            values[name] = value
    return title, values


def _as_text(value: Value | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def normalize_page(page: dict[str, Any]) -> dict[str, Any]:
    """Kompakter Eintrag: {id, titel, status, datum, felder}."""
    title, values = flatten_page(page)
    lowered = {k.lower(): k for k in values}

    status_key = lowered.get("status")
    status = _as_text(values.get(status_key)) if status_key else None

    date_key = next((lowered[n] for n in DATE_NAMES if n in lowered), None)
    if date_key is None:
        date_key = next(
            (
                k
                for k, v in values.items()
                if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v)
            ),
            None,
        )
    date = _as_text(values.get(date_key)) if date_key else None

    used = {k for k in (status_key, date_key) if k}
    ordered = [lowered[n] for n in IMPORTANT_FIELDS if n in lowered and lowered[n] not in used]
    ordered += [k for k in values if k not in used and k not in ordered]
    fields = {k: values[k] for k in ordered[:MAX_FIELDS]}

    entry: dict[str, Any] = {"titel": title or "Unbenannter Eintrag"}
    page_id = page.get("id")
    if isinstance(page_id, str):
        entry["id"] = page_id
    if status:
        entry["status"] = status
    if date:
        entry["datum"] = date
    if fields:
        entry["felder"] = fields
    return entry


def is_done(entry: dict[str, Any]) -> bool:
    """Erledigt-Status, auch mit Symbolen oder Zusätzen wie "✅ Erledigt" oder "Done!"."""
    words = re.findall(r"\w+", str(entry.get("status", "")).casefold())
    if any(word in NEGATIONS for word in words):  # "Nicht erledigt", "not done"
        return False
    return any(word in DONE_STATUS for word in words)


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Priorität (Hoch zuerst), dann Datum (frühestes zuerst, ohne Datum zuletzt)."""

    def key(entry: dict[str, Any]) -> tuple[int, int, str]:
        felder: dict[str, Any] = entry.get("felder", {})
        priority = next(
            (str(v).strip().casefold() for k, v in felder.items() if k.casefold() == "priorität"),
            "",
        )
        date = str(entry.get("datum", ""))
        return (PRIORITY_RANK.get(priority, len(PRIORITY_RANK)), 0 if date else 1, date)

    return sorted(entries, key=key)


def matches(entry: dict[str, Any], values: dict[str, Value], needle: str) -> bool:
    """Volltextsuche über Titel, Status und die WERTE aller lesbaren Properties.

    Property-Namen zählen nicht: sonst träfe die Suche nach "Fach" jede Schulmission.
    """
    texts = [entry.get("titel"), entry.get("status"), *(_as_text(v) for v in values.values())]
    haystack = "\n".join(str(t) for t in texts if t)
    return needle.strip().casefold() in haystack.casefold()


def fit_budget(
    entries: list[dict[str, Any]], budget: int = OUTPUT_BUDGET
) -> tuple[list[dict[str, Any]], bool]:
    """Wirft die letzten Einträge ab, bis die Antwort sicher ins Budget passt."""
    kept = list(entries)
    while len(kept) > 1 and len(json.dumps(kept, ensure_ascii=False)) > budget:
        kept.pop()
    return kept, len(kept) < len(entries)


_BLOCK_PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ",
    "numbered_list_item": "1. ", "quote": "> ", "paragraph": "", "callout": "", "toggle": "",
}  # fmt: skip


def blocks_to_text(blocks: list[dict[str, Any]], limit: int = 3000) -> str:
    lines: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if not isinstance(kind, str):
            continue
        inner = as_dict(block.get(kind))
        text = _plain(inner.get("rich_text"))
        if kind == "to_do":
            lines.append(f"[{'x' if inner.get('checked') else ' '}] {text}")
        elif kind == "code" and text:
            lines.append(f"```\n{text}\n```")
        elif kind == "child_page" and isinstance(inner.get("title"), str):
            lines.append(f"[Unterseite: {inner['title']}]")
        elif kind in _BLOCK_PREFIX and text:
            lines.append(_BLOCK_PREFIX[kind] + text)
    joined = "\n".join(lines)
    return joined if len(joined) <= limit else joined[:limit] + "…"


_ID_RE = re.compile(r"([0-9a-fA-F]{32})(?![0-9a-fA-F])")


def extract_page_id(reference: str) -> str | None:
    """Seiten-ID (mit oder ohne Bindestriche) oder Notion-URL -> ID mit Bindestrichen.

    In Links aus der Datenbankansicht (`.../<Datenbank-ID>?v=<Ansicht>&p=<Seiten-ID>`) zählt die
    Seiten-ID hinter `p=`, nicht die erste ID im Link.
    """
    compact = reference.strip().replace("-", "")
    peek = re.search(r"[?&]p=([0-9a-fA-F]{32})(?![0-9a-fA-F])", compact)
    match = peek or _ID_RE.search(compact)
    if match is None:
        return None
    h = match.group(1).lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

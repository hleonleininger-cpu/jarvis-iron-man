"""MCP-Server `memory`: Langzeitgedächtnis und Tagesjournal, lokal (ADR-019).

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Kein Netz. Inhalte
bleiben in den Dateien; ins Audit-Log kommt wie immer nur der Werkzeugname. Gemerkt wird nur,
was der Nutzer ausdrücklich sagen lässt. remember und die Journal-Werkzeuge schreiben sofort
(lokal, umkehrbar); forget löscht endgültig, die Bestätigung erzwingt der Core (Policy).
"""

import argparse
from contextlib import suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from mcp_servers.memory.embed import (
    DEFAULT_MODEL,
    DEFAULT_URL,
    Embedder,
    EmbedError,
    make_embedder,
)
from mcp_servers.memory.store import KINDS, Memory, MemoryStore

TZ = ZoneInfo("Europe/Berlin")
DEFAULT_FILE = Path.home() / ".jarvis" / "gedaechtnis.sqlite3"
DEFAULT_JOURNAL = Path.home() / ".jarvis" / "journal"
MAX_TEXT_LENGTH = 1000
MAX_LISTED = 50
MAX_FOUND = 5
MAX_JOURNAL_SHOWN = 4000
FIRST_YEAR, LAST_YEAR = 1970, 2200

mcp = MCPServer("memory", log_level="WARNING")
_store: MemoryStore | None = None
_journal_dir: Path = DEFAULT_JOURNAL
# Embeddings (M1.8): None = nicht eingerichtet, dann gilt die Stichwortsuche. In Tests ersetzt.
_embedder: Embedder | None = None
_embed_model: str = DEFAULT_MODEL
EMBED_HINT = "Embedding-Modell nicht erreichbar; es gilt die Stichwortsuche (ollama pull {model})."


def store() -> MemoryStore:
    """Wird in Tests ersetzt; im Betrieb von main() gesetzt."""
    global _store
    if _store is None:
        _store = MemoryStore(DEFAULT_FILE)
    return _store


def _fail(message: str, reason: str = "invalid_input") -> dict[str, Any]:
    return {"ok": False, "fehler": message, "grund": reason}


def _text(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Es fehlt der Text.")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise ValueError(f"Der Text darf höchstens {MAX_TEXT_LENGTH} Zeichen lang sein.")
    return cleaned


def _kind(art: str | None) -> str:
    kind = (art or "").strip().lower() or "fakt"
    if kind not in KINDS:
        raise ValueError(f"Unbekannte Art '{art}'. Erlaubt: {', '.join(KINDS)}.")
    return kind


def _entry(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "text": memory.text,
        "art": memory.kind,
        "gemerkt_am": memory.created.astimezone(TZ).date().isoformat(),
    }


def remember_data(text: str, art: str | None = None) -> dict[str, Any]:
    try:
        cleaned, kind = _text(text), _kind(art)
    except ValueError as exc:
        return _fail(str(exc))
    memory, new = store().add(cleaned, kind)
    if new and _embedder is not None:
        # Ohne Embedding bleibt der Eintrag per Stichwort auffindbar (die Pflege holt nach).
        with suppress(EmbedError):
            store().set_embedding(memory.id, _embedder([cleaned])[0], _embed_model)
    note = "Gemerkt." if new else "Das wusste ich schon."
    return {"ok": True, "gespeichert": note, "id": memory.id, "text": memory.text}


def recall_data(frage: str) -> dict[str, Any]:
    if not frage.strip():
        return _fail("Es wurde keine Frage genannt.")
    found: list[Memory] = []
    hint: str | None = None
    method = "Stichwortsuche"
    if _embedder is not None:
        try:
            vector = _embedder([frage.strip()])[0]
            semantic, compared = store().semantic_search(vector, _embed_model, MAX_FOUND)
            if compared:
                method = "semantisch"
                found = [memory for memory, _similarity in semantic]
        except EmbedError:
            hint = EMBED_HINT.format(model=_embed_model)
    # Stichwort-Treffer kommen immer dazu (auch Einträge ohne Embedding), die semantischen zuerst.
    for memory, _score in store().search(frage, MAX_FOUND):
        if memory.id not in {m.id for m in found}:
            found.append(memory)
    found = found[:MAX_FOUND]
    result: dict[str, Any] = {
        "ok": True,
        "suche": method,
        "anzahl": len(found),
        "treffer": [_entry(memory) for memory in found],
    }
    if hint:
        result["hinweis"] = hint
    elif not found:
        result["hinweis"] = "Dazu ist nichts gespeichert."
    return result


def list_data(art: str | None = None) -> dict[str, Any]:
    try:
        kind = _kind(art) if art and art.strip() else None
    except ValueError as exc:
        return _fail(str(exc))
    memories = store().entries(kind)
    result: dict[str, Any] = {
        "ok": True,
        "anzahl": len(memories),
        "eintraege": [_entry(m) for m in memories[-MAX_LISTED:]],
    }
    if len(memories) > MAX_LISTED:
        result["hinweis"] = f"Nur die neuesten {MAX_LISTED} von {len(memories)} Einträgen gezeigt."
    return result


def _find(memory_id: int) -> Memory | dict[str, Any]:
    memory = store().get(memory_id)
    if memory is None:
        return _fail(f"Es gibt keinen Eintrag mit der Nummer {memory_id}.", "not_found")
    return memory


def preview_forget_data(id: int) -> dict[str, Any]:
    found = _find(id)
    if isinstance(found, dict):
        return found
    return {
        "ok": True,
        "vorschau": f"Aus dem Gedächtnis löschen (endgültig): {found.text}",
        "frage": "Vergessen?",
    }


def forget_data(id: int) -> dict[str, Any]:
    found = _find(id)
    if isinstance(found, dict):
        return found
    before = store().count()
    store().delete(id)
    return {"ok": True, "vergessen": found.text, "eintraege_vorher": before,
            "eintraege_nachher": store().count()}  # fmt: skip


# --------------------------------------------------------------------------- Tagesjournal (M1.9)
def _day(tag: str | None, now: datetime | None = None) -> date:
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    key = (tag or "").strip().lower()
    if key in ("", "heute"):
        return today
    if key == "gestern":
        return today - timedelta(days=1)
    try:
        day = date.fromisoformat(key)
    except ValueError:
        raise ValueError(
            f"Ungültiger Tag '{tag}'. Erlaubt: heute, gestern oder JJJJ-MM-TT."
        ) from None
    if not FIRST_YEAR <= day.year <= LAST_YEAR:
        raise ValueError(f"Das Datum {day:%Y-%m-%d} ist nicht plausibel.")
    return day


def _journal_file(day: date) -> Path:
    return _journal_dir / f"{day:%Y-%m-%d}.md"  # Name nur aus einem Datum: kein Pfadausbruch


def journal_add_data(text: str, now: datetime | None = None) -> dict[str, Any]:
    try:
        cleaned = _text(text)
    except ValueError as exc:
        return _fail(str(exc))
    moment = (now or datetime.now(TZ)).astimezone(TZ)
    path = _journal_file(moment.date())
    _journal_dir.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if is_new:
            fh.write(f"# Journal {moment:%Y-%m-%d}\n\n")
        fh.write(f"- {moment:%H:%M} {cleaned}\n")
    return {"ok": True, "eingetragen": f"{moment:%d.%m.%Y %H:%M}", "datei": str(path)}


def journal_read_data(tag: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    try:
        day = _day(tag, now)
    except ValueError as exc:
        return _fail(str(exc))
    path = _journal_file(day)
    if not path.is_file():
        return {
            "ok": True,
            "tag": day.isoformat(),
            "inhalt": "",
            "hinweis": "Für diesen Tag gibt es keinen Eintrag.",
        }
    content = path.read_text(encoding="utf-8")
    result: dict[str, Any] = {"ok": True, "tag": day.isoformat(), "inhalt": content}
    if len(content) > MAX_JOURNAL_SHOWN:
        result["inhalt"] = content[-MAX_JOURNAL_SHOWN:]
        result["hinweis"] = f"Nur die letzten {MAX_JOURNAL_SHOWN} Zeichen gezeigt."
    return result


@mcp.tool()
def remember(text: str, art: str | None = None) -> dict[str, Any]:
    """Merkt sich etwas dauerhaft, aber NUR wenn der Nutzer es ausdrücklich sagt ("Merk dir ...").
    art optional: fakt, vorliebe, person, projekt oder sonstiges."""
    return remember_data(text, art)


@mcp.tool()
def recall(frage: str) -> dict[str, Any]:
    """Sucht im Gedächtnis des Nutzers nach Bedeutung (bis zu 5 Treffer). Für 'Was weißt du über
    ...?'. Ergebnisse nennst du 'gespeichert'."""
    return recall_data(frage)


@mcp.tool()
def list_memories(art: str | None = None) -> dict[str, Any]:
    """Zeigt, was im Gedächtnis steht (neueste zuletzt). art optional filtert."""
    return list_data(art)


@mcp.tool()
def forget(id: int) -> dict[str, Any]:
    """Löscht einen Eintrag endgültig aus dem Gedächtnis (id aus recall oder list_memories).
    Direkt aufrufen: die Bestätigung holt das System selbst beim Nutzer ein."""
    return forget_data(id)


@mcp.tool()
def preview_forget(id: int) -> dict[str, Any]:
    """Interne Vorschau für forget (löscht nichts)."""
    return preview_forget_data(id)


@mcp.tool()
def journal_add(text: str) -> dict[str, Any]:
    """Hängt einen Eintrag mit Uhrzeit an das Tagesjournal von heute an (eine Textdatei je Tag)."""
    return journal_add_data(text)


@mcp.tool()
def journal_read(tag: str | None = None) -> dict[str, Any]:
    """Liest das Tagesjournal. tag: heute (Standard), gestern oder JJJJ-MM-TT."""
    return journal_read_data(tag)


def main() -> None:
    global _store, _journal_dir, _embedder, _embed_model
    parser = argparse.ArgumentParser(prog="mcp_servers.memory")
    parser.add_argument("--datei", type=Path, default=DEFAULT_FILE, help="SQLite-Datei")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL, help="Journal-Ordner")
    parser.add_argument("--ollama-url", default=DEFAULT_URL, help="Ollama (nur im eigenen Netz)")
    parser.add_argument("--embed-modell", default=DEFAULT_MODEL, help="Embedding-Modell")
    parser.add_argument("--ohne-embeddings", action="store_true", help="nur Stichwortsuche")
    args = parser.parse_args()
    _store = MemoryStore(args.datei.expanduser())
    _journal_dir = args.journal.expanduser()
    if not args.ohne_embeddings:
        _embed_model = args.embed_modell
        _embedder = make_embedder(args.ollama_url, args.embed_modell)
    mcp.run(transport="stdio")

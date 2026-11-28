"""`jarvis voice setup`: lädt die Modelle ausdrücklich und prüft sie (einziger Netzzugriff)."""

from collections.abc import Callable
from pathlib import Path

from jarvis_voice.models import ModelEntry, ModelError, fetch, load_registry, verify

# Ohne Auswahl: alles, was die Standardeinstellung braucht (das Wake-Word-Paket gibt es nur unter
# Windows; die Modelldateien lädt `setup` auf jedem System).
DEFAULT_KEYS = ("silero_vad", "smart_turn", "wake_word", "whisper_base", "piper_en", "piper_de")


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def plan(models_dir: Path, keys: tuple[str, ...] = DEFAULT_KEYS) -> list[tuple[ModelEntry, bool]]:
    """(Gruppe, fehlt noch?) je gewünschtem Schlüssel."""
    registry = load_registry()
    unknown = [k for k in keys if k not in registry]
    if unknown:
        raise ModelError(
            f"Unbekanntes Modell: {', '.join(unknown)}. Bekannt: {', '.join(registry)}."
        )
    return [(registry[k], bool(verify(models_dir, registry[k], deep=True))) for k in keys]


def run_setup(
    models_dir: Path,
    keys: tuple[str, ...],
    echo: Callable[[str], None],
    confirm: Callable[[str], bool],
) -> bool:
    """Lädt fehlende Modelle nach Rückfrage. True, wenn danach alles vollständig und geprüft ist."""
    todo = [entry for entry, missing in plan(models_dir, keys) if missing]
    if not todo:
        echo("Alle gewünschten Modelle sind vorhanden. Prüfe die Prüfsummen ...")
    else:
        total = sum(entry.total_bytes for entry in todo)
        echo(f"Ablageort: {models_dir}")
        for entry in todo:
            echo(f"  {entry.key}: {entry.zweck} ({megabytes(entry.total_bytes)}, {entry.lizenz})")
        echo(f"Gesamt ca. {megabytes(total)} aus den Originalquellen (kostenlos).")
        if not confirm("Jetzt herunterladen?"):
            echo("Abgebrochen, es wurde nichts geladen.")
            return False

    def progress(name: str, done: int, total: int) -> None:
        if done == total:
            echo(f"  {name} geladen ({megabytes(total)})")

    ok = True
    for entry, _missing in plan(models_dir, keys):
        try:
            fetch(models_dir, entry, progress)
        except ModelError as exc:
            echo(f"FEHLER: {exc}")
            ok = False
            continue
        problems = verify(models_dir, entry, deep=True)
        if problems:
            echo(f"FEHLER {entry.key}: {'; '.join(problems)}")
            ok = False
    echo("Alle Modelle geprüft." if ok else "Nicht alles ist in Ordnung (siehe oben).")
    return ok

"""CLI: jarvis serve | chat | doctor | audit verify."""

import asyncio
import sys

import typer
import uvicorn
from pydantic import ValidationError

from jarvis_core.agent import ChatError, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings, configure_logging, load_settings
from jarvis_core.hardware import (
    HardwareInfo,
    check_ollama,
    detect_hardware,
    is_installed,
    recommend,
)

app = typer.Typer(help="JARVIS Core", no_args_is_help=True, add_completion=False)
audit_app = typer.Typer(help="Audit-Log", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


def _settings(log_level: str | None = None) -> Settings:
    try:
        settings = load_settings()
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "Konfiguration"
            msg = err["msg"].removeprefix("Value error, ")
            typer.echo(f"Ungültige Konfiguration ({where}): {msg}", err=True)
        raise typer.Exit(2) from None
    configure_logging(log_level or settings.log_level)
    return settings


@app.command()
def serve() -> None:
    """Startet den Core-Server (Standard: nur 127.0.0.1)."""
    settings = _settings()
    from jarvis_core.api import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


async def _chat(text: str, settings: Settings) -> None:
    service = build_service(settings, AuditLog(settings.audit_db_path))
    async for delta in service.stream(text, channel="cli"):
        sys.stdout.write(delta)
        sys.stdout.flush()
    sys.stdout.write("\n")


@app.command()
def chat(text: str = typer.Argument(..., help="Nachricht an JARVIS")) -> None:
    """Beantwortet eine Nachricht gestreamt über das lokale Ollama-Modell."""
    settings = _settings("WARNING")  # Terminal bleibt frei von JSON-Logs
    try:
        asyncio.run(_chat(text, settings))
    except ChatError as exc:
        typer.echo(f"\nFehler: {exc}", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo("\nAbgebrochen.", err=True)
        raise typer.Exit(130) from None


def _fmt(value: float | str | None, unit: str = "") -> str:
    if value is None:
        return "unbekannt"
    if isinstance(value, float):
        return f"{value:.1f}{unit}"
    return str(value)


def _rows(info: HardwareInfo, settings: Settings) -> list[tuple[str, str]]:
    status = check_ollama(settings.ollama_base_url)
    rec = recommend(info)
    vram = "geteilt mit RAM" if info.is_mac else _fmt(info.vram_gb, " GB")
    rows = [
        ("CPU", _fmt(info.cpu)),
        ("RAM", _fmt(info.ram_gb, " GB")),
        ("GPU", _fmt(info.gpu)),
        ("VRAM", vram),
        (
            "Ollama",
            f"läuft ({settings.ollama_base_url})"
            if status.running
            else f"nicht erreichbar ({settings.ollama_base_url})",
        ),
        ("Installierte Modelle", ", ".join(status.models) if status.models else "keine"),
        ("Empfohlenes Profil", f"{rec.profile} ({rec.reason})"),
        ("Empfohlenes Modell", rec.model),
        ("Installieren mit", rec.pull_command),
    ]
    if settings.model:
        installed = "installiert" if is_installed(settings.model, status.models) else "FEHLT"
        rows.append(("Konfiguriertes Modell", f"{settings.model} ({installed})"))
    return rows


@app.command()
def doctor() -> None:
    """Zeigt Hardware, Ollama-Status und das empfohlene Modellprofil (nur Anzeige)."""
    settings = _settings()
    rows = _rows(detect_hardware(), settings)
    width = max(len(label) for label, _ in rows)
    typer.echo("JARVIS Doctor")
    typer.echo("=" * (width + 40))
    for label, value in rows:
        typer.echo(f"{label:<{width}}  {value}")


@audit_app.command("verify")
def audit_verify() -> None:
    """Prüft die Hash-Kette des Audit-Logs."""
    settings = _settings()
    result = AuditLog(settings.audit_db_path).verify_chain()
    if result.ok:
        typer.echo(f"Audit-Kette gültig ({result.checked} Einträge).")
        return
    typer.echo(
        f"Audit-Kette UNGÜLTIG: erste fehlerhafte Zeile: {result.first_invalid_id} "
        f"({result.reason}).",
        err=True,
    )
    raise typer.Exit(1)

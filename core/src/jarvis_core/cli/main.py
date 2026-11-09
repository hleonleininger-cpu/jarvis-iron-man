"""CLI: jarvis serve | chat | doctor | audit verify."""

import asyncio
import sys
from pathlib import Path

import typer
import uvicorn
from pydantic import ValidationError

from jarvis_core.agent import (
    ChatError,
    ConfirmRequested,
    Notice,
    TextDelta,
    ToolUsed,
    build_service,
    resolve_model_name,
)
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings, configure_logging, load_settings
from jarvis_core.evals import (
    EvalAbort,
    EvalTask,
    EvalTaskError,
    RunResult,
    default_evals_dir,
    format_table,
    load_tasks,
    run_evals,
    save_report,
)
from jarvis_core.hardware import (
    HardwareInfo,
    check_ollama,
    detect_hardware,
    is_installed,
    recommend,
)
from jarvis_core.secrets import SECRETS, SecretsError, is_set, store_secret

app = typer.Typer(help="JARVIS Core", no_args_is_help=True, add_completion=False)
audit_app = typer.Typer(help="Audit-Log", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
tools_app = typer.Typer(help="MCP-Werkzeuge", no_args_is_help=True)
evals_app = typer.Typer(
    help="Eval-Harness: Zuverlässigkeit der Werkzeugwahl messen", no_args_is_help=True
)
app.add_typer(evals_app, name="evals")
secrets_app = typer.Typer(help="Zugangsdaten im OS-Schlüsselbund", no_args_is_help=True)
app.add_typer(secrets_app, name="secrets")
app.add_typer(tools_app, name="tools")


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


def _ask_yes_no(question: str) -> bool:
    """Interaktive Rückfrage; alles außer j/ja ist Nein (auch EOF)."""
    try:
        answer = input(f"{question} [j/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("j", "ja", "y", "yes")


async def _chat(text: str, settings: Settings) -> None:
    service = build_service(settings, AuditLog(settings.audit_db_path))
    at_line_start = True
    async for event in service.stream(text, channel="cli"):
        match event:
            case TextDelta(chunk):
                sys.stdout.write(chunk)
                at_line_start = chunk.endswith("\n") if chunk else at_line_start
            case ToolUsed(name):
                sys.stdout.write(f"{'' if at_line_start else chr(10)}[Werkzeug: {name}]\n")
                at_line_start = True
            case Notice(message):
                typer.echo(f"Hinweis: {message}", err=True)
            case ConfirmRequested(request_id, _tool, preview, question):
                sys.stdout.write(f"{'' if at_line_start else chr(10)}{preview}\n")
                at_line_start = True
                service.broker.resolve(request_id, _ask_yes_no(question))
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


def _one_line(text: str, limit: int = 90) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def _list_tools(settings: Settings) -> None:
    from jarvis_core.mcp_hub import McpHub

    audit = AuditLog(settings.audit_db_path)
    async with McpHub(settings.mcp_servers, audit, model_name="-") as hub:
        for failure in hub.failures:
            typer.echo(f"Hinweis: {failure}", err=True)
        tools = hub.list_tools()
        if not tools:
            typer.echo("Keine Werkzeuge verfügbar (siehe mcp_servers in jarvis.toml).")
            return
        width = max(len(t.name) for t in tools)
        for tool in tools:
            line = f"{tool.name:<{width}}  {tool.risk:<24}  [{tool.server}]"
            typer.echo(f"{line}  {_one_line(tool.description)}")


@tools_app.command("list")
def tools_list() -> None:
    """Listet verfügbare Werkzeuge mit Risikoklasse."""
    settings = _settings("WARNING")
    asyncio.run(_list_tools(settings))


@secrets_app.command("set")
def secrets_set(name: str = typer.Argument(..., help="z. B. apple_caldav")) -> None:
    """Fragt lokal nach den Zugangsdaten und legt sie im OS-Schlüsselbund ab."""
    spec = SECRETS.get(name)
    if spec is None:
        typer.echo(f"Unbekanntes Secret '{name}'. Bekannt: {', '.join(SECRETS)}.", err=True)
        raise typer.Exit(2)
    values = {f.key: typer.prompt(f.label, hide_input=f.hidden).strip() for f in spec.fields}
    try:
        store_secret(name, **values)
    except SecretsError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo("gespeichert")


@secrets_app.command("status")
def secrets_status() -> None:
    """Zeigt nur, ob Secrets gesetzt sind (nie den Wert)."""
    for name in SECRETS:
        typer.echo(f"{name}: {'gesetzt' if is_set(name) else 'nicht gesetzt'}")


def _eval_tasks(directory: Path | None) -> tuple[list[EvalTask], Path]:
    base = directory or default_evals_dir()
    try:
        return load_tasks(base / "tasks.toml"), base
    except EvalTaskError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(2) from None


@evals_app.command("list")
def evals_list(
    directory: Path | None = typer.Option(None, "--dir", help="Ordner mit tasks.toml"),
) -> None:
    """Listet die Eval-Aufgaben mit erwartetem Werkzeug."""
    tasks, _ = _eval_tasks(directory)
    rows = [("Aufgabe", "Erwartet", "Bestätigung", "Schreibt", "Eingabe")]
    for task in tasks:
        confirm = ("Ja" if task.confirm == "yes" else "Nein") if task.expect_confirm else "-"
        rows.append(
            (task.id, ", ".join(task.expect_tools) or "keins", confirm, str(task.expect_writes),
             task.prompt)
        )  # fmt: skip
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for row in rows:
        typer.echo("  ".join(c.ljust(widths[i]) for i, c in enumerate(row[:4])) + f"  {row[4]}")
    typer.echo(f"\n{len(tasks)} Aufgaben")


@evals_app.command("run")
def evals_run(
    model: str | None = typer.Option(None, "--model", help="Ollama-Modell (Standard: Empfehlung)"),
    runs: int = typer.Option(3, "--runs", min=1, help="Durchläufe je Aufgabe"),
    only: list[str] = typer.Option([], "--task", help="Nur diese Aufgaben-ID (mehrfach möglich)"),
    limit_s: float = typer.Option(240.0, "--timeout", help="Sekunden je Durchlauf"),
    directory: Path | None = typer.Option(None, "--dir", help="Ordner mit tasks.toml"),
) -> None:
    """Lässt die Aufgaben gegen Agent + Ollama laufen (Attrappen, kein Netz außer Ollama)."""
    settings = _settings("WARNING")
    model_name = model or resolve_model_name(settings)
    tasks, base = _eval_tasks(directory)
    if only:
        unknown = [i for i in only if i not in {t.id for t in tasks}]
        if unknown:
            typer.echo(f"Unbekannte Aufgaben-ID(s): {', '.join(unknown)}", err=True)
            raise typer.Exit(2)
        tasks = [t for t in tasks if t.id in only]

    def ensure_ollama() -> None:
        status = check_ollama(settings.ollama_base_url)
        if not status.running:
            raise EvalAbort(
                f"Ollama ist unter {settings.ollama_base_url} nicht erreichbar. "
                "Starte Ollama (z. B. 'ollama serve') und wiederhole den Lauf."
            )
        if not is_installed(model_name, status.models):
            raise EvalAbort(
                f"Das Modell '{model_name}' ist nicht installiert. "
                f"Installiere es mit: ollama pull {model_name}"
            )

    total = len(tasks) * runs
    counter = [0]

    def progress(task: EvalTask, index: int, result: RunResult) -> None:
        counter[0] += 1
        mark = "ok" if result.ok else "FEHL"
        detail = "" if result.ok else f"  ({'; '.join(result.reasons)[:110]})"
        typer.echo(
            f"[{counter[0]}/{total}] {task.id} {index}/{runs} {mark} "
            f"{result.observation.duration_s:.0f}s{detail}",
            err=True,
        )

    try:
        ensure_ollama()
        typer.echo(
            f"Eval: {len(tasks)} Aufgaben x {runs} Durchläufe, Modell {model_name}", err=True
        )
        report = asyncio.run(
            run_evals(
                tasks, settings, model_name, runs,
                limit_s=limit_s, before_run=ensure_ollama, on_run=progress,
            )
        )  # fmt: skip
    except EvalAbort as exc:
        typer.echo(f"\nAbgebrochen: {exc} Es wurde kein Ergebnis gespeichert.", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo("\nAbgebrochen. Es wurde kein Ergebnis gespeichert.", err=True)
        raise typer.Exit(130) from None
    typer.echo("")
    typer.echo(format_table(report))
    path = save_report(report, base / "results")
    typer.echo(f"\nErgebnisdatei: {path}")

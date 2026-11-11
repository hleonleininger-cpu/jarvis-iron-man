"""CLI: jarvis serve | chat | doctor | tools | secrets | evals | audit."""

import asyncio
import errno
import os
import re
import socket
import sys
import threading
import tomllib
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from pydantic import ValidationError

from jarvis_core.agent import (
    ChatError,
    ChatService,
    ConfirmationBroker,
    ConfirmRequested,
    Notice,
    TextDelta,
    ToolUsed,
    build_service,
    resolve_model_name,
)
from jarvis_core.audit import AuditError, AuditLog
from jarvis_core.config import (
    ConfigFileMissing,
    Settings,
    config_path,
    configure_logging,
    load_settings,
)
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
from jarvis_core.secrets import (
    SECRETS,
    SecretsError,
    delete_secret,
    generate_secret,
    read_secret_field,
    secret_state,
    store_secret,
)

app = typer.Typer(help="JARVIS Core", no_args_is_help=True, add_completion=False)


@app.callback()
def _tolerant_output() -> None:
    """Zeichen, die die Ausgabe-Kodierung nicht kennt (z. B. Emoji bei cp1252 unter Windows,
    wenn die Ausgabe umgeleitet ist), werden ersetzt statt den Befehl abzubrechen."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


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


# Interaktive Befehle melden Fehler selbst in Klartext; Warnungen als JSON-Zeile wären doppelt.
INTERACTIVE_LOG_LEVEL = "ERROR"


def _audit(path: Path) -> AuditLog:
    """Audit-Log öffnen; ist es nicht nutzbar, klare Meldung statt Traceback (und kein Betrieb)."""
    try:
        return AuditLog(path)
    except AuditError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(1) from None


def _settings(log_level: str | None = None) -> Settings:
    try:
        settings = load_settings()
    except ConfigFileMissing as exc:
        typer.echo(f"Ungültige Konfiguration: {exc}", err=True)
        raise typer.Exit(2) from None
    except (
        tomllib.TOMLDecodeError
    ) as exc:  # Tippfehler in jarvis.toml: Zeile nennen, kein Traceback
        typer.echo(f"Ungültige Konfiguration ({config_path().resolve()}): {exc}", err=True)
        raise typer.Exit(2) from None
    except OSError as exc:
        typer.echo(f"Konfiguration nicht lesbar ({config_path().resolve()}): {exc}", err=True)
        raise typer.Exit(2) from None
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "Konfiguration"
            msg = err["msg"].removeprefix("Value error, ")
            typer.echo(f"Ungültige Konfiguration ({where}): {msg}", err=True)
        raise typer.Exit(2) from None
    configure_logging(log_level or settings.log_level)
    return settings


# Vorlage im Repository (core/jarvis.example.toml); `jarvis init` kopiert sie.
EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "jarvis.example.toml"


@app.command()
def init() -> None:
    """Legt jarvis.toml aus der Vorlage an (überschreibt nie) und nennt die nächsten Schritte."""
    target = config_path()
    if target.exists():
        typer.echo(f"{target.resolve()} gibt es schon; es wurde nichts geändert.", err=True)
        raise typer.Exit(1)
    if not EXAMPLE_CONFIG.is_file():
        typer.echo(f"Vorlage nicht gefunden: {EXAMPLE_CONFIG}", err=True)
        raise typer.Exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    rec = recommend(detect_hardware())
    typer.echo(f"Angelegt: {target.resolve()}")
    typer.echo("Nächste Schritte:")
    for step in (
        f"{rec.pull_command}   (empfohlen für diesen Rechner: {rec.reason})",
        "jarvis secrets set apple_caldav   (Kalender, optional)",
        "jarvis secrets set notion   (und Datenbank-IDs in jarvis.toml eintragen, optional)",
        "jarvis doctor   (prüft alles)",
        "jarvis chat   oder   jarvis serve",
    ):
        typer.echo(f"  {step}")


@app.command()
def serve(
    open_browser: Annotated[
        bool, typer.Option("--open", help="Web-Oberfläche danach im Browser öffnen")
    ] = False,
) -> None:
    """Startet den Core-Server (Standard: nur 127.0.0.1) mit Web-Oberfläche."""
    settings = _settings()
    from jarvis_core.api import create_app
    from jarvis_core.api.app import token_required

    _ensure_port_free(settings.host, settings.port)
    token: str | None = None
    if token_required(settings):
        try:
            token = read_secret_field("api_token", "token")
        except SecretsError as exc:
            typer.echo(f"Fehler: {exc}", err=True)
            raise typer.Exit(1) from None
        if token is None:
            typer.echo(
                "Dieser Server ist nicht nur lokal erreichbar und braucht ein Zugangstoken. "
                "Erzeugen mit: jarvis secrets generate api_token",
                err=True,
            )
            raise typer.Exit(1)
    try:
        app_ = create_app(settings, api_token=token)
    except AuditError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(1) from None
    # uvicorn bleibt still (log_config=None): ohne diese Zeile sähe man nach dem Start nichts.
    typer.echo(
        f"JARVIS Core läuft auf http://{_url_host(settings.host)}:{settings.port} "
        f"(Modell {resolve_model_name(settings)}"
        f"{', Zugang nur mit Token' if token else ''}). Web-Oberfläche: dieselbe Adresse im "
        "Browser. Beenden mit Strg+C.",
        err=True,
    )
    if open_browser:
        url = f"http://{_url_host(settings.host)}:{settings.port}/"
        # Erst öffnen, wenn uvicorn lauscht; ein Timer reicht dafür (Start dauert < 1 s).
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app_, host=settings.host, port=settings.port, log_config=None)


def _ensure_port_free(host: str, port: int) -> None:
    """Belegter Port: klare Meldung statt eines stillen Abbruchs (uvicorn loggt hier nicht)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            # Wie uvicorn: Verbindungen eines eben beendeten Servers (TIME_WAIT) blockieren den
            # Port nicht. Unter Windows hieße SO_REUSEADDR "fremden Port mitbenutzen", also nicht.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host.strip("[]"), port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                hint = "Läuft JARVIS schon? Sonst einen anderen port in jarvis.toml wählen."
            else:  # z. B. Tailnet-Adresse, solange Tailscale nicht verbunden ist
                hint = (
                    "Gibt es diese Adresse auf dem Rechner? Bei Tailnet: ist Tailscale verbunden?"
                )
            typer.echo(
                f"Kann nicht auf {_url_host(host)}:{port} starten ({exc.strerror or exc}). {hint}",
                err=True,
            )
            raise typer.Exit(1) from None


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


# Steuerzeichen außer Zeilenumbruch und Tab. Modellausgabe und Vorschau stammen (indirekt) vom
# Modell bzw. aus Kalender/Notion; ESC-Sequenzen könnten das Terminal umfärben, Text verstecken
# oder per OSC die Zwischenablage setzen.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _printable(text: str) -> str:
    return _CONTROL.sub("", text)


def _ask_yes_no(question: str) -> bool:
    """Interaktive Rückfrage; alles außer j/ja ist Nein (auch EOF)."""
    try:
        answer = input(f"{question} [j/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("j", "ja", "y", "yes")


async def _chat(service: ChatService, text: str, session_id: str | None = None) -> None:
    at_line_start = True
    async for event in service.stream(text, channel="cli", session_id=session_id):
        match event:
            case TextDelta(raw):
                chunk = _printable(raw)
                sys.stdout.write(chunk)
                at_line_start = chunk.endswith("\n") if chunk else at_line_start
            case ToolUsed(name):
                sys.stdout.write(f"{'' if at_line_start else chr(10)}[Werkzeug: {name}]\n")
                at_line_start = True
            case Notice(message):
                typer.echo(f"Hinweis: {_printable(message)}", err=True)
            case ConfirmRequested(request_id, _tool, preview, question):
                sys.stdout.write(f"{'' if at_line_start else chr(10)}{_printable(preview)}\n")
                at_line_start = True
                service.broker.resolve(request_id, _ask_yes_no(_printable(question)))
        sys.stdout.flush()
    sys.stdout.write("\n")


DIALOG_EXIT = ("/ende", "/exit", "/quit")
DIALOG_RESET = ("/neu", "/new")


def _dialog(service: ChatService) -> None:
    """Gespräch über mehrere Nachrichten; der Verlauf lebt nur in diesem Prozess (ADR-009)."""
    session_id = service.sessions.new_id()
    typer.echo(
        f"JARVIS-Dialog (Modell {service.model_name}). "
        "/neu beginnt ein neues Gespräch, /ende beendet."
    )
    while True:
        try:
            text = input("du> ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            return
        if not text:
            continue
        if text.lower() in DIALOG_EXIT:
            return
        if text.lower() in DIALOG_RESET:
            service.sessions.clear(session_id)
            typer.echo("Neues Gespräch.")
            continue
        if text.startswith("/") and " " not in text:
            # Ein vertippter Befehl soll nicht als Frage ans Modell gehen.
            typer.echo(f"Unbekannter Befehl '{text}'. Es gibt /neu und /ende.")
            continue
        try:
            asyncio.run(_chat(service, text, session_id))
        except ChatError as exc:
            typer.echo(f"\nFehler: {exc}", err=True)
        except KeyboardInterrupt:
            typer.echo("\nAbgebrochen.", err=True)


@app.command()
def chat(
    text: str | None = typer.Argument(
        None, help="Nachricht an JARVIS; ohne Angabe startet ein Dialog"
    ),
) -> None:
    """Beantwortet eine Nachricht (gestreamt) oder führt ohne Argument einen Dialog."""
    settings = _settings(INTERACTIVE_LOG_LEVEL)  # Terminal bleibt frei von JSON-Logs
    service = build_service(settings, _audit(settings.audit_db_path))
    service.broker = ConfirmationBroker(timeout=None)  # der Nutzer sitzt vor der Rückfrage
    if text is None:
        _dialog(service)
        return
    if not text.strip():
        typer.echo("Die Nachricht ist leer.", err=True)
        raise typer.Exit(2)
    try:
        asyncio.run(_chat(service, text))
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
        if not status.running:
            installed = "unbekannt, Ollama nicht erreichbar"
        else:
            installed = "installiert" if is_installed(settings.model, status.models) else "FEHLT"
        rows.append(("Konfiguriertes Modell", f"{settings.model} ({installed})"))
    path = config_path()
    found = "gefunden" if path.is_file() else "nicht gefunden, es gelten Standardwerte"
    rows.append(("Konfiguration", f"{path.resolve()} ({found})"))
    rows.append(("MCP-Server", ", ".join(settings.mcp_servers) or "keine (ohne Werkzeuge)"))
    secrets = ", ".join(f"{name}: {secret_state(name)}" for name in SECRETS)
    rows.append(("Zugangsdaten", secrets))
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
    path = settings.audit_db_path
    if not path.is_file():
        # Nichts anlegen: ein falscher Ordner soll nicht als "gültig, 0 Einträge" durchgehen.
        typer.echo(f"Kein Audit-Log unter {path.resolve()}.", err=True)
        raise typer.Exit(1)
    result = _audit(path).verify_chain()
    if result.ok:
        typer.echo(f"Audit-Kette gültig ({result.checked} Einträge, {path.resolve()}).")
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

    audit = _audit(settings.audit_db_path)
    async with McpHub(settings.mcp_servers, audit, model_name="-") as hub:
        for failure in hub.failures:
            typer.echo(f"Hinweis: {failure}", err=True)
        tools = hub.list_tools()
        if not tools:
            typer.echo("Keine Werkzeuge verfügbar (siehe mcp_servers in jarvis.toml).")
        else:
            width = max(len(t.name) for t in tools)
            server_width = max(len(t.server) for t in tools) + 2
            for tool in tools:
                server = f"[{tool.server}]"
                line = f"{tool.name:<{width}}  {tool.risk:<16}  {server:<{server_width}}"
                typer.echo(f"{line}  {_one_line(tool.description)}")
        if hub.failures:  # Skripte sollen merken, dass etwas fehlt
            raise typer.Exit(1)


@tools_app.command("list")
def tools_list() -> None:
    """Listet verfügbare Werkzeuge mit Risikoklasse."""
    settings = _settings(INTERACTIVE_LOG_LEVEL)
    asyncio.run(_list_tools(settings))


@secrets_app.command("set")
def secrets_set(name: str = typer.Argument(..., help="z. B. apple_caldav")) -> None:
    """Fragt lokal nach den Zugangsdaten und legt sie im OS-Schlüsselbund ab."""
    spec = SECRETS.get(name)
    if spec is None:
        typer.echo(f"Unbekanntes Secret '{name}'. Bekannt: {', '.join(SECRETS)}.", err=True)
        raise typer.Exit(2)
    # Verdeckte Eingaben zweimal abfragen: ein Tippfehler im App-Passwort fiele sonst erst
    # beim nächsten Kalenderzugriff als "Zugangsdaten abgelehnt" auf.
    values = {
        f.key: typer.prompt(f.label, hide_input=f.hidden, confirmation_prompt=f.hidden).strip()
        for f in spec.fields
    }
    try:
        store_secret(name, **values)
    except SecretsError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo("gespeichert")


@secrets_app.command("generate")
def secrets_generate(name: str = typer.Argument(..., help="api_token")) -> None:
    """Erzeugt ein zufälliges Token, legt es im Schlüsselbund ab und zeigt es EINMAL an."""
    try:
        token = generate_secret(name)
    except SecretsError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(2 if name not in SECRETS else 1) from None
    typer.echo(token)
    typer.echo(
        "Gespeichert. Im Web-Client bzw. auf dem iPhone einmal eingeben; es wird nicht wieder "
        "angezeigt (neu erzeugen ersetzt es).",
        err=True,
    )


@secrets_app.command("delete")
def secrets_delete(name: str = typer.Argument(..., help="z. B. apple_caldav")) -> None:
    """Entfernt die Zugangsdaten aus dem OS-Schlüsselbund."""
    try:
        removed = delete_secret(name)
    except SecretsError as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(2 if name not in SECRETS else 1) from None
    typer.echo("gelöscht" if removed else "war nicht gesetzt")


@secrets_app.command("status")
def secrets_status() -> None:
    """Zeigt nur, ob Secrets gesetzt sind (nie den Wert)."""
    for name in SECRETS:
        typer.echo(f"{name}: {secret_state(name)}")


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
    settings = _settings(INTERACTIVE_LOG_LEVEL)
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

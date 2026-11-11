"""Konfiguration: jarvis.toml + Umgebungsvariablen (Präfix JARVIS_)."""

import ipaddress
import os
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from jarvis_core.policy import RiskClass

# Wildcard-Adressen würden den Server in jedem Netz erreichbar machen (ADR-003).
# Pfad der Konfigurationsdatei; ohne Angabe `jarvis.toml` im aktuellen Ordner.
CONFIG_ENV = "JARVIS_CONFIG"
DEFAULT_CONFIG_FILE = "jarvis.toml"


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_FILE)


FORBIDDEN_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})  # noqa: S104


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# Namen, die nur im eigenen Netz bzw. Tailnet auflösen.
PRIVATE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def is_private_host(host: str) -> bool:
    """localhost, private/Loopback-/Tailnet-Adressen oder Rechnernamen ohne öffentliche Domain."""
    name = host.strip("[]").lower().rstrip(".")
    if not name:
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name == "localhost" or "." not in name or name.endswith(PRIVATE_SUFFIXES)
    return ip.is_loopback or ip.is_private or ip in TAILNET


class McpServerConfig(BaseModel):
    """Ein in jarvis.toml deklarierter MCP-Server (stdio). Nur deklarierte werden gestartet.

    `tools` ist die einzige Stelle für Werkzeug-Metadaten: nur dort genannte Werkzeuge werden dem
    Modell angeboten, jeweils mit ihrer Risikoklasse.
    """

    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = []
    # Umgebungsvariablen für den Server-Prozess (keine Secrets; die liegen im Schlüsselbund).
    env: dict[str, str] = {}
    tools: dict[str, RiskClass]
    # Optional: CONFIRM-Werkzeug -> Vorschau-Werkzeug desselben Servers (schreibt nichts). Das
    # Vorschau-Werkzeug steht nicht in `tools` und wird dem Modell nie angeboten.
    previews: dict[str, str] = {}

    @model_validator(mode="after")
    def _previews_belong_to_tools(self) -> "McpServerConfig":
        for tool, preview in self.previews.items():
            if tool not in self.tools:
                raise ValueError(f"previews: '{tool}' ist nicht in tools deklariert")
            if preview in self.tools:
                raise ValueError(
                    f"previews: '{preview}' darf nicht selbst als Werkzeug angeboten werden"
                )
        return self


class ConfigFileMissing(Exception):
    """Die über JARVIS_CONFIG genannte Datei fehlt (deutsche Meldung)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        toml_file="jarvis.toml",
        extra="forbid",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    model: str | None = None
    data_dir: Path = Path("data")
    log_level: str = "INFO"
    # Gesprächsverlauf je Sitzung (nur im Arbeitsspeicher, ADR-009). 0 Runden = kein Verlauf.
    history_max_turns: int = Field(default=10, ge=0, le=100)
    history_max_chars: int = Field(default=16_000, ge=1_000, le=200_000)
    mcp_servers: dict[str, McpServerConfig] = {}
    # Wohnort für Fragen ohne Ortsangabe (z. B. "Wie ist das Wetter?"), etwa "Dortmund".
    home_location: str | None = Field(default=None, max_length=80)
    # Zugangstoken für /v1/* (ADR-011). None = automatisch: Pflicht, sobald host nicht lokal ist.
    require_api_token: bool | None = None

    @field_validator("host")
    @classmethod
    def _reject_wildcard_host(cls, value: str) -> str:
        if value.strip() in FORBIDDEN_HOSTS:
            raise ValueError(
                "Bindung an alle Netzwerkschnittstellen (0.0.0.0) ist nicht erlaubt; "
                "erlaubt sind 127.0.0.1 oder eine konkrete Tailnet-Adresse."
            )
        return value.strip()

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"log_level muss eines von {', '.join(LOG_LEVELS)} sein.")
        return level

    @field_validator("ollama_base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("ollama_base_url muss mit http:// oder https:// beginnen.")
        if not is_private_host(urlsplit(value).hostname or ""):
            # Gesprächsinhalte verlassen nie das eigene Netz: keine Cloud-Modelle (ADR-003).
            raise ValueError(
                "ollama_base_url muss auf diesen Rechner, das Heimnetz oder das Tailnet zeigen "
                "(keine Cloud-Modelle)."
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def _tool_names_are_unique(self) -> Self:
        """Werkzeugnamen sind global: das Modell ruft sie ohne Servernamen auf."""
        owner: dict[str, str] = {}
        for server, cfg in self.mcp_servers.items():
            for tool in (*cfg.tools, *cfg.previews.values()):
                if tool in owner:
                    raise ValueError(
                        f"Werkzeug '{tool}' ist doppelt deklariert "
                        f"(mcp_servers.{owner[tool]} und mcp_servers.{server})."
                    )
                owner[tool] = server
        return self

    @property
    def audit_db_path(self) -> Path:
        return self.data_dir / "audit.sqlite3"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Reihenfolge = Priorität: init > Umgebung > jarvis.toml. Kein .env-Laden.
        toml = TomlConfigSettingsSource(settings_cls, toml_file=config_path())
        return (init_settings, env_settings, toml)

    @classmethod
    def load(cls) -> Self:
        explicit = os.environ.get(CONFIG_ENV)
        if explicit and not Path(explicit).is_file():
            # Eine ausdrücklich genannte Datei darf nicht still durch Standardwerte ersetzt werden.
            raise ConfigFileMissing(
                f"{CONFIG_ENV} zeigt auf '{explicit}', die Datei gibt es nicht."
            )
        settings = cls()
        path = config_path()
        if not settings.data_dir.is_absolute() and path.is_file():
            # Ein Audit-Log je Installation, nicht eins je Ordner, aus dem `jarvis` gestartet wird.
            settings.data_dir = path.resolve().parent / settings.data_dir
        return settings


def load_settings() -> Settings:
    return Settings.load()

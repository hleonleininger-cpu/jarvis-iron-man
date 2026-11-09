"""Konfiguration: jarvis.toml + Umgebungsvariablen (Präfix JARVIS_)."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from jarvis_core.policy import RiskClass

# Wildcard-Adressen würden den Server in jedem Netz erreichbar machen (ADR-003).
FORBIDDEN_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})  # noqa: S104


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
    mcp_servers: dict[str, McpServerConfig] = {}

    @field_validator("host")
    @classmethod
    def _reject_wildcard_host(cls, value: str) -> str:
        if value.strip() in FORBIDDEN_HOSTS:
            raise ValueError(
                "Bindung an alle Netzwerkschnittstellen (0.0.0.0) ist nicht erlaubt; "
                "erlaubt sind 127.0.0.1 oder eine konkrete Tailnet-Adresse."
            )
        return value.strip()

    @field_validator("ollama_base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("ollama_base_url muss mit http:// oder https:// beginnen.")
        return value.rstrip("/")

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
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))

    @classmethod
    def load(cls) -> Self:
        return cls()


def load_settings() -> Settings:
    return Settings.load()

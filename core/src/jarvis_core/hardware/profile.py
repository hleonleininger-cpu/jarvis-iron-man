"""Profilwahl S/M/L. Nur Empfehlung, keine Installation."""

from dataclasses import dataclass
from enum import StrEnum

from jarvis_core.hardware.detect import HardwareInfo

# Toleranz, weil Karten "8 GB" oft als 8151 MiB melden.
_GB_TOLERANCE = 0.5


class Profile(StrEnum):
    S = "S"
    M = "M"
    L = "L"


MODELS: dict[Profile, str] = {
    Profile.S: "qwen3.5:4b",
    Profile.M: "qwen3.5:9b",
    Profile.L: "qwen3.6:27b",
}


@dataclass(frozen=True)
class Recommendation:
    profile: Profile
    model: str
    reason: str

    @property
    def pull_command(self) -> str:
        return f"ollama pull {self.model}"


def _tier(gb: float) -> Profile:
    if gb >= 16 - _GB_TOLERANCE:
        return Profile.L
    if gb >= 8 - _GB_TOLERANCE:
        return Profile.M
    return Profile.S


def recommend(info: HardwareInfo) -> Recommendation:
    """Unbekannte Werte führen immer zum kleineren Profil."""
    if info.is_mac:
        if info.ram_gb is None:
            return _make(Profile.S, "Arbeitsspeicher unbekannt")
        if info.ram_gb >= 32 - _GB_TOLERANCE:
            return _make(Profile.L, f"Mac mit {info.ram_gb:.0f} GB")
        if info.ram_gb >= 16 - _GB_TOLERANCE:
            return _make(Profile.M, f"Mac mit {info.ram_gb:.0f} GB")
        return _make(Profile.S, f"Mac mit {info.ram_gb:.0f} GB")
    if info.vram_gb is None:
        return _make(Profile.S, "keine GPU oder VRAM unbekannt")
    profile = _tier(info.vram_gb)
    return _make(profile, f"{info.vram_gb:.0f} GB VRAM")


def _make(profile: Profile, reason: str) -> Recommendation:
    return Recommendation(profile=profile, model=MODELS[profile], reason=reason)

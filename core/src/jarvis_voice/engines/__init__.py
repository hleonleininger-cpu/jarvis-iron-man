"""Echte Engines hinter den Protokollen aus `jarvis_voice.protocols`. Die schweren Pakete
(numpy, onnxruntime, faster-whisper, piper, openwakeword, sounddevice) werden erst bei Gebrauch
geladen und stehen nur in der Gruppe `voice`; fehlen sie, kommt eine verständliche Meldung."""

import importlib
from types import ModuleType


class MissingPackage(Exception):
    """Ein Sprachpaket ist nicht installiert (deutsche Meldung)."""


def need(module: str, why: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError:
        raise MissingPackage(
            f"Das Paket '{module}' fehlt ({why}). Installieren mit: uv sync --extra voice"
        ) from None

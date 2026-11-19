# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Sprachausgabe: Piper (`piper-tts`, GPL-3, nur lokal) mit je einer Stimme für Deutsch und
Englisch (M2.3). Der Satz wird in Stücken geliefert, damit die Ausgabe sofort beginnt."""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from jarvis_voice.engines import need

WHISPER_VOLUME = 0.35  # Flüstermodus: leiser und etwas langsamer
WHISPER_LENGTH_SCALE = 1.15


class PiperTts:
    def __init__(self, voices: dict[str, Path]) -> None:
        """`voices`: Sprache ("de", "en") -> Pfad der .onnx-Datei (die .json liegt daneben)."""
        self._module = need("piper", "Sprachausgabe")
        self._paths = voices
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    def _voice(self, language: str) -> object:
        path = self._paths.get(language) or next(iter(self._paths.values()))
        if not path.is_file():
            from jarvis_voice.models import ModelError

            raise ModelError(
                f"Die Stimme {path.name} fehlt in {path.parent}. Laden mit: jarvis voice setup"
            )
        with self._lock:
            if str(path) not in self._voices:
                self._voices[str(path)] = self._module.PiperVoice.load(
                    str(path), config_path=str(path) + ".json"
                )
            return self._voices[str(path)]

    def warm_up(self, language: str = "de") -> None:
        self._voice(language)

    def _chunks(self, text: str, language: str, whisper: bool) -> Iterator[tuple[bytes, int]]:
        voice = self._voice(language)
        config = None
        if whisper:
            config = self._module.SynthesisConfig(
                volume=WHISPER_VOLUME, length_scale=WHISPER_LENGTH_SCALE
            )
        for chunk in voice.synthesize(text, syn_config=config):  # type: ignore[attr-defined]
            yield chunk.audio_int16_bytes, chunk.sample_rate

    async def synthesize(
        self, text: str, language: str, whisper: bool
    ) -> AsyncIterator[tuple[bytes, int]]:
        iterator = self._chunks(text, language, whisper)
        sentinel = (b"", 0)
        while (item := await asyncio.to_thread(next, iterator, sentinel)) is not sentinel:
            yield item

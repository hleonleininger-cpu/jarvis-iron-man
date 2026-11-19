# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Spracherkennung: faster-whisper (MIT), nur mit lokalen Modellordnern (M2.2). Deutsch und
Englisch werden je Äußerung automatisch erkannt (M2.7)."""

import asyncio
import os
import threading
from pathlib import Path

from jarvis_voice.engines import need
from jarvis_voice.protocols import Transcript

MIN_LANGUAGE_PROBABILITY = 0.5


class FasterWhisperStt:
    def __init__(
        self, model_dir: Path, device: str = "cpu", compute_type: str = "int8",
        language: str | None = None,
    ) -> None:  # fmt: skip
        os.environ["HF_HUB_OFFLINE"] = "1"  # (auch beim Import von jarvis_voice.engines gesetzt)
        self._np = need("numpy", "Signalverarbeitung")
        self._module = need("faster_whisper", "Spracherkennung")
        self._dir = model_dir
        self._device = device
        self._compute = compute_type
        self.language = language  # None = automatisch (de/en)
        self._model: object | None = None
        self._lock = threading.Lock()

    def _load(self) -> object:
        with self._lock:
            if self._model is None:  # erst beim ersten Gebrauch (das Laden dauert)
                self._model = self._module.WhisperModel(
                    str(self._dir), device=self._device, compute_type=self._compute,
                    local_files_only=True,
                )  # fmt: skip
            return self._model

    def warm_up(self) -> None:
        self._load()

    def _run(self, pcm: bytes) -> Transcript:
        np = self._np
        model = self._load()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = model.transcribe(  # type: ignore[attr-defined]
            audio, language=self.language, beam_size=1, vad_filter=False,
            condition_on_previous_text=False,
        )  # fmt: skip
        if self.language is None and info.language not in ("de", "en"):
            # Whisper kennt 99 Sprachen und rät bei kurzen Äußerungen gern falsch (z. B.
            # Niederländisch): nur Deutsch und Englisch zählen, also mit der wahrscheinlicheren
            # von beiden noch einmal.
            probabilities = dict(getattr(info, "all_language_probs", None) or [])
            best = max(("de", "en"), key=lambda code: probabilities.get(code, 0.0))
            segments, info = model.transcribe(  # type: ignore[attr-defined]
                audio, language=best, beam_size=1, vad_filter=False,
                condition_on_previous_text=False,
            )  # fmt: skip
        text = " ".join(segment.text.strip() for segment in segments).strip()
        language = info.language if info.language_probability >= MIN_LANGUAGE_PROBABILITY else None
        return Transcript(text, language if language in ("de", "en") else None)

    async def transcribe(self, pcm: bytes) -> Transcript:
        return await asyncio.to_thread(self._run, pcm)

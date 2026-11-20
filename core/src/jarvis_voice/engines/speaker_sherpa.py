# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Sprecherprüfung: NeMo TitaNet small über sherpa-onnx (M2.9). Vergleicht den Stimm-Vektor einer
Äußerung (Kosinus) mit dem angelernten Profil. Keine Sicherheitsgarantie: eine Aufnahme oder ein
guter Nachahmer kann täuschen; die Schwelle ist einstellbar."""

import threading
from collections.abc import Sequence
from pathlib import Path

from jarvis_voice.engines import need
from jarvis_voice.protocols import SAMPLE_RATE
from jarvis_voice.speaker import cosine

MIN_SECONDS = 0.5  # kürzere Äußerungen tragen zu wenig Stimme für ein Urteil


class SherpaEmbedder:
    def __init__(self, model: Path) -> None:
        self._np = need("numpy", "Signalverarbeitung")
        sherpa = need("sherpa_onnx", "Sprecherkennung")
        config = sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(model), num_threads=1, debug=False, provider="cpu"
        )
        self._extractor = sherpa.SpeakerEmbeddingExtractor(config)
        self._lock = threading.Lock()
        self.model_name = model.name

    def embed(self, pcm: bytes) -> list[float]:
        np = self._np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
            stream.input_finished()
            return [float(x) for x in self._extractor.compute(stream)]


class SherpaSpeakerGate:
    def __init__(
        self, embedder: SherpaEmbedder, profile: Sequence[float], threshold: float = 0.4
    ) -> None:
        self._embedder = embedder
        self._profile = list(profile)
        self.threshold = threshold
        self.last_score = 0.0

    def verify(self, pcm: bytes) -> bool:
        if len(pcm) / 2 / SAMPLE_RATE < MIN_SECONDS:
            return False  # zu kurz für ein Urteil: im Zweifel nicht Master
        self.last_score = cosine(self._embedder.embed(pcm), self._profile)
        return self.last_score >= self.threshold

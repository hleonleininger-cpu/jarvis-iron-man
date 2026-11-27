# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Wake Word "hey jarvis": openWakeWord (Code Apache-2.0, Modelle CC BY-NC-SA, nur privat) mit
ONNX (M2.1). Unter Windows genügt onnxruntime; die Modelle kommen aus `jarvis voice setup`."""

from pathlib import Path

from jarvis_voice.engines import need

CHUNK_SAMPLES = 1280  # openWakeWord arbeitet mit Vielfachen von 80 ms


class OpenWakeWordDetector:
    def __init__(
        self, wake: Path, melspectrogram: Path, embedding: Path, threshold: float = 0.5
    ) -> None:
        self._np = need("numpy", "Signalverarbeitung")
        module = need("openwakeword.model", "Wake Word (nur Windows)")
        # Schnittstelle von openwakeword 0.4.0 (uv.lock). Neuere Versionen nennen Argumente anders.
        self._model = module.Model(
            wakeword_model_paths=[str(wake)],
            melspec_onnx_model_path=str(melspectrogram), embedding_onnx_model_path=str(embedding),
        )  # fmt: skip
        self.threshold = threshold
        self.last_score = 0.0
        self._buffer = bytearray()

    def process(self, frame: bytes) -> bool:
        np = self._np
        self._buffer += frame
        hit = False
        while len(self._buffer) >= CHUNK_SAMPLES * 2:
            chunk = np.frombuffer(bytes(self._buffer[: CHUNK_SAMPLES * 2]), dtype=np.int16)
            del self._buffer[: CHUNK_SAMPLES * 2]
            scores = self._model.predict(chunk)
            self.last_score = float(max(scores.values())) if scores else 0.0
            hit = hit or self.last_score >= self.threshold
        if hit:
            self.reset()
        return hit

    def reset(self) -> None:
        self._buffer.clear()
        self._model.reset()

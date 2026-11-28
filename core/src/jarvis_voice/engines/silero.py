# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Sprache oder Stille: Silero-VAD (MIT) direkt mit onnxruntime, ohne PyTorch (M2.4)."""

from pathlib import Path

from jarvis_voice.engines import need
from jarvis_voice.protocols import FRAME_SAMPLES, SAMPLE_RATE

CONTEXT = 64  # Silero erwartet die letzten 64 Samples des Vorgängers vorangestellt


class SileroVad:
    def __init__(self, model: Path, threshold: float = 0.5) -> None:
        self._np = need("numpy", "Signalverarbeitung")
        ort = need("onnxruntime", "Modelle ausführen")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def probability(self, frame: bytes) -> float:
        np = self._np
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) != FRAME_SAMPLES:  # zu kurz oder lang: auf Frame-Größe bringen
            samples = np.pad(samples, (0, max(0, FRAME_SAMPLES - len(samples))))[:FRAME_SAMPLES]
        x = np.concatenate([self._context, samples])[None].astype(np.float32)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)}
        )
        self._context = samples[-CONTEXT:]
        return float(out[0][0])

    def is_speech(self, frame: bytes) -> bool:
        return self.probability(frame) >= self.threshold

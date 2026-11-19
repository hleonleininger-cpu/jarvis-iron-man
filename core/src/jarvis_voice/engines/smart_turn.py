# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Satzende-Erkennung: Pipecat Smart Turn v3 (BSD-2) direkt mit onnxruntime (M2.4, Entscheidung
von Master am 19.11.: ohne Pipecat und ohne PyTorch).

Ablauf: Nach dem Ende der Sprache (VAD) wird die Äußerung geprüft; sagt das Modell "vollständig",
endet der Satz sofort, sonst wartet der Erkenner weiter, bis die Stille `max_silence` erreicht.
Die Log-Mel-Merkmale (80 x 800 für 8 Sekunden) berechnet der Merkmalsberechner von faster-whisper;
das Eingabeformat ist dasselbe wie bei Whisper."""

from pathlib import Path

from jarvis_voice.engines import need
from jarvis_voice.protocols import FRAME_SAMPLES, FRAME_SECONDS, SAMPLE_RATE

WINDOW_SECONDS = 8
WINDOW_SAMPLES = WINDOW_SECONDS * SAMPLE_RATE
MEL_FRAMES = 800


class SmartTurnDetector:
    def __init__(
        self,
        model: Path,
        *,
        first_check_silence: float = 0.25,
        recheck_every: float = 0.25,
        max_silence: float = 2.0,
        threshold: float = 0.5,
    ) -> None:
        self._np = need("numpy", "Signalverarbeitung")
        ort = need("onnxruntime", "Modelle ausführen")
        extractor = need("faster_whisper.feature_extractor", "Merkmale für Smart Turn")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._features = extractor.FeatureExtractor(
            feature_size=80, sampling_rate=SAMPLE_RATE, hop_length=160, chunk_length=WINDOW_SECONDS,
            n_fft=400,
        )  # fmt: skip
        self.configure(first_check_silence, recheck_every, max_silence, threshold)
        self.reset()

    def configure(
        self, first_check_silence: float, recheck_every: float, max_silence: float, threshold: float
    ) -> None:
        """Zeiten in Sekunden: ab wann geprüft wird, wie oft erneut, wann es ohne Modell endet."""
        self._first = max(1, round(first_check_silence / FRAME_SECONDS))
        self._every = max(1, round(recheck_every / FRAME_SECONDS))
        self._max = max(self._first, round(max_silence / FRAME_SECONDS))
        self.threshold = threshold

    def reset(self) -> None:
        self._audio = bytearray()
        self._heard = False
        self._quiet = 0
        self.last_probability: float | None = None

    def feed(self, frame: bytes, speech: bool) -> bool:
        self._audio += frame
        keep = WINDOW_SAMPLES * 2 + FRAME_SAMPLES * 2
        if len(self._audio) > keep:
            del self._audio[: len(self._audio) - keep]
        if speech:
            self._heard = True
            self._quiet = 0
            return False
        if not self._heard:
            self._audio.clear()  # Stille vor der Äußerung gehört nicht dazu
            return False
        self._quiet += 1
        if self._quiet >= self._max:
            return True
        if self._quiet >= self._first and (self._quiet - self._first) % self._every == 0:
            self.last_probability = self.complete_probability()
            return self.last_probability >= self.threshold
        return False

    def complete_probability(self) -> float:
        """Wie sicher ist das Modell, dass die Äußerung zu Ende ist (0 bis 1)."""
        np = self._np
        audio = np.frombuffer(bytes(self._audio), dtype=np.int16).astype(np.float32) / 32768.0
        audio = audio[-WINDOW_SAMPLES:]
        if len(audio) < WINDOW_SAMPLES:
            audio = np.pad(audio, (WINDOW_SAMPLES - len(audio), 0))
        audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        mel = self._features(audio, padding=False, chunk_length=WINDOW_SECONDS)
        if mel.shape[1] != MEL_FRAMES:  # Sicherheitsnetz: das Modell verlangt genau 800 Frames
            mel = np.pad(mel, ((0, 0), (0, max(0, MEL_FRAMES - mel.shape[1]))))[:, :MEL_FRAMES]
        out = self._session.run(None, {"input_features": mel[None].astype(np.float32)})
        return float(out[0][0][0])

# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Mikrofon und Lautsprecher über `sounddevice` (PortAudio). Das Mikrofon läuft nur, solange der
Strom geöffnet ist: `close()` schließt ihn wirklich (Schlafmodus, harte Stummschaltung)."""

import asyncio
import contextlib
import threading
from typing import Any

from jarvis_voice.audio import tone
from jarvis_voice.engines import need
from jarvis_voice.protocols import FRAME_SAMPLES, SAMPLE_RATE

BLOCK_FRAMES = 1024  # Schreibblöcke der Ausgabe: so schnell wirkt ein Stopp
QUEUE_FRAMES = 300  # ca. 10 s Puffer; bei Überlauf fallen die ältesten Frames weg
CUES = {"wake": (880.0, 0.09), "listen_done": (660.0, 0.06), "listen": (520.0, 0.05)}


def find_device(sd: Any, name: str | None, kind: str) -> int | None:
    """Index des Geräts, dessen Name `name` enthält (kind: "input" oder "output")."""
    if not name:
        return None
    key = f"max_{kind}_channels"
    devices: list[dict[str, Any]] = list(sd.query_devices())
    matches = [
        index
        for index, device in enumerate(devices)
        if device[key] > 0 and name.casefold() in str(device["name"]).casefold()
    ]
    if matches:
        # Unter Windows gibt es dasselbe Gerät mehrfach (MME, WASAPI, ...). MME rechnet Abtastraten
        # selbst um; WASAPI und WDM-KS verlangen die Rate des Geräts. Deshalb MME zuerst.
        return next((i for i in matches if _is_mme(sd, devices[i])), matches[0])
    names = ", ".join(str(d["name"]) for d in devices if d[key] > 0)
    raise ValueError(f"Kein {kind}-Gerät mit '{name}'. Vorhanden: {names or 'keines'}.")


def _is_mme(sd: Any, device: dict[str, Any]) -> bool:
    try:
        return "MME" in str(sd.query_hostapis(device["hostapi"])["name"])
    except Exception:  # Attrappen oder alte PortAudio-Versionen ohne Host-API-Angabe
        return False


def list_devices(sd: Any) -> list[tuple[int, str, int, int]]:
    """(Index, Name, Eingangskanäle, Ausgangskanäle) aller Geräte."""
    return [
        (i, str(d["name"]), int(d["max_input_channels"]), int(d["max_output_channels"]))
        for i, d in enumerate(sd.query_devices())
    ]


class _Converting:
    """Ausgabestrom mit Umrechnung der Abtastrate (lineare Interpolation reicht für Sprache)."""

    def __init__(self, stream: Any, rate_in: int, rate_out: int, np: Any) -> None:
        self._stream, self._in, self._out, self._np = stream, rate_in, rate_out, np

    @property
    def active(self) -> bool:
        return bool(self._stream.active)

    def start(self) -> None:
        self._stream.start()

    def abort(self) -> None:
        self._stream.abort()

    def write(self, samples: Any) -> None:
        np = self._np
        data = samples[:, 0].astype(np.float32)
        count = max(1, int(len(data) * self._out / self._in))
        out = np.interp(np.linspace(0, len(data) - 1, count), np.arange(len(data)), data)
        self._stream.write(out.astype(np.int16).reshape(-1, 1))


class SoundDeviceSource:
    def __init__(self, device: str | None = None) -> None:
        self._sd = need("sounddevice", "Mikrofon und Lautsprecher")
        self._device = device
        self._stream: Any = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    async def open(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        index = find_device(self._sd, self._device, "input")

        def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            del frames, time, status
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._put, bytes(indata))

        self._stream = self._sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
            device=index, callback=callback,
        )  # fmt: skip
        self._stream.start()

    def _put(self, data: bytes) -> None:
        if self._queue.qsize() >= QUEUE_FRAMES:
            self._queue.get_nowait()  # lieber alte Frames verlieren als immer weiter zurückfallen
        self._queue.put_nowait(data)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._queue.put_nowait(None)

    async def frames(self):  # noqa: ANN201
        while (frame := await self._queue.get()) is not None:
            yield frame


class SoundDeviceSink:
    def __init__(self, device: str | None = None) -> None:
        self._sd = need("sounddevice", "Mikrofon und Lautsprecher")
        self._np = need("numpy", "Signalverarbeitung")
        self._device = find_device(self._sd, device, "output")
        self._streams: dict[int, Any] = {}
        self._generation = 0
        self._write_lock = threading.Lock()  # PortAudio-Streams vertragen keine zwei Schreiber
        self._beeps: set[asyncio.Task[None]] = set()

    def _stream(self, rate: int) -> Any:
        stream = self._streams.get(rate)
        if stream is None:
            try:
                stream = self._sd.OutputStream(
                    samplerate=rate, channels=1, dtype="int16", device=self._device
                )
                stream.start()
            except Exception:  # das Gerät kann diese Rate nicht: mit seiner eigenen Rate spielen
                native = int(self._sd.query_devices(self._device, "output")["default_samplerate"])
                stream = _Converting(
                    self._sd.OutputStream(
                        samplerate=native, channels=1, dtype="int16", device=self._device
                    ),
                    rate,
                    native,
                    self._np,
                )
                stream.start()
            self._streams[rate] = stream
        if not stream.active:
            stream.start()
        return stream

    def _write(self, stream: Any, pcm: bytes, generation: int) -> None:
        samples = self._np.frombuffer(pcm, dtype=self._np.int16).reshape(-1, 1)
        with self._write_lock:
            for start in range(0, len(samples), BLOCK_FRAMES):
                if generation != self._generation:
                    return  # inzwischen gestoppt
                try:
                    stream.write(samples[start : start + BLOCK_FRAMES])
                except Exception:
                    if generation != self._generation:
                        return  # Stopp kam mitten im Schreiben: der Fehler gehört dazu
                    raise

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        generation = self._generation
        stream = self._stream(sample_rate)
        await asyncio.to_thread(self._write, stream, pcm, generation)

    def stop(self) -> None:
        """Sofort verstummen: laufende Schreibvorgänge enden, gepufferter Ton wird verworfen."""
        self._generation += 1
        for stream in self._streams.values():
            with contextlib.suppress(Exception):  # schon zu: das Ziel (Ruhe) ist erreicht
                if stream.active:
                    stream.abort()

    def beep(self, name: str) -> None:
        """Kurzer Hinweiston, ohne zu warten (Aufwachen, Aufnahme beendet, Folgefrage offen)."""
        freq, seconds = CUES.get(name, (700.0, 0.05))
        pcm = tone(freq, seconds, sample_rate=SAMPLE_RATE, level=0.25)
        task = asyncio.ensure_future(self._quiet_play(pcm))
        self._beeps.add(task)
        task.add_done_callback(self._beeps.discard)

    async def _quiet_play(self, pcm: bytes) -> None:
        with contextlib.suppress(Exception):  # ein misslungener Hinweiston darf nichts stören
            await self.play(pcm, SAMPLE_RATE)

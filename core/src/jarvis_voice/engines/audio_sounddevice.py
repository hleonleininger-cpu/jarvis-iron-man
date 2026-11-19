# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Mikrofon und Lautsprecher über `sounddevice` (PortAudio). Das Mikrofon läuft nur, solange der
Strom geöffnet ist: `close()` schließt ihn wirklich (Schlafmodus, harte Stummschaltung)."""

import asyncio
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
    for index, device in enumerate(devices):
        if device[key] > 0 and name.casefold() in str(device["name"]).casefold():
            return index
    names = ", ".join(str(d["name"]) for d in devices if d[key] > 0)
    raise ValueError(f"Kein {kind}-Gerät mit '{name}'. Vorhanden: {names or 'keines'}.")


def list_devices(sd: Any) -> list[tuple[int, str, int, int]]:
    """(Index, Name, Eingangskanäle, Ausgangskanäle) aller Geräte."""
    return [
        (i, str(d["name"]), int(d["max_input_channels"]), int(d["max_output_channels"]))
        for i, d in enumerate(sd.query_devices())
    ]


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

    def _stream(self, rate: int) -> Any:
        stream = self._streams.get(rate)
        if stream is None:
            stream = self._sd.OutputStream(
                samplerate=rate, channels=1, dtype="int16", device=self._device
            )
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
                stream.write(samples[start : start + BLOCK_FRAMES])

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        generation = self._generation
        stream = self._stream(sample_rate)
        await asyncio.to_thread(self._write, stream, pcm, generation)

    def stop(self) -> None:
        """Sofort verstummen: laufende Schreibvorgänge enden, gepufferter Ton wird verworfen."""
        self._generation += 1
        for stream in self._streams.values():
            if stream.active:
                stream.abort()

    def beep(self, name: str) -> None:
        """Kurzer Hinweiston, ohne zu warten (Aufwachen, Aufnahme beendet, Folgefrage offen)."""
        freq, seconds = CUES.get(name, (700.0, 0.05))
        pcm = tone(freq, seconds, sample_rate=SAMPLE_RATE, level=0.25)
        asyncio.ensure_future(self.play(pcm, SAMPLE_RATE))  # noqa: RUF006

"""Globale Tastenkürzel (M2.13) über pynput. Sie laufen in einem eigenen Thread und reichen die
Aktion an die Event-Loop weiter. Standard: Strg+Alt+J hören, Strg+Alt+M Mikrofon stumm/an,
Strg+Alt+X unterbrechen, Strg+Alt+D Diktat an/aus."""

import asyncio
import re
from collections.abc import Callable
from typing import Any

from jarvis_voice.engines import need

# "<ctrl>+<alt>+j": mindestens eine Umschalttaste, dann genau eine normale oder Funktionstaste
HOTKEY_PATTERN = re.compile(
    r"(<(ctrl|alt|shift|cmd|alt_gr)>\+)+([a-z0-9]|<f([1-9]|1[0-9]|2[0-4])>|<(space|home|end|insert|delete|page_up|page_down)>)"
)


def valid_hotkey(text: str) -> bool:
    """Nur mit Strg, Alt oder Cmd: <shift>+j löste bei jedem großen J aus (auch beim Diktat)."""
    if not HOTKEY_PATTERN.fullmatch(text):
        return False
    return any(mod in text for mod in ("<ctrl>", "<alt>", "<cmd>", "<alt_gr>"))


class HotkeyListener:
    def __init__(
        self, bindings: dict[str, Callable[[], None]], loop: asyncio.AbstractEventLoop
    ) -> None:
        """`bindings`: Kürzel -> Aktion. Die Aktion läuft in der Event-Loop, nicht im Thread."""
        for combo in bindings:
            if not valid_hotkey(combo):
                raise ValueError(f"Ungültiges Tastenkürzel '{combo}' (Beispiel: <ctrl>+<alt>+j).")
        self._bindings = bindings
        self._loop = loop
        self._listener: Any = None

    def start(self) -> None:
        keyboard = need("pynput.keyboard", "Tastenkürzel")
        loop = self._loop

        def forward(action: Callable[[], None]) -> Callable[[], None]:
            def call() -> None:
                loop.call_soon_threadsafe(action)

            return call

        mapping = {combo: forward(action) for combo, action in self._bindings.items()}
        self._listener = keyboard.GlobalHotKeys(mapping)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

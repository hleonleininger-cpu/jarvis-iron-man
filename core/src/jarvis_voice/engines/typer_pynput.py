# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Diktat (M2.12): tippt Text als Tastenanschläge in das aktive Fenster. Nur Zeichen: keine
Zwischenablage (der Windows-Verlauf und die Cloud-Synchronisierung würden mitlesen), nie Enter,
Tab oder Steuerzeichen (in einem Terminal würde das einen Befehl ausführen)."""

from jarvis_voice.engines import need


class PynputTyper:
    def __init__(self) -> None:
        keyboard = need("pynput.keyboard", "Tastenkürzel und Diktat")
        self._controller = keyboard.Controller()

    def type(self, text: str) -> None:
        safe = "".join(ch for ch in text if ch.isprintable())  # auch hier: kein \n, \t, \x1b ...
        if safe:
            self._controller.type(safe)

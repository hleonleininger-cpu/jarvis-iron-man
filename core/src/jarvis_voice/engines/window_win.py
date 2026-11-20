# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Aktives Fenster (nur Windows, reines ctypes): das Diktat endet, wenn es wechselt."""

import sys


class ForegroundWatcher:
    def current(self) -> object | None:
        if sys.platform != "win32":
            return None  # unbekannt: dann gibt es keinen erkennbaren Wechsel
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow())  # type: ignore[attr-defined]

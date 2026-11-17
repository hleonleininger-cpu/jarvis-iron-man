"""Modell-Router (M1.1): kurze Fragen ohne Werkzeugbedarf gehen an das schnelle Modell, alles
andere an das starke. Ohne `model_fast` in jarvis.toml gibt es keinen Router und nichts ändert sich.
"""

FAST = "schnell"
STRONG = "stark"

# Längere Fragen sind selten "nur ein Satz Plauderei".
SHORT_CHARS = 160
# Ab dieser Ähnlichkeit zu einem Werkzeug gilt die Frage als Werkzeugfrage. Der Wert hängt vom
# Embedding-Modell ab; geprüft werden kann er nur mit dem echten Modell (einstellbar).
DEFAULT_TOOL_MATCH = 0.6


def choose_tier(message: str, best_tool_match: float | None, tool_match: float) -> str:
    """Das schnelle Modell nur, wenn sicher: kurze Frage UND klar kein Werkzeug gemeint.

    `best_tool_match` ist None, wenn die Werkzeuge nicht bewertet werden konnten (kein
    Embedding-Modell): dann immer das starke Modell, denn ein falsches Schnell-Urteil nähme
    der Frage ihre Werkzeuge.
    """
    if best_tool_match is None or len(message) > SHORT_CHARS:
        return STRONG
    return FAST if best_tool_match < tool_match else STRONG

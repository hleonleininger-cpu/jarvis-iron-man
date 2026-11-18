"""Zerlegt den gestreamten Antworttext in Sätze, damit die Sprachausgabe schon beim ersten
fertigen Satz beginnt (kurze Antwortzeit) statt auf die ganze Antwort zu warten."""

import re

# Satzende: . ! ? gefolgt von Leerraum und Großbuchstaben, oder ein Zeilenumbruch. Dezimalzahlen
# ("3.5") und Abkürzungen ("z. B.") trennen nicht.
_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„])|\n+")
_ABBREVIATIONS = frozenset({"z.", "d.", "u.", "ca.", "bzw.", "usw.", "evtl.", "dr.", "nr.", "etc."})
MIN_CHARS = 12  # kürzere Stücke wandern zum nächsten Satz (sonst stückelt die Stimme)

_MARKDOWN = re.compile(r"[*_`#>]+")
_LINK = re.compile(r"https?://\S+")


def speakable(text: str) -> str:
    """Entfernt, was man nicht vorliest: Markdown-Zeichen und Links."""
    text = _LINK.sub("", text)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_abbreviation(text: str) -> bool:
    """Ist das letzte Wort eine ganze Abkürzung ("z." in "z. B."), nicht nur "d." in "Dortmund."?"""
    words = text.split()
    return bool(words) and words[-1].casefold() in _ABBREVIATIONS


class SentenceChunker:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """Nimmt weiteren Text an und gibt die inzwischen fertigen Sätze zurück."""
        self._buffer += text
        sentences: list[str] = []
        while True:
            match = next(
                (
                    m
                    for m in _END.finditer(self._buffer)
                    if not _is_abbreviation(self._buffer[: m.start()])
                    and len(self._buffer[: m.start()]) >= MIN_CHARS
                ),
                None,
            )
            if match is None:
                break
            sentence = speakable(self._buffer[: match.start()])
            self._buffer = self._buffer[match.end() :]
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> list[str]:
        """Der Rest nach dem Ende des Stroms."""
        rest = speakable(self._buffer)
        self._buffer = ""
        return [rest] if rest else []

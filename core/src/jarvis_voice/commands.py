"""Feste Sprachbefehle. Sie werden ohne Modell erkannt und nur, wenn die GANZE Äußerung der
Phrase entspricht (optional mit "Jarvis" davor): ein Satz, der "Stopp" nur enthält, ist keiner."""

import re
import unicodedata
from enum import StrEnum


class Command(StrEnum):
    STOP = "stop"  # nur Sprechen und eigene Anfrage beenden
    EMERGENCY = "emergency"  # Not-Aus des Cores: stoppt alles, auch andere Geräte
    SLEEP = "sleep"
    WHISPER_ON = "whisper_on"
    WHISPER_OFF = "whisper_off"
    DICTATION_ON = "dictation_on"
    DICTATION_OFF = "dictation_off"
    YES = "yes"  # nur die feste Phrase "ja, bestätigt"
    NO = "no"


_PHRASES: dict[Command, tuple[str, ...]] = {
    Command.STOP: ("stopp", "stop", "halt", "sei still"),
    Command.EMERGENCY: ("not aus", "notaus", "emergency stop"),
    Command.SLEEP: ("gute nacht", "gute nacht jarvis", "good night"),
    Command.WHISPER_ON: ("flüstere", "flüstern an", "flüstermodus an", "whisper mode on"),
    Command.WHISPER_OFF: (
        "flüstern aus",
        "flüstermodus aus",
        "normal sprechen",
        "whisper mode off",
    ),
    Command.DICTATION_ON: ("diktiermodus an", "diktat an", "dictation on"),
    Command.DICTATION_OFF: ("diktat ende", "diktiermodus aus", "diktat aus", "dictation off"),
    Command.YES: ("ja bestätigt", "yes confirmed"),
    Command.NO: ("nein", "no", "abbrechen", "nicht ausführen"),
}


def normalize(text: str) -> str:
    """Klein, ohne Satzzeichen und ohne führendes "Jarvis"."""
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(r"[^\w\s-]", " ", text).replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    for lead in ("hey jarvis ", "jarvis "):
        if text.startswith(lead):
            text = text[len(lead) :]
    return text


def parse_command(text: str) -> Command | None:
    spoken = normalize(text)
    for command, phrases in _PHRASES.items():
        if spoken in phrases:
            return command
    return None

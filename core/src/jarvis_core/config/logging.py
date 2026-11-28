"""structlog: JSON-Logs auf stderr (stdout bleibt für Nutzerausgabe frei)."""

import logging
import sys
from contextlib import suppress

import structlog


class _StderrLogger:
    """Schreibt auf das jeweils aktuelle sys.stderr.

    Eine beim Konfigurieren gemerkte Datei kann später geschlossen sein (umgeleitete Ausgabe);
    ein Logeintrag darf nie die eigentliche Anfrage scheitern lassen.
    """

    def msg(self, message: str) -> None:
        with suppress(OSError, ValueError):
            print(message, file=sys.stderr, flush=True)

    log = debug = info = warning = error = critical = exception = msg


def configure_logging(level: str = "INFO") -> None:
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=lambda *_args: _StderrLogger(),
        cache_logger_on_first_use=False,
    )

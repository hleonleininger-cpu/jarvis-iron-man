"""Live-Test gegen echtes Ollama; wird übersprungen, wenn Ollama nicht erreichbar ist."""

import pytest

from jarvis_core.agent import build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.hardware import check_ollama, recommend
from jarvis_core.hardware.detect import detect_hardware

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_chat(settings: Settings) -> None:
    status = check_ollama(settings.ollama_base_url)
    if not status.running:
        pytest.skip("Ollama nicht erreichbar")
    wanted = settings.model or recommend(detect_hardware()).model
    if wanted not in status.models:
        pytest.skip(f"Modell {wanted} nicht installiert (ollama pull {wanted})")
    service = build_service(settings, AuditLog(settings.audit_db_path), model_name=wanted)
    text = "".join([d async for d in service.stream("Sag kurz Hallo.", channel="cli")])
    assert text.strip()

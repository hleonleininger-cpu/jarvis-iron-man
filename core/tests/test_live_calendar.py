"""Live-Test gegen echtes iCloud, NUR LESEND. Ohne gespeicherte Zugangsdaten übersprungen.

create_event wird bewusst nie automatisch live getestet.
"""

import pytest

from jarvis_core.secrets import is_set
from mcp_servers.calendar.server import list_events_data

pytestmark = pytest.mark.live


def test_live_list_events_week() -> None:
    if not is_set("apple_caldav"):
        pytest.skip("Keine Zugangsdaten (jarvis secrets set apple_caldav)")
    result = list_events_data("woche")
    assert result["ok"] is True, result
    assert isinstance(result["termine"], list)
    assert result["zeitzone"] == "Europe/Berlin"

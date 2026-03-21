from __future__ import annotations

import json
import time

from knowledge_graph import SecurityKnowledgeGraph
from security_monitor import EventType, SecurityMonitor


def _extract_metadata(description: str) -> dict:
    marker = "| metadata="
    if marker not in description:
        return {}
    payload = description.split(marker, 1)[1].strip()
    return json.loads(payload)


def test_coordinated_attack_detected_for_idtag_auth_flood() -> None:
    monitor = SecurityMonitor(use_persistence=False)
    monitor._kg = SecurityKnowledgeGraph(
        auth_window_seconds=30,
        idtag_chargepoint_threshold=5,
        event_fanout_window_seconds=30,
        event_fanout_threshold=99,
    )

    id_tag = "IDTAG-ATTACK-001"
    charge_points = [f"CP-{i}" for i in range(1, 7)]

    # Insert real auth-link events into the graph (no graph internals mocked).
    for cp in charge_points:
        monitor.record_auth_request(cp, id_tag, auth_success=False)

    deadline = time.time() + 3.0
    coordinated_event = None
    while time.time() < deadline:
        recent = monitor.get_recent_events(limit=200)
        coordinated = [e for e in recent if e.event_type == EventType.COORDINATED_ATTACK_DETECTED]
        if coordinated:
            coordinated_event = coordinated[-1]
            break
        time.sleep(0.05)

    assert coordinated_event is not None, "Expected COORDINATED_ATTACK_DETECTED to be emitted"

    metadata = _extract_metadata(coordinated_event.description)
    assert metadata, "Expected coordinated alert metadata in description"
    assert set(metadata.get("affected_charge_points", [])) == set(charge_points)
    assert metadata.get("involved_idtags") == [id_tag]
    assert metadata.get("time_window") == 30
    assert isinstance(metadata.get("graph_explanation_path"), str)
    assert metadata["graph_explanation_path"].startswith("inmemory://security_knowledge_graph/")

from __future__ import annotations

from enum import Enum


class SecurityEventType(str, Enum):
    """Canonical security event taxonomy for OCPP and EV charging security telemetry.

    Categories:
    - Protocol validation and parsing integrity anomalies.
    - Authorization, policy, and state-machine abuse events.
    - Charge manipulation and charging profile tampering attacks.
    - ISO 15118 session abuse and authentication cycling anomalies.
    - Coordinated or distributed denial-of-service attack indicators.
    """

    # Protocol and payload validation threats.
    OCPP_SCHEMA_VIOLATION = "OCPP_SCHEMA_VIOLATION"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    UNKNOWN_MESSAGE_TYPE = "UNKNOWN_MESSAGE_TYPE"

    # Authorization and policy enforcement threats.
    UNAUTHORIZED_ACTION = "UNAUTHORIZED_ACTION"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # Charge manipulation attack family.
    CHARGE_MANIPULATION_ATTACK = "CHARGE_MANIPULATION_ATTACK"
    PROFILE_TAMPERING = "PROFILE_TAMPERING"

    # ISO 15118 abuse detection.
    ISO15118_SESSION_ABUSE = "ISO15118_SESSION_ABUSE"

    # Distributed and coordinated infrastructure attacks.
    COORDINATED_ATTACK_DETECTED = "COORDINATED_ATTACK_DETECTED"
    DISTRIBUTED_DOS_ATTACK = "DISTRIBUTED_DOS_ATTACK"


def coerce_severity(value: int | str) -> int:
    """Normalize severity to numeric 1..10 for database constraints."""
    if isinstance(value, int):
        return max(1, min(10, value))
    mapped = {
        "low": 3,
        "medium": 5,
        "high": 8,
    }
    return mapped.get(str(value).lower().strip(), 5)

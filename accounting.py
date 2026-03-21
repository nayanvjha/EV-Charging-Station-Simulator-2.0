from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Set, Tuple

_CURRENT_PRICE_PER_KWH = Decimal("20")
_SESSION_ENERGY_KWH: Dict[str, Decimal] = {}
_FINALIZED_SESSIONS: Set[str] = set()
def ensure_session(session_id: str) -> None:
    if session_id not in _SESSION_ENERGY_KWH:
        _SESSION_ENERGY_KWH[session_id] = Decimal("0")


def update_session_energy(session_id: str, delta_energy_kwh: Decimal) -> None:
    if delta_energy_kwh <= 0:
        return
    if session_id in _FINALIZED_SESSIONS:
        raise RuntimeError("Session energy accumulator is finalized")
    if session_id not in _SESSION_ENERGY_KWH:
        _SESSION_ENERGY_KWH[session_id] = Decimal("0")
    _SESSION_ENERGY_KWH[session_id] += delta_energy_kwh


def finalize_session(session_id: str) -> None:
    _FINALIZED_SESSIONS.add(session_id)


def get_session_energy(session_id: str) -> Optional[Decimal]:
    return _SESSION_ENERGY_KWH.get(session_id)


def get_totals() -> Tuple[Decimal, Decimal]:
    # CSV energy must never drive totals.
    if "TOTAL_ENERGY_KWH" in globals() or "TOTAL_EARNINGS" in globals():
        raise RuntimeError("Direct totals mutation is forbidden; totals derive from sessions only")
    total_energy = Decimal("0")
    for session_id in sorted(_SESSION_ENERGY_KWH.keys()):
        total_energy += _SESSION_ENERGY_KWH[session_id]
    total_earnings = total_energy * _CURRENT_PRICE_PER_KWH
    return total_energy, total_earnings


def reset_totals() -> None:
    _SESSION_ENERGY_KWH.clear()
    _FINALIZED_SESSIONS.clear()


def get_price() -> Decimal:
    return _CURRENT_PRICE_PER_KWH


def set_price(price: float) -> None:
    global _CURRENT_PRICE_PER_KWH
    _CURRENT_PRICE_PER_KWH = Decimal(str(price))

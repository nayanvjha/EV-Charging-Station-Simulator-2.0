import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
    update,
)
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("db")

DB_PATH = os.environ.get(
    "SIMULATOR_DB_PATH",
    os.path.join(os.path.dirname(__file__), "simulator.db"),
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()

_db_initialized = False


class StationLog(Base):
    __tablename__ = "station_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    station_id = Column(String(64), nullable=False, index=True)
    message = Column(Text, nullable=False)


class SessionHistory(Base):
    __tablename__ = "session_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, nullable=False, index=True)
    station_id = Column(String(64), nullable=False, index=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    stop_time = Column(DateTime(timezone=True), nullable=True)
    energy_kwh = Column(Float, nullable=True)


class EnergySnapshot(Base):
    __tablename__ = "energy_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    energy_delivered_kwh = Column(Float, nullable=False)


class ChargingProfile(Base):
    __tablename__ = "charging_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String(64), nullable=False, index=True)
    profile_id = Column(Integer, nullable=True, index=True)
    profile_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    try:
        Base.metadata.create_all(bind=engine)
        _db_initialized = True
        logger.info("SQLite database initialized at %s", DB_PATH)
    except Exception as exc:
        logger.exception("Failed to initialize database: %s", exc)


def _ensure_db() -> None:
    if not _db_initialized:
        init_db()


def log_station_message(station_id: str, message: str, timestamp: Optional[datetime] = None) -> None:
    _ensure_db()
    ts = timestamp or datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            session.add(StationLog(timestamp=ts, station_id=station_id, message=message))
            session.commit()
    except Exception as exc:
        logger.exception("Failed to write StationLog: %s", exc)


def start_session_history(
    session_id: int,
    station_id: str,
    start_time: Optional[datetime] = None,
) -> None:
    _ensure_db()
    ts = start_time or datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            session.add(
                SessionHistory(
                    session_id=session_id,
                    station_id=station_id,
                    start_time=ts,
                )
            )
            session.commit()
    except Exception as exc:
        logger.exception("Failed to insert SessionHistory start: %s", exc)


def stop_session_history(
    session_id: int,
    station_id: str,
    stop_time: Optional[datetime] = None,
    energy_kwh: Optional[float] = None,
) -> None:
    _ensure_db()
    ts = stop_time or datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            stmt = (
                update(SessionHistory)
                .where(
                    SessionHistory.session_id == session_id,
                    SessionHistory.station_id == station_id,
                )
                .values(stop_time=ts, energy_kwh=energy_kwh)
            )
            session.execute(stmt)
            session.commit()
    except Exception as exc:
        logger.exception("Failed to update SessionHistory stop: %s", exc)


def add_energy_snapshot(
    station_id: str,
    energy_kwh: float,
    timestamp: Optional[datetime] = None,
) -> None:
    _ensure_db()
    ts = timestamp or datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            session.add(
                EnergySnapshot(
                    station_id=station_id,
                    timestamp=ts,
                    energy_delivered_kwh=energy_kwh,
                )
            )
            session.commit()
    except Exception as exc:
        logger.exception("Failed to insert EnergySnapshot: %s", exc)


def save_charging_profile(
    station_id: str,
    profile: Dict[str, Any],
    profile_id: Optional[int] = None,
    created_at: Optional[datetime] = None,
) -> None:
    _ensure_db()
    ts = created_at or datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            session.add(
                ChargingProfile(
                    station_id=station_id,
                    profile_id=profile_id,
                    profile_json=json.dumps(profile),
                    created_at=ts,
                )
            )
            session.commit()
    except Exception as exc:
        logger.exception("Failed to insert ChargingProfile: %s", exc)


def get_station_history(
    station_id: str,
    limit_logs: int = 200,
    limit_snapshots: int = 200,
) -> Dict[str, List[Dict[str, Any]]]:
    _ensure_db()
    logs: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    try:
        with SessionLocal() as session:
            log_stmt = (
                select(StationLog)
                .where(StationLog.station_id == station_id)
                .order_by(StationLog.timestamp.desc())
                .limit(limit_logs)
            )
            snapshot_stmt = (
                select(EnergySnapshot)
                .where(EnergySnapshot.station_id == station_id)
                .order_by(EnergySnapshot.timestamp.desc())
                .limit(limit_snapshots)
            )
            logs = [
                {
                    "timestamp": row.timestamp.isoformat(),
                    "station_id": row.station_id,
                    "message": row.message,
                }
                for row in session.execute(log_stmt).scalars().all()
            ]
            snapshots = [
                {
                    "timestamp": row.timestamp.isoformat(),
                    "station_id": row.station_id,
                    "energy_delivered_kwh": row.energy_delivered_kwh,
                }
                for row in session.execute(snapshot_stmt).scalars().all()
            ]
    except Exception as exc:
        logger.exception("Failed to read station history: %s", exc)
    return {"logs": logs, "energy_snapshots": snapshots}


def list_sessions(
    limit: int = 200,
    station_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _ensure_db()
    sessions: List[Dict[str, Any]] = []
    try:
        with SessionLocal() as session:
            stmt = select(SessionHistory)
            if station_id:
                stmt = stmt.where(SessionHistory.station_id == station_id)
            stmt = stmt.order_by(SessionHistory.start_time.desc()).limit(limit)
            sessions = [
                {
                    "session_id": row.session_id,
                    "station_id": row.station_id,
                    "start_time": row.start_time.isoformat(),
                    "stop_time": row.stop_time.isoformat() if row.stop_time else None,
                    "energy_kwh": row.energy_kwh,
                }
                for row in session.execute(stmt).scalars().all()
            ]
    except Exception as exc:
        logger.exception("Failed to list sessions: %s", exc)
    return sessions

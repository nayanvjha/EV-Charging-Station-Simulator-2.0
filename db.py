import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    Index,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("db")

DB_PATH = os.environ.get(
    "SIMULATOR_DB_PATH",
    os.path.join(os.path.dirname(__file__), "simulator.db"),
)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    api_key = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class SecurityEventRecord(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("severity BETWEEN 1 AND 10", name="ck_security_events_severity_range"),
        CheckConstraint("length(event_type) > 0", name="ck_security_events_event_type_nonempty"),
        Index("idx_security_events_charge_point_id", "charge_point_id"),
        Index("idx_security_events_timestamp_desc", "timestamp"),
        Index("idx_security_events_cp_timestamp_desc", "charge_point_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    charge_point_id = Column(String(100), nullable=False)
    event_type = Column(String(100), nullable=False, index=True)
    severity = Column(Integer, nullable=False, index=True)
    description = Column(Text, nullable=False)
    raw_message = Column(JSON, nullable=True)


def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    try:
        Base.metadata.create_all(bind=engine)
        _db_initialized = True
        logger.info("Database initialized: %s", DATABASE_URL)
    except Exception as exc:
        logger.exception("Failed to initialize database: %s", exc)


def _ensure_db() -> None:
    if not _db_initialized:
        init_db()


def _require_timestamp(value: object) -> datetime:
    if value is None:
        raise RuntimeError("Explicit timestamp required")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise RuntimeError("Explicit timestamp required") from exc
    raise RuntimeError("Explicit timestamp required")


def log_station_message(station_id: str, message: str, timestamp: object) -> None:
    _ensure_db()
    ts = _require_timestamp(timestamp)
    try:
        with SessionLocal() as session:
            session.add(StationLog(timestamp=ts, station_id=station_id, message=message))
            session.commit()
    except Exception as exc:
        logger.exception("Failed to write StationLog: %s", exc)


def start_session_history(
    session_id: int,
    station_id: str,
    start_time: object,
) -> None:
    _ensure_db()
    ts = _require_timestamp(start_time)
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
    stop_time: object,
    energy_kwh: Optional[float] = None,
) -> None:
    _ensure_db()
    ts = _require_timestamp(stop_time)
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
    timestamp: object,
) -> None:
    _ensure_db()
    ts = _require_timestamp(timestamp)
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


def get_latest_energy_snapshot(station_id: str) -> Optional[float]:
    _ensure_db()
    try:
        with SessionLocal() as session:
            stmt = (
                select(EnergySnapshot.energy_delivered_kwh)
                .where(EnergySnapshot.station_id == station_id)
                .order_by(EnergySnapshot.timestamp.desc())
                .limit(1)
            )
            value = session.execute(stmt).scalar_one_or_none()
            return float(value) if value is not None else None
    except Exception as exc:
        logger.exception("Failed to read latest EnergySnapshot: %s", exc)
        return None


def save_charging_profile(
    station_id: str,
    profile: Dict[str, Any],
    created_at: object,
    profile_id: Optional[int] = None,
) -> None:
    _ensure_db()
    ts = _require_timestamp(created_at)
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
    station_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    _ensure_db()
    sessions: List[Dict[str, Any]] = []
    try:
        with SessionLocal() as session:
            stmt = select(SessionHistory)
            if station_id:
                stmt = stmt.where(SessionHistory.station_id == station_id)
            elif station_ids:
                stmt = stmt.where(SessionHistory.station_id.in_(station_ids))
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


def insert_security_event(event: Dict[str, Any]) -> None:
    _ensure_db()
    try:
        charge_point_id = event.get("charge_point_id") or event.get("station_id")
        if not isinstance(charge_point_id, str) or not charge_point_id.strip():
            raise ValueError("charge_point_id is required")
        raw_severity = event.get("severity", 5)
        if isinstance(raw_severity, str):
            severity_map = {"low": 3, "medium": 5, "high": 8}
            severity_value = severity_map.get(raw_severity.lower(), 5)
        else:
            severity_value = int(raw_severity)
        if severity_value < 1 or severity_value > 10:
            raise ValueError("severity must be between 1 and 10")

        with SessionLocal() as session:
            record = SecurityEventRecord(
                timestamp=event.get("timestamp") or datetime.now(timezone.utc),
                charge_point_id=charge_point_id.strip(),
                event_type=event["event_type"],
                severity=severity_value,
                description=event["description"],
                raw_message=event.get("raw_message"),
            )
            session.add(record)
            session.commit()

        log_level = "info" if severity_value <= 3 else ("warn" if severity_value <= 6 else "error")
        payload = {
            "level": log_level,
            "timestamp": (event.get("timestamp") or datetime.now(timezone.utc)).isoformat(),
            "charge_point_id": charge_point_id.strip(),
            "event_type": str(event.get("event_type")),
            "severity": severity_value,
            "description": str(event.get("description", "")),
        }
        msg = json.dumps(payload, ensure_ascii=False)
        if log_level == "info":
            logger.info(msg)
        elif log_level == "warn":
            logger.warning(msg)
        else:
            logger.error(msg)
    except Exception as exc:
        logger.error(
            json.dumps(
                {
                    "level": "error",
                    "type": "SECURITY_EVENT_DB_FAILURE",
                    "message": "Failed to insert security event",
                    "error_class": exc.__class__.__name__,
                },
                ensure_ascii=False,
            )
        )


def get_security_events_recent(limit: int = 100) -> List[Dict[str, Any]]:
    _ensure_db()
    try:
        with SessionLocal() as session:
            stmt = (
                select(SecurityEventRecord)
                .order_by(SecurityEventRecord.timestamp.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "timestamp": row.timestamp,
                    "station_id": row.charge_point_id,
                    "charge_point_id": row.charge_point_id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "description": row.description,
                    "raw_message": row.raw_message,
                }
                for row in rows
            ]
    except Exception as exc:
        logger.exception("Failed to read SecurityEvent history: %s", exc)
        return []


def get_security_events_by_station(station_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    _ensure_db()
    try:
        with SessionLocal() as session:
            stmt = (
                select(SecurityEventRecord)
                .where(SecurityEventRecord.charge_point_id == station_id)
                .order_by(SecurityEventRecord.timestamp.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "timestamp": row.timestamp,
                    "station_id": row.charge_point_id,
                    "charge_point_id": row.charge_point_id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "description": row.description,
                    "raw_message": row.raw_message,
                }
                for row in rows
            ]
    except Exception as exc:
        logger.exception("Failed to read SecurityEvent for station: %s", exc)
        return []


def get_security_event_stats() -> Dict[str, Dict[str, int]]:
    _ensure_db()
    stats = {"by_type": {}, "by_severity": {}}
    try:
        with SessionLocal() as session:
            type_stmt = (
                select(SecurityEventRecord.event_type, func.count(SecurityEventRecord.id))
                .group_by(SecurityEventRecord.event_type)
            )
            severity_stmt = (
                select(SecurityEventRecord.severity, func.count(SecurityEventRecord.id))
                .group_by(SecurityEventRecord.severity)
            )
            for event_type, count in session.execute(type_stmt).all():
                stats["by_type"][event_type] = int(count)
            for severity, count in session.execute(severity_stmt).all():
                stats["by_severity"][severity] = int(count)
    except Exception as exc:
        logger.exception("Failed to read SecurityEvent stats: %s", exc)
    return stats


def get_security_events_paginated(
    page: int = 1,
    limit: int = 50,
    severity: Optional[int] = None,
    charge_point_id: Optional[str] = None,
    charge_point_ids: Optional[List[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    _ensure_db()
    if page < 1:
        page = 1
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    filters = []
    if severity is not None:
        filters.append(SecurityEventRecord.severity >= severity)
    if charge_point_id:
        filters.append(SecurityEventRecord.charge_point_id == charge_point_id)
    elif charge_point_ids:
        filters.append(SecurityEventRecord.charge_point_id.in_(charge_point_ids))
    if start_date is not None:
        filters.append(SecurityEventRecord.timestamp >= start_date)
    if end_date is not None:
        filters.append(SecurityEventRecord.timestamp <= end_date)

    offset = (page - 1) * limit
    try:
        with SessionLocal() as session:
            count_stmt = select(func.count(SecurityEventRecord.id))
            if filters:
                count_stmt = count_stmt.where(*filters)
            total = int(session.execute(count_stmt).scalar_one() or 0)

            data_stmt = select(SecurityEventRecord)
            if filters:
                data_stmt = data_stmt.where(*filters)
            data_stmt = data_stmt.order_by(SecurityEventRecord.timestamp.desc()).limit(limit).offset(offset)
            rows = session.execute(data_stmt).scalars().all()

            data = [
                {
                    "id": row.id,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "charge_point_id": row.charge_point_id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "description": row.description,
                }
                for row in rows
            ]
            return {
                "page": page,
                "limit": limit,
                "total": total,
                "data": data,
            }
    except Exception as exc:
        logger.exception("Failed to paginate SecurityEvent rows: %s", exc)
        return {
            "page": page,
            "limit": limit,
            "total": 0,
            "data": [],
        }


def get_security_event_type_stats(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    charge_point_id: Optional[str] = None,
    charge_point_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    _ensure_db()
    filters = []
    if start_date is not None:
        filters.append(SecurityEventRecord.timestamp >= start_date)
    if end_date is not None:
        filters.append(SecurityEventRecord.timestamp <= end_date)
    if charge_point_id:
        filters.append(SecurityEventRecord.charge_point_id == charge_point_id)
    elif charge_point_ids:
        filters.append(SecurityEventRecord.charge_point_id.in_(charge_point_ids))

    try:
        with SessionLocal() as session:
            stmt = select(SecurityEventRecord.event_type, func.count(SecurityEventRecord.id).label("count"))
            if filters:
                stmt = stmt.where(*filters)
            stmt = stmt.group_by(SecurityEventRecord.event_type).order_by(func.count(SecurityEventRecord.id).desc())
            rows = session.execute(stmt).all()
            return [{"event_type": event_type, "count": int(count)} for event_type, count in rows]
    except Exception as exc:
        logger.exception("Failed to read SecurityEvent grouped stats: %s", exc)
        return []


def clear_security_events() -> None:
    _ensure_db()
    try:
        with SessionLocal() as session:
            session.execute(delete(SecurityEventRecord))
            session.commit()
    except Exception as exc:
        logger.exception("Failed to clear SecurityEvent table: %s", exc)


def create_user(email: str, api_key: str, created_at: object) -> Dict[str, Any]:
    _ensure_db()
    ts = _require_timestamp(created_at)
    with SessionLocal() as session:
        user = User(email=email, api_key=api_key, created_at=ts)
        session.add(user)
        session.commit()
        session.refresh(user)
        return {
            "id": user.id,
            "email": user.email,
            "api_key": user.api_key,
            "created_at": user.created_at.isoformat(),
        }


def get_user_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    try:
        with SessionLocal() as session:
            stmt = select(User).where(User.api_key == api_key)
            user = session.execute(stmt).scalars().first()
            if not user:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "api_key": user.api_key,
                "created_at": user.created_at.isoformat(),
            }
    except Exception as exc:
        logger.exception("Failed to get user by api_key: %s", exc)
        return None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    try:
        with SessionLocal() as session:
            stmt = select(User).where(User.email == email)
            user = session.execute(stmt).scalars().first()
            if not user:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "api_key": user.api_key,
                "created_at": user.created_at.isoformat(),
            }
    except Exception as exc:
        logger.exception("Failed to get user by email: %s", exc)
        return None

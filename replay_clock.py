from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from replay_mode import (
    assert_real_csv_entry_active,
    assert_replay_mode_explicit,
    is_real_csv_mode,
    is_strict_mode,
)


@dataclass(frozen=True)
class ReplayClockConfig:
    acceleration: float = 1.0
    tick_seconds: float = 1.0


class ReplayClock:
    """
    Deterministic replay clock that maps historical timestamps to live simulation time.

    This component only advances replay time and notifies observers. It does not
    execute charging loops or emit protocol messages.
    """

    def __init__(
        self,
        start_time: datetime,
        end_time: datetime,
        config: Optional[ReplayClockConfig] = None,
    ) -> None:
        assert_replay_mode_explicit()
        if is_strict_mode():
            raise RuntimeError("ReplayClock is forbidden in STRICT mode")
        if not is_real_csv_mode():
            raise RuntimeError("ReplayClock runs only in non-STRICT modes")
        assert_real_csv_entry_active()

        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")

        self._config = config or ReplayClockConfig()
        if self._config.acceleration <= 0:
            raise ValueError("acceleration must be positive")
        if self._config.tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")

        self._start_time = start_time
        self._end_time = end_time
        self._replay_time = start_time
        self._running = False
        self._finished = False
        self._listeners: List[Callable[[datetime], None]] = []

    @property
    def replay_time(self) -> datetime:
        return self._replay_time

    @property
    def end_time(self) -> datetime:
        return self._end_time

    @property
    def tick_seconds(self) -> float:
        return self._config.tick_seconds

    @property
    def running(self) -> bool:
        return self._running

    @property
    def finished(self) -> bool:
        return self._finished

    def add_listener(self, listener: Callable[[datetime], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[datetime], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def start(self) -> None:
        if self._finished:
            raise RuntimeError("ReplayClock already finished")
        if self._running:
            return
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False

    def advance(self, wall_clock_delta_sec: float) -> datetime:
        if self._finished:
            raise RuntimeError("ReplayClock already finished")
        if not self._running:
            return self._replay_time
        if wall_clock_delta_sec < 0:
            raise ValueError("wall_clock_delta_sec must be non-negative")

        delta = wall_clock_delta_sec * self._config.acceleration
        candidate = self._replay_time + timedelta(seconds=delta)

        if candidate > self._end_time:
            raise RuntimeError("ReplayClock exceeded configured end_time")

        if candidate < self._replay_time:
            raise RuntimeError("ReplayClock must be monotonic")

        self._replay_time = candidate
        self._notify()
        return self._replay_time

    def extend_end_time(self, new_end_time: datetime) -> None:
        if new_end_time <= self._end_time:
            return
        self._end_time = new_end_time
        if self._finished and self._replay_time < self._end_time:
            self._finished = False

    def tick(self) -> datetime:
        if self._finished:
            raise RuntimeError("ReplayClock already finished")
        if not self._running:
            return self._replay_time
        return self.advance(self._config.tick_seconds)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self._replay_time)


__all__ = ["ReplayClock", "ReplayClockConfig"]

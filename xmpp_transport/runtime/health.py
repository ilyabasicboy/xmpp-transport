"""Process health state shared by runtime and HTTP adapter."""

from dataclasses import dataclass
from enum import Enum


class RuntimeStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass(frozen=True)
class HealthSnapshot:
    status: RuntimeStatus
    live: bool
    ready: bool


class HealthState:
    def __init__(self) -> None:
        self._status = RuntimeStatus.STARTING

    def mark_ready(self) -> None:
        self._status = RuntimeStatus.READY

    def mark_failed(self) -> None:
        self._status = RuntimeStatus.FAILED

    def mark_stopping(self) -> None:
        self._status = RuntimeStatus.STOPPING

    def snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(
            status=self._status,
            live=self._status in (RuntimeStatus.STARTING, RuntimeStatus.READY),
            ready=self._status is RuntimeStatus.READY,
        )

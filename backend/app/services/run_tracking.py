from datetime import datetime, timezone
from typing import Protocol


class LongRunningRun(Protocol):
    status: str
    error: str | None
    finished_at: datetime | None
    last_heartbeat_at: datetime | None


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def heartbeat(run: LongRunningRun) -> None:
    run.last_heartbeat_at = utcnow()


def finish_run(run: LongRunningRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = utcnow()
    heartbeat(run)

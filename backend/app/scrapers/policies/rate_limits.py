from dataclasses import dataclass
from time import monotonic, sleep


@dataclass
class RateLimiter:
    rate_limit_per_minute: int
    last_request_at: float | None = None

    def __post_init__(self) -> None:
        if self.rate_limit_per_minute <= 0:
            raise ValueError("rate_limit_per_minute must be positive")

    @property
    def delay_seconds(self) -> float:
        return 60.0 / self.rate_limit_per_minute

    def delay_until_next(self, now: float | None = None) -> float:
        if self.last_request_at is None:
            return 0.0
        current = monotonic() if now is None else now
        return max(0.0, self.delay_seconds - (current - self.last_request_at))

    def wait(self) -> float:
        delay = self.delay_until_next()
        if delay > 0:
            sleep(delay)
        self.last_request_at = monotonic()
        return delay

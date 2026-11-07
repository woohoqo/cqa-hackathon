"""Process-local request pacing; actual project quotas remain enforced by Gemini.

Share a single backend worker for the free-tier profile. Separate processes/apps
need an integrator-owned distributed limiter; a new API key does not add quota.
"""
import asyncio
import threading
import time

from .provider import TransientProviderError
from ..contracts import AIProviderError


class RequestGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._next_start: dict[str, float] = {}
        self._cooldown: dict[str, tuple[float, str]] = {}

    async def acquire(self, model: str, interval: float, max_cooldown_wait: float = 0.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                until, code = self._cooldown.get(model, (0.0, "provider_rate_limit"))
                cooldown = until - now
                if cooldown > 0:
                    if code == "provider_daily_quota_exhausted":
                        error = AIProviderError(code, "Gemini daily quota cooldown is active. Retry after quota reset.")
                        error.retry_after = cooldown
                        raise error
                    if cooldown > max_cooldown_wait:
                        raise TransientProviderError(
                            "provider_rate_limit", "Gemini quota cooldown is active. Retry later.",
                            retry_after=cooldown,
                        )
                delay = max(cooldown, self._next_start.get(model, 0.0) - now)
                if delay <= 0:
                    self._next_start[model] = now + interval
                    return
            # Do not reserve a future slot: cancellation must not consume it.
            await asyncio.sleep(delay)

    def defer(self, model: str, seconds: float, code: str = "provider_rate_limit") -> None:
        with self._lock:
            now = time.monotonic()
            previous_until, previous_code = self._cooldown.get(model, (0.0, code))
            # A later short minute-limit response cannot erase a daily-quota cause.
            if previous_until > now and previous_code == "provider_daily_quota_exhausted":
                code = previous_code
            self._cooldown[model] = (max(previous_until, now + seconds), code)


request_gate = RequestGate()

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
        self._group_next: dict[tuple[str, str], float] = {}
        self._group_cooldown: dict[tuple[str, str], tuple[float, str]] = {}
        self._connection_next: dict[str, float] = {}
        self._connection_cooldown: dict[str, tuple[float, str]] = {}
        self._open_circuit: dict[str, float] = {}
        self._disabled: set[str] = set()
        self._failures: dict[str, int] = {}
        self._inflight: dict[str, tuple[str, str]] = {}

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

    @staticmethod
    def _overlap(left: str, right: str) -> bool:
        # Unknown ownership is not evidence of an independent quota. An unknown
        # connection conservatively overlaps every known/unknown quota group.
        return left == right or "unknown" in (left, right)

    @staticmethod
    def _blocked(code: str, remaining: float = 0.0) -> AIProviderError:
        if code == "provider_authentication_failed":
            return AIProviderError(code, "Подключение отключено после ошибки доступа. Исправьте ключ или настройки модели.")
        if code in {"provider_daily_quota_exhausted", "provider_quota_exhausted"}:
            error = AIProviderError(code, "Группа квоты приостановлена. Проверьте квоту и повторите после её восстановления.")
        else:
            error = TransientProviderError(code, "Подключение временно недоступно. Повторите позднее.", retry_after=remaining)
        error.retry_after = remaining
        return error

    async def acquire_connection(self, connection, model: str, interval: float,
                                 max_cooldown_wait: float = 0.0) -> str:
        """Lease a credential/quota group until release, atomically with pacing.

        Holding a lease through error classification prevents concurrent jobs
        from discovering the same dead credential/quota with a request storm.
        Independent, explicitly named groups may run concurrently.
        """
        identity = connection.identity(model)
        group = connection.quota_group
        while True:
            with self._lock:
                now = time.monotonic()
                if identity in self._disabled:
                    raise self._blocked("provider_authentication_failed")
                if self._open_circuit.get(identity, 0.0) > now:
                    until, code = self._connection_cooldown[identity]
                    raise self._blocked(code, until - now)
                cooldowns = [self._cooldown.get(model, (0.0, "provider_rate_limit")),
                             self._connection_cooldown.get(identity, (0.0, "provider_unavailable"))]
                cooldowns.extend(value for (other_model, other_group), value in self._group_cooldown.items()
                                 if other_model == model and self._overlap(group, other_group))
                active = [(until, code) for until, code in cooldowns if until > now]
                if active:
                    until, code = max(active)
                    exhausted = [item for item in active if item[1] in {"provider_daily_quota_exhausted", "provider_quota_exhausted"}]
                    if exhausted:
                        until, code = max(exhausted)
                        raise self._blocked(code, until - now)
                    if until - now > max_cooldown_wait:
                        raise self._blocked(code, until - now)
                    cooldown_delay = until - now
                else:
                    cooldown_delay = 0.0
                busy = identity in self._inflight or any(
                    other_model == model and self._overlap(group, other_group)
                    for other_model, other_group in self._inflight.values()
                )
                starts = [self._next_start.get(model, 0.0), self._connection_next.get(identity, 0.0)]
                starts.extend(value for (other_model, other_group), value in self._group_next.items()
                              if other_model == model and self._overlap(group, other_group))
                delay = max(cooldown_delay, max(starts, default=0.0) - now, 0.01 if busy else 0.0)
                if delay <= 0:
                    self._connection_next[identity] = now + interval
                    self._group_next[(model, group)] = now + interval
                    self._inflight[identity] = (model, group)
                    return identity
            # Never reserve a future slot, and never sleep holding a thread lock.
            await asyncio.sleep(delay)

    def release_connection(self, identity: str) -> None:
        with self._lock:
            self._inflight.pop(identity, None)

    def succeeded(self, connection, model: str) -> None:
        with self._lock:
            identity = connection.identity(model)
            self._failures.pop(identity, None)
            self._connection_cooldown.pop(identity, None)
            self._open_circuit.pop(identity, None)

    def disable(self, connection, model: str) -> None:
        with self._lock:
            self._disabled.add(connection.identity(model))

    def defer_group(self, connection, model: str, seconds: float, code: str) -> None:
        with self._lock:
            key = (model, connection.quota_group)
            now = time.monotonic()
            previous_until, previous_code = self._group_cooldown.get(key, (0.0, code))
            if previous_until > now and previous_code in {"provider_daily_quota_exhausted", "provider_quota_exhausted"}:
                code = previous_code
            self._group_cooldown[key] = (max(previous_until, now + seconds), code)

    def transient_failure(self, connection, model: str, *, threshold: int,
                          backoff: float, circuit_seconds: float, code: str) -> bool:
        """Count consecutive failures across the queue; return whether circuit opened."""
        with self._lock:
            identity = connection.identity(model)
            count = self._failures.get(identity, 0) + 1
            self._failures[identity] = count
            opened = count >= threshold
            seconds = max(backoff, circuit_seconds if opened else 0.0)
            previous_until, _ = self._connection_cooldown.get(identity, (0.0, code))
            self._connection_cooldown[identity] = (max(previous_until, time.monotonic() + seconds), code)
            # After the cooldown one probe is allowed (lease serializes it).
            if opened:
                self._failures[identity] = 0
                self._open_circuit[identity] = self._connection_cooldown[identity][0]
            return opened


request_gate = RequestGate()

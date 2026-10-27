"""Resource Manager (plan.md sections 18 and 19).

The development machine has roughly 8 GB of RAM, so NAO initially allows
``max_concurrent_agents = 1``. The manager prefers to *wait* for a slot rather
than crash the machine, and it refuses to launch when available memory is
dangerously low.

``psutil`` is used when present; without it, NAO still enforces the concurrency
limit and simply skips the memory gate.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from .audit import AuditLogger
from .config import ResourceConfig
from .errors import FailureClass, StructuredError
from .models.events import EventType

try:  # pragma: no cover - exercised only by the environment
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


class ResourceUnavailable(Exception):
    """Raised when a slot cannot be acquired within the configured timeout."""

    def __init__(self, message: str):
        super().__init__(message)
        self.error = StructuredError(
            failure_class=FailureClass.RESOURCE_EXHAUSTION,
            message=message,
        )


@dataclass
class ResourceStats:
    max_concurrent_agents: int
    active_jobs: int
    available_ram_percent: Optional[float]
    disk_free_gb: Optional[float]
    cpu_percent: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "max_concurrent_agents": self.max_concurrent_agents,
            "active_jobs": self.active_jobs,
            "available_ram_percent": self.available_ram_percent,
            "disk_free_gb": self.disk_free_gb,
            "cpu_percent": self.cpu_percent,
        }


class ResourceManager:
    def __init__(
        self,
        config: ResourceConfig,
        audit: Optional[AuditLogger] = None,
        path: Optional[str] = None,
    ) -> None:
        self.config = config
        self.audit = audit
        self._semaphore = threading.BoundedSemaphore(config.max_concurrent_agents)
        self._active = 0
        self._lock = threading.Lock()
        self._path = path

    # -- memory / cpu / disk -----------------------------------------------
    def available_ram_percent(self) -> Optional[float]:
        if psutil is None:
            return None
        try:
            return float(psutil.virtual_memory().percent)
        except Exception:  # pragma: no cover - defensive
            return None

    def cpu_percent(self) -> Optional[float]:
        if psutil is None:
            return None
        try:
            return float(psutil.cpu_percent(interval=None))
        except Exception:  # pragma: no cover - defensive
            return None

    def disk_free_gb(self) -> Optional[float]:
        if psutil is None:
            return None
        try:
            usage = psutil.disk_usage(self._path or ".")
            return round(usage.free / (1024 ** 3), 2)
        except Exception:  # pragma: no cover - defensive
            return None

    def memory_ok(self) -> tuple[bool, str]:
        """Return (ok, reason). ``ok`` is True when RAM is unknown."""
        percent = self.available_ram_percent()
        if percent is None:
            return True, "memory stats unavailable"
        # virtual_memory().percent is *used* percent.
        available = 100.0 - percent
        if available < self.config.min_available_ram_percent:
            return False, f"only {available:.1f}% RAM available"
        return True, f"{available:.1f}% RAM available"

    def stats(self) -> ResourceStats:
        return ResourceStats(
            max_concurrent_agents=self.config.max_concurrent_agents,
            active_jobs=self._active,
            available_ram_percent=self.available_ram_percent(),
            disk_free_gb=self.disk_free_gb(),
            cpu_percent=self.cpu_percent(),
        )

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active

    # -- acquisition -------------------------------------------------------
    @contextmanager
    def acquire(
        self, job_id: Optional[str] = None, timeout: Optional[float] = None
    ) -> Iterator[None]:
        """Acquire a concurrency slot, waiting (not crashing) when busy."""
        timeout = timeout if timeout is not None else self.config.queue_timeout_seconds
        deadline = time.monotonic() + timeout

        acquired = self._semaphore.acquire(timeout=timeout)
        if not acquired:
            raise ResourceUnavailable(
                f"no agent slot available within {timeout}s "
                f"(max_concurrent_agents={self.config.max_concurrent_agents})"
            )

        try:
            # Wait for memory headroom before actually launching.
            while True:
                ok, reason = self.memory_ok()
                if ok:
                    break
                if time.monotonic() > deadline:
                    raise ResourceUnavailable(
                        f"insufficient memory after waiting: {reason}"
                    )
                if self.audit:
                    self.audit.record(
                        EventType.RESOURCE_WAIT,
                        job_id=job_id,
                        status="waiting",
                        message=reason,
                    )
                time.sleep(self.config.poll_interval_seconds)

            with self._lock:
                self._active += 1
            try:
                yield
            finally:
                with self._lock:
                    self._active -= 1
        finally:
            self._semaphore.release()

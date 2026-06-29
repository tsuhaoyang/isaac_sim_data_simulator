"""Clock abstraction (SPEC §5.4).

RealClock drives the live demo in wall-clock time. VirtualClock (M3) will let the
same policy run faster-than-real for capacity validation.
"""

import heapq
import itertools
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable


class Clock(ABC):
    @abstractmethod
    def now(self) -> float: ...

    @abstractmethod
    def call_later(self, delay_s: float, fn: Callable[[], None]):
        """Schedule fn to run after delay_s. Returns an implementation handle."""


class RealClock(Clock):
    def now(self) -> float:
        return time.time()

    def call_later(self, delay_s: float, fn: Callable[[], None]):
        timer = threading.Timer(max(delay_s, 0.0), fn)
        timer.daemon = True
        timer.start()
        return timer


class VirtualClock(Clock):
    """Discrete-event clock: jumps from event to event in virtual time.

    Lets the SAME SchedulingPolicy run far faster than real time for capacity
    validation (SPEC §5.4/§5.5). Not thread-safe — single-threaded by design.
    """

    def __init__(self):
        self._now = 0.0
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()  # tie-breaker -> FIFO for same-time events

    def now(self) -> float:
        return self._now

    def call_later(self, delay_s: float, fn: Callable[[], None]):
        item = (self._now + max(delay_s, 0.0), next(self._seq), fn)
        heapq.heappush(self._queue, item)
        return item

    def run(self, max_time: float | None = None) -> float:
        """Process events in time order until the queue drains (or max_time). Returns final time."""
        while self._queue:
            t, _, fn = self._queue[0]
            if max_time is not None and t > max_time:
                break
            heapq.heappop(self._queue)
            self._now = t
            fn()
        return self._now

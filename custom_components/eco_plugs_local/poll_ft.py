"""Runs a v1.x plug's polls together, once per cycle.

The plug lets one client write at a time, and holds that for a few seconds after the
client's last packet (docs/PROTOCOL_FT.md §5.4). Polls spread over separate timers keep
the hold forever and shut the vendor app out. Sending them back to back leaves the plug
quiet for most of each cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class FtPoller:
    """One per plug. Jobs run in registration order, on cycles where ``cycle % every == 0``."""

    def __init__(
        self, hass: HomeAssistant, interval: float, on_empty: Callable[[], None]
    ) -> None:
        self._hass = hass
        self._interval = interval
        self._on_empty = on_empty
        self._jobs: dict[str, tuple[int, Job]] = {}
        self._cycle = 0
        self._running = False
        self._cancel: Callable[[], None] | None = None

    @callback
    def register(self, key: str, every: int, job: Job) -> Callable[[], None]:
        """Run ``job`` every ``every`` cycles. The returned callable stops it."""
        self._jobs[key] = (every, job)
        if self._cancel is None:
            self._cancel = async_track_time_interval(
                self._hass, self._tick, timedelta(seconds=self._interval)
            )

        @callback
        def _unregister() -> None:
            if self._jobs.get(key, (0, None))[1] is job:
                del self._jobs[key]
            if not self._jobs and self._cancel is not None:
                self._cancel()
                self._cancel = None
                self._on_empty()

        return _unregister

    async def _tick(self, _now: Any = None) -> None:
        if self._running:
            return
        self._running = True
        try:
            cycle = self._cycle
            self._cycle += 1
            for key, (every, job) in list(self._jobs.items()):
                if cycle % every:
                    continue
                try:
                    await job()
                except Exception:
                    _LOGGER.exception("Poll job %s failed", key)
        finally:
            self._running = False

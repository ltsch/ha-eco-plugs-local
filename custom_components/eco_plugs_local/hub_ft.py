"""Shared UDP socket for v1.x plugs: search, status polling and commands.

One per HA instance. The plug only accepts set commands from source port 8900,
so that port belongs to the host. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
import logging
import time

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    COMMAND_ATTEMPTS,
    DOMAIN,
    FT_CONFIRM_DELAY,
    FT_DISCOVERY_SECONDS,
    FT_POLL_ATTEMPTS,
    REPLY_TIMEOUT,
)
from .hub import CommandError, _Datagram, _udp_socket
from .poll_ft import FtPoller
from .protocol_ft import (
    CMD_ADD_TIMER,
    CMD_DELETE_TIMER,
    CMD_EDIT_TIMER,
    CMD_GET_POWER,
    CMD_GET_RANDOM,
    CMD_GET_SWITCH,
    CMD_GET_TIMERS,
    CMD_SET_RANDOM,
    CMD_SET_SWITCH,
    CMD_SET_TIMEZONE,
    MARK_BAD_DATE,
    MARK_NO_PERMIT,
    MARK_OK,
    SEARCH_PORTS,
    SEARCH_REPLY_LENGTH,
    SOURCE_PORT,
    FtDevice,
    PowerReading,
    Response,
    TimerSettings,
    build_command,
    build_search,
    dst_payload,
    new_sequence,
    parse_power,
    parse_random,
    parse_response,
    parse_search_reply,
    parse_timers,
    power_request_payload,
    random_payload,
    switch_state,
    timer_list_entries,
    with_timer_added,
    with_timer_removed,
    with_timer_updated,
)

_LOGGER = logging.getLogger(__name__)

_HUB_KEY = f"{DOMAIN}_ft_hub"
# Search replies are unauthenticated; bound the cache so forged MACs can't grow it.
_MAX_SIGHTINGS = 64
_SIGHTING_TTL = 300.0

SEARCH_BROADCAST = "255.255.255.255"
SEARCH_TARGETS = tuple((SEARCH_BROADCAST, port) for port in SEARCH_PORTS)
UNICAST_SEARCH_PORT = SEARCH_PORTS[-1]  # the port the plug answers from


@dataclass(slots=True)
class FtSighting:
    """Most recent search reply from one device."""

    device: FtDevice
    host: str  # source address of the reply datagram
    monotonic: float
    clock_difference: float | None = None  # plug wall clock minus HA's, in seconds


SightingCallback = Callable[[FtSighting], None]
SettingsCallback = Callable[[TimerSettings], None]


class EcoPlugsFtHub:
    """Talks to every configured v1.x plug over one shared socket."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.sightings: dict[str, FtSighting] = {}
        self._watched: set[str] = set()
        self._subscribers: dict[str, list[SightingCallback]] = {}
        self._settings: dict[str, TimerSettings] = {}
        self._settings_subscribers: dict[str, list[SettingsCallback]] = {}
        self._pollers: dict[str, FtPoller] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: dict[tuple[int, int, str], asyncio.Future[Response]] = {}
        self._seen = asyncio.Event()
        self._users = 0
        self._lifecycle_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

    async def async_acquire(self) -> None:
        """Bind the socket (reference counted)."""
        async with self._lifecycle_lock:
            if self._transport is None:
                try:
                    sock = _udp_socket(SOURCE_PORT, share=False)
                except OSError as err:
                    raise CommandError(
                        f"Cannot bind UDP source port {SOURCE_PORT} "
                        f"(the plug only accepts commands from it): {err}"
                    ) from err
                self._transport, _ = await self.hass.loop.create_datagram_endpoint(
                    lambda: _Datagram(self._on_datagram), sock=sock
                )
            self._users += 1

    async def async_release(self) -> None:
        """Close the socket once the last user is gone."""
        async with self._lifecycle_lock:
            self._users = max(0, self._users - 1)
            if self._users:
                return
            if self._transport is not None:
                self._transport.close()
            self._transport = None

    @callback
    def watch(self, unique_id: str):
        """Keep this device in the cache; returns the callable that stops."""
        self._watched.add(unique_id)

        @callback
        def _unwatch() -> None:
            self._watched.discard(unique_id)

        return _unwatch

    @callback
    def subscribe(self, unique_id: str, cb: SightingCallback) -> Callable[[], None]:
        """Call ``cb`` for every search reply from ``unique_id``."""
        self._subscribers.setdefault(unique_id, []).append(cb)

        @callback
        def _unsub() -> None:
            subs = self._subscribers.get(unique_id, [])
            if cb in subs:
                subs.remove(cb)
            if not subs:
                self._subscribers.pop(unique_id, None)

        return _unsub

    @callback
    def subscribe_settings(
        self, unique_id: str, cb: SettingsCallback
    ) -> Callable[[], None]:
        """Call ``cb`` whenever the plug's timers and random flags are read."""
        self._settings_subscribers.setdefault(unique_id, []).append(cb)

        @callback
        def _unsub() -> None:
            subs = self._settings_subscribers.get(unique_id, [])
            if cb in subs:
                subs.remove(cb)
            if not subs:
                self._settings_subscribers.pop(unique_id, None)

        return _unsub

    def timer_settings(self, unique_id: str) -> TimerSettings | None:
        return self._settings.get(unique_id)

    def poller(self, unique_id: str, interval: float) -> FtPoller:
        """The plug's burst scheduler. It exists while something is registered with it."""
        poller = self._pollers.get(unique_id)
        if poller is None:
            poller = self._pollers[unique_id] = FtPoller(
                self.hass, interval, lambda: self._pollers.pop(unique_id, None)
            )
        return poller

    def sighting(self, unique_id: str) -> FtSighting | None:
        return self.sightings.get(unique_id)

    def recent(self, max_age: float) -> dict[str, FtSighting]:
        now = time.monotonic()
        return {
            uid: s for uid, s in self.sightings.items() if now - s.monotonic <= max_age
        }

    @callback
    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) == SEARCH_REPLY_LENGTH:
            if (device := parse_search_reply(data)) is not None:
                self._record(device, addr[0])
            return
        response = parse_response(data)
        if response is None:
            return
        future = self._pending.get((response.cmd, response.sequence, addr[0]))
        if future is not None and not future.done():
            future.set_result(response)

    def _record(self, device: FtDevice, host: str) -> None:
        now = time.monotonic()
        if device.unique_id not in self.sightings:
            self._prune(now)
        difference = None
        if device.clock is not None:
            local = dt_util.now().replace(tzinfo=None)
            difference = (device.clock.local - local).total_seconds()
        sighting = FtSighting(device, host, now, difference)
        self.sightings[device.unique_id] = sighting
        self._seen.set()
        for cb in list(self._subscribers.get(device.unique_id, ())):
            cb(sighting)

    def _prune(self, now: float) -> None:
        stale = [
            uid
            for uid, s in self.sightings.items()
            if uid not in self._watched and now - s.monotonic > _SIGHTING_TTL
        ]
        for uid in stale:
            del self.sightings[uid]
        while len(self.sightings) >= _MAX_SIGHTINGS:
            unwatched = [u for u in self.sightings if u not in self._watched]
            if not unwatched:
                break
            del self.sightings[
                min(unwatched, key=lambda u: self.sightings[u].monotonic)
            ]

    def _require_transport(self) -> asyncio.DatagramTransport:
        if self._transport is None:
            raise CommandError("The Eco Plugs socket is not open")
        return self._transport

    @callback
    def send_search(self, host: str | None = None) -> None:
        """Broadcast a search, or unicast one to ``host``. Replies land in ``sightings``."""
        transport = self._require_transport()
        packet = build_search()
        targets = SEARCH_TARGETS if host is None else ((host, UNICAST_SEARCH_PORT),)
        for target in targets:
            transport.sendto(packet, target)

    async def async_refresh(self, unique_id: str, broadcast: bool = True) -> bool:
        """Search again (at the known address, and by broadcast); True if the plug answered."""
        before = self.sightings.get(unique_id)
        if before is not None:
            self.send_search(before.host)
        if broadcast or before is None:
            self.send_search()
        deadline = self.hass.loop.time() + REPLY_TIMEOUT
        while True:
            now = self.sightings.get(unique_id)
            if now is not None and (before is None or now.monotonic > before.monotonic):
                return True
            self._seen.clear()
            remaining = deadline - self.hass.loop.time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self._seen.wait(), remaining)
            except TimeoutError:
                return False

    async def _async_exchange(
        self, unique_id: str, cmd: int, payload: bytes = b"", attempts: int = 1
    ) -> Response:
        """Send one command and return the reply to that exact request.

        Caller holds the request lock. If the plug stays silent it is searched
        for again, because its address may have changed.
        """
        transport = self._require_transport()
        for attempt in range(1, attempts + 1):
            sighting = self.sightings.get(unique_id)
            if sighting is None:
                if not await self.async_refresh(unique_id):
                    continue
                sighting = self.sightings[unique_id]
            sequence = new_sequence()
            key = (cmd, sequence, sighting.host)
            future: asyncio.Future[Response] = self.hass.loop.create_future()
            self._pending[key] = future
            try:
                transport.sendto(
                    build_command(cmd, sighting.device, payload, sequence=sequence),
                    (sighting.host, sighting.device.port),
                )
                return await asyncio.wait_for(future, REPLY_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug(
                    "No reply from %s (attempt %s/%s)", sighting.host, attempt, attempts
                )
                if attempt < attempts:
                    await self.async_refresh(unique_id)
            finally:
                self._pending.pop(key, None)
        raise CommandError("The plug did not answer; is it online?")

    async def async_get_state(
        self, unique_id: str, attempts: int = FT_POLL_ATTEMPTS
    ) -> bool:
        """Ask the plug for its relay state."""
        async with self._request_lock:
            response = await self._async_exchange(
                unique_id, CMD_GET_SWITCH, attempts=attempts
            )
        state = switch_state(response)
        if state is None:
            raise CommandError("The plug sent an unexpected status reply")
        return state

    async def async_get_power(
        self, unique_id: str, day: date, attempts: int = FT_POLL_ATTEMPTS
    ) -> PowerReading:
        """Read voltage, current, power and the energy used on ``day``."""
        async with self._request_lock:
            response = await self._async_exchange(
                unique_id,
                CMD_GET_POWER,
                power_request_payload(day),
                attempts=attempts,
            )
        if response.mark == MARK_BAD_DATE:
            raise CommandError("The plug rejected the date range")
        reading = parse_power(response)
        if reading is None:
            raise CommandError("The plug sent an unexpected power reply")
        return reading

    async def _async_read_timers(self, unique_id: str, attempts: int) -> TimerSettings:
        """Read the timers and the random record. Caller holds the request lock."""
        timers_reply = await self._async_exchange(
            unique_id, CMD_GET_TIMERS, attempts=attempts
        )
        random_reply = await self._async_exchange(
            unique_id, CMD_GET_RANDOM, attempts=attempts
        )
        timers = parse_timers(timers_reply)
        mode = parse_random(random_reply)
        if timers is None or mode is None:
            raise CommandError("The plug sent an unexpected timer reply")
        settings = TimerSettings(timers, random=mode.random, vacation=mode.vacation)
        self._settings[unique_id] = settings
        for cb in list(self._settings_subscribers.get(unique_id, ())):
            cb(settings)
        return settings

    async def async_get_timers(
        self, unique_id: str, attempts: int = FT_POLL_ATTEMPTS
    ) -> TimerSettings:
        """Read the plug's timers and its random and vacation flags."""
        async with self._request_lock:
            return await self._async_read_timers(unique_id, attempts)

    @staticmethod
    def _check_write(response: Response) -> None:
        if response.mark == MARK_NO_PERMIT:
            raise CommandError(
                "The plug refused the command. Another client, such as the vendor "
                "app, may have used it in the last few seconds; try again"
            )
        if response.mark != MARK_OK:
            raise CommandError(f"The plug rejected the command (code {response.mark})")

    async def async_set_dst(self, unique_id: str, on: bool) -> None:
        """Set the plug's daylight-saving flag and read it back.

        Raises CommandError if the plug refuses, or doesn't report the new flag.
        """
        async with self._request_lock:
            response = await self._async_exchange(
                unique_id,
                CMD_SET_TIMEZONE,
                dst_payload(on),
                attempts=COMMAND_ATTEMPTS,
            )
            self._check_write(response)
            reported: bool | None = None
            for _ in range(COMMAND_ATTEMPTS):
                if await self.async_refresh(unique_id, broadcast=False):
                    clock = self.sightings[unique_id].device.clock
                    reported = None if clock is None else clock.dst
                    if reported == on:
                        return
                await asyncio.sleep(FT_CONFIRM_DELAY)
        if reported is None:
            raise CommandError("Plug acknowledged but sent no valid clock")
        raise CommandError(
            f"Plug acknowledged but reports daylight saving {'on' if reported else 'off'}"
        )

    async def async_set_random(self, unique_id: str, on: bool) -> None:
        """Set the plug's "random timers" flag and read it back."""
        async with self._request_lock:
            current = parse_random(
                await self._async_exchange(
                    unique_id, CMD_GET_RANDOM, attempts=FT_POLL_ATTEMPTS
                )
            )
            if current is None:
                raise CommandError("The plug sent an unexpected random-mode reply")
            response = await self._async_exchange(
                unique_id,
                CMD_SET_RANDOM,
                random_payload(current, on),
                attempts=COMMAND_ATTEMPTS,
            )
            self._check_write(response)
            for _ in range(COMMAND_ATTEMPTS):
                settings = await self._async_read_timers(unique_id, FT_POLL_ATTEMPTS)
                if settings.random == on:
                    return
                await asyncio.sleep(FT_CONFIRM_DELAY)
        raise CommandError(
            f"Plug acknowledged but reports random timers {'on' if settings.random else 'off'}"
        )

    async def _async_change_timers(
        self, unique_id: str, cmd: int, change: Callable[[bytes], bytes]
    ) -> TimerSettings:
        """Read the list, apply ``change``, send the WHOLE list, and read it back.

        A list that doesn't look healthy is left alone. If the plug doesn't end up with
        what was sent, the list it had before is sent back.
        """
        async with self._request_lock:
            reply = await self._async_exchange(
                unique_id, CMD_GET_TIMERS, attempts=FT_POLL_ATTEMPTS
            )
            if reply.mark != MARK_OK or timer_list_entries(reply.payload) is None:
                raise CommandError(
                    "The plug's timer list looks damaged, so it was left alone"
                )
            original = bytes(reply.payload)
            try:
                wanted = change(original)
            except ValueError as err:
                raise CommandError(str(err)) from err
            response = await self._async_exchange(
                unique_id, cmd, wanted, attempts=COMMAND_ATTEMPTS
            )
            self._check_write(response)
            for _ in range(COMMAND_ATTEMPTS):
                check = await self._async_exchange(
                    unique_id, CMD_GET_TIMERS, attempts=FT_POLL_ATTEMPTS
                )
                if check.payload == wanted:
                    return await self._async_read_timers(unique_id, FT_POLL_ATTEMPTS)
                await asyncio.sleep(FT_CONFIRM_DELAY)
            await self._async_exchange(
                unique_id, CMD_EDIT_TIMER, original, attempts=COMMAND_ATTEMPTS
            )
        raise CommandError(
            "The plug did not take the timer change, so the previous list was sent back"
        )

    async def async_add_timer(
        self, unique_id: str, days: list[str], start: int, end: int, today: date
    ) -> TimerSettings:
        return await self._async_change_timers(
            unique_id,
            CMD_ADD_TIMER,
            lambda raw: with_timer_added(raw, days, start, end, today),
        )

    async def async_update_timer(
        self,
        unique_id: str,
        timer_id: int,
        days: list[str] | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> TimerSettings:
        return await self._async_change_timers(
            unique_id,
            CMD_EDIT_TIMER,
            lambda raw: with_timer_updated(raw, timer_id, days, start, end),
        )

    async def async_delete_timer(self, unique_id: str, timer_id: int) -> TimerSettings:
        return await self._async_change_timers(
            unique_id,
            CMD_DELETE_TIMER,
            lambda raw: with_timer_removed(raw, timer_id),
        )

    async def async_set_power(self, unique_id: str, on: bool) -> None:
        """Set the relay; return only once the plug reports the requested state.

        Raises CommandError otherwise.
        """
        async with self._request_lock:
            response = await self._async_exchange(
                unique_id,
                CMD_SET_SWITCH,
                bytes([1, int(on)]),
                attempts=COMMAND_ATTEMPTS,
            )
            if response.mark == MARK_NO_PERMIT:
                raise CommandError("The plug refused the command")
            if response.mark != MARK_OK:
                raise CommandError(
                    f"The plug rejected the command (code {response.mark})"
                )
            reported: bool | None = None
            for _ in range(COMMAND_ATTEMPTS):
                reported = switch_state(
                    await self._async_exchange(
                        unique_id, CMD_GET_SWITCH, attempts=FT_POLL_ATTEMPTS
                    )
                )
                if reported == on:
                    return
                await asyncio.sleep(FT_CONFIRM_DELAY)
        if reported is None:
            raise CommandError("Plug acknowledged but sent no valid state")
        raise CommandError(
            f"Plug acknowledged but reports {'on' if reported else 'off'}"
        )


def get_ft_hub(hass: HomeAssistant) -> EcoPlugsFtHub:
    """Return the shared hub, creating it if needed."""
    hub: EcoPlugsFtHub | None = hass.data.get(_HUB_KEY)
    if hub is None:
        hub = hass.data[_HUB_KEY] = EcoPlugsFtHub(hass)
    return hub


async def async_ft_discover(hass: HomeAssistant) -> dict[str, FtSighting]:
    """Search for a few seconds and return what answered."""
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    try:
        hub.send_search()
        await asyncio.sleep(FT_DISCOVERY_SECONDS / 2)
        hub.send_search()
        await asyncio.sleep(FT_DISCOVERY_SECONDS / 2)
        return hub.recent(FT_DISCOVERY_SECONDS * 2)
    finally:
        await hub.async_release()

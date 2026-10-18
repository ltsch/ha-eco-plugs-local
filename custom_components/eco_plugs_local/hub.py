"""Shared UDP beacon listener and command sender (one per HA instance).

The beacon port and command source port are fixed by the firmware, so all
plugs share them. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import socket
import time

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import COMMAND_ATTEMPTS, DISCOVERY_SECONDS, DOMAIN, REPLY_TIMEOUT
from .protocol import (
    BEACON_LISTEN_PORT,
    COMMAND_SOURCE_PORT,
    COMMAND_SUBTYPES,
    Beacon,
    beacon_fingerprint,
    build_set_power,
    parse_beacon,
    parse_status_reply,
)

_LOGGER = logging.getLogger(__name__)

_HUB_KEY = f"{DOMAIN}_hub"
# Beacons are unauthenticated; bound the cache so forged MACs can't grow it.
_MAX_SIGHTINGS = 64
_SIGHTING_TTL = 300.0


class CommandError(HomeAssistantError):
    """Raised when a command could not be delivered or confirmed."""


@dataclass(slots=True)
class Sighting:
    """Most recent beacon from one device."""

    beacon: Beacon
    host: str  # source address of the beacon datagram
    monotonic: float
    fingerprint: bytes = b""  # per-packet XOR key; identifies duplicate copies


BeaconCallback = Callable[[Sighting], None]


class _Datagram(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)


def _udp_socket(port: int, *, share: bool) -> socket.socket:
    """Bind a UDP socket on all interfaces.

    ``share`` allows other listeners on the host to bind the same port (needed
    for broadcasts). The command port is exclusive so no other process can
    receive the plug's replies.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if share:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        sock.bind(("", port))
    except OSError:
        sock.close()
        raise
    return sock


class EcoPlugsHub:
    """Listens for beacons and sends commands for every configured plug."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.sightings: dict[str, Sighting] = {}
        self._subscribers: dict[str, list[BeaconCallback]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._command_transport: asyncio.DatagramTransport | None = None
        self._on_reply: Callable[[bytes, tuple[str, int]], None] | None = None
        self._users = 0
        self._lifecycle_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()

    async def async_acquire(self) -> None:
        """Start listening (reference counted)."""
        async with self._lifecycle_lock:
            if self._transport is None:
                try:
                    sock = _udp_socket(BEACON_LISTEN_PORT, share=True)
                except OSError as err:
                    raise CommandError(
                        f"Cannot listen on UDP port {BEACON_LISTEN_PORT}: {err}"
                    ) from err
                self._transport, _ = await self.hass.loop.create_datagram_endpoint(
                    lambda: _Datagram(self._on_beacon_datagram), sock=sock
                )
            self._users += 1

    async def async_release(self) -> None:
        """Close both sockets once the last user is gone."""
        async with self._lifecycle_lock:
            self._users = max(0, self._users - 1)
            if self._users:
                return
            for transport in (self._transport, self._command_transport):
                if transport is not None:
                    transport.close()
            self._transport = self._command_transport = None

    @callback
    def _on_beacon_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        beacon = parse_beacon(data)
        if beacon is None:
            return
        # A multi-homed host (e.g. wired + Wi-Fi) receives each broadcast once per
        # interface; counting copies would defeat the debounce.
        fingerprint = beacon_fingerprint(data)
        previous = self.sightings.get(beacon.unique_id)
        if previous is not None and previous.fingerprint == fingerprint:
            return
        sighting = Sighting(beacon, addr[0], time.monotonic(), fingerprint)
        if beacon.unique_id not in self.sightings:
            self._prune(sighting.monotonic)
        self.sightings[beacon.unique_id] = sighting
        for cb in list(self._subscribers.get(beacon.unique_id, ())):
            cb(sighting)

    def _prune(self, now: float) -> None:
        stale = [
            uid
            for uid, s in self.sightings.items()
            if uid not in self._subscribers and now - s.monotonic > _SIGHTING_TTL
        ]
        for uid in stale:
            del self.sightings[uid]
        while len(self.sightings) >= _MAX_SIGHTINGS:
            unsubscribed = [u for u in self.sightings if u not in self._subscribers]
            if not unsubscribed:
                break
            del self.sightings[
                min(unsubscribed, key=lambda u: self.sightings[u].monotonic)
            ]

    @callback
    def subscribe(self, unique_id: str, cb: BeaconCallback) -> Callable[[], None]:
        """Call ``cb`` for every valid beacon from ``unique_id``."""
        self._subscribers.setdefault(unique_id, []).append(cb)

        @callback
        def _unsub() -> None:
            subs = self._subscribers.get(unique_id, [])
            if cb in subs:
                subs.remove(cb)
            if not subs:
                self._subscribers.pop(unique_id, None)

        return _unsub

    def recent(self, max_age: float) -> dict[str, Sighting]:
        now = time.monotonic()
        return {
            uid: s for uid, s in self.sightings.items() if now - s.monotonic <= max_age
        }

    async def _async_command_transport(self) -> asyncio.DatagramTransport:
        """Bind the command port on first use; it stays open until release."""
        if self._command_transport is None:
            try:
                sock = _udp_socket(COMMAND_SOURCE_PORT, share=False)
            except OSError as err:
                raise CommandError(
                    f"Cannot bind UDP source port {COMMAND_SOURCE_PORT} "
                    f"(the plug only accepts commands from it): {err}"
                ) from err
            self._command_transport, _ = await self.hass.loop.create_datagram_endpoint(
                lambda: _Datagram(self._on_command_datagram), sock=sock
            )
        return self._command_transport

    @callback
    def _on_command_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._on_reply is not None:
            self._on_reply(data, addr)

    async def async_set_power(self, unique_id: str, on: bool, max_age: float) -> None:
        """Send a set-power command; return only once the plug confirms it.

        Raises CommandError otherwise.
        """
        async with self._command_lock:
            sighting = self.recent(max_age).get(unique_id)
            if sighting is None:
                raise CommandError("No recent beacon from this plug; is it online?")
            beacon, host = sighting.beacon, sighting.host
            transport = await self._async_command_transport()
            confirmed: asyncio.Future[None] = self.hass.loop.create_future()
            last_reply: list[bool] = []

            @callback
            def on_reply(data: bytes, addr: tuple[str, int]) -> None:
                state = parse_status_reply(data) if addr[0] == host else None
                if state is None:
                    return
                last_reply.append(state)
                if state == on and not confirmed.done():
                    confirmed.set_result(None)

            self._on_reply = on_reply
            try:
                for attempt in range(1, COMMAND_ATTEMPTS + 1):
                    for subtype in COMMAND_SUBTYPES:
                        transport.sendto(
                            build_set_power(beacon, on, subtype),
                            (host, beacon.command_port),
                        )
                    try:
                        await asyncio.wait_for(asyncio.shield(confirmed), REPLY_TIMEOUT)
                    except TimeoutError:
                        _LOGGER.debug(
                            "No confirmation from %s (attempt %s/%s)",
                            host,
                            attempt,
                            COMMAND_ATTEMPTS,
                        )
                        continue
                    return
            finally:
                self._on_reply = None

        if last_reply:
            raise CommandError(
                f"Plug replied but reports {'on' if last_reply[-1] else 'off'}"
            )
        raise CommandError(f"Plug at {host} did not acknowledge the command")


def get_hub(hass: HomeAssistant) -> EcoPlugsHub:
    """Return the shared hub, creating it if needed."""
    hub: EcoPlugsHub | None = hass.data.get(_HUB_KEY)
    if hub is None:
        hub = hass.data[_HUB_KEY] = EcoPlugsHub(hass)
    return hub


async def async_discover(hass: HomeAssistant) -> dict[str, Sighting]:
    """Collect beacons for a few seconds and return what was seen."""
    hub = get_hub(hass)
    await hub.async_acquire()
    try:
        await asyncio.sleep(DISCOVERY_SECONDS)
        return hub.recent(DISCOVERY_SECONDS * 2)
    finally:
        await hub.async_release()

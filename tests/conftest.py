"""Shared fixtures."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import socket
import time
from unittest.mock import patch

from homeassistant.util import dt as dt_util
import pytest

from custom_components.eco_plugs_local import protocol as P

from .beacon_factory import make_reply
from .ft_factory import make_response, make_search_reply


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def ephemeral_ports(socket_enabled):
    """Never touch the real ports (or real plugs) on the test host."""
    with (
        patch("custom_components.eco_plugs_local.hub.BEACON_LISTEN_PORT", 0),
        patch("custom_components.eco_plugs_local.hub.COMMAND_SOURCE_PORT", 0),
        patch("custom_components.eco_plugs_local.hub_ft.SOURCE_PORT", 0),
        patch("custom_components.eco_plugs_local.hub_ft.SEARCH_TARGETS", ()),
        patch("custom_components.eco_plugs_local.hub_ft.FT_DISCOVERY_SECONDS", 0.05),
        patch("custom_components.eco_plugs_local.hub_ft.FT_CONFIRM_DELAY", 0),
    ):
        yield


class FakePlug(asyncio.DatagramProtocol):
    """A loopback stand-in for the plug's command port."""

    def __init__(
        self,
        reply: bool | None = True,
        wrong_state: bool = False,
        reply_from: socket.socket | None = None,
    ) -> None:
        self.reply = reply
        self.wrong_state = wrong_state
        self.reply_from = reply_from  # answer from a different address
        self.received: list[bytes] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.received.append(data)
        if not self.reply:
            return
        state = int.from_bytes(data[0x50:0x54], "little") != 0
        reply = make_reply(state != self.wrong_state)
        if self.reply_from is not None:
            self.reply_from.sendto(reply, addr)
        else:
            self.transport.sendto(reply, addr)

    @property
    def port(self) -> int:
        return self.transport.get_extra_info("sockname")[1]

    def decoded(self, i: int) -> bytes:
        buf = bytearray(self.received[i])
        P._command_xor(buf)  # the XOR is its own inverse
        return bytes(buf)


@pytest.fixture
async def fake_plug():
    loop = asyncio.get_running_loop()
    plugs: list[FakePlug] = []

    async def _make(**kw) -> FakePlug:
        _transport, proto = await loop.create_datagram_endpoint(
            lambda: FakePlug(**kw), local_addr=("127.0.0.1", 0)
        )
        plugs.append(proto)
        return proto

    yield _make
    for p in plugs:
        p.transport.close()


class FakeFtPlug(asyncio.DatagramProtocol):
    """A loopback stand-in for a v1.x plug. Like the real one, it answers twice."""

    def __init__(
        self,
        on: bool = False,
        set_mark: int = 0,
        silent: bool = False,
        stuck: bool = False,
        power: bytes | None = None,
        power_mark: int = 0,
        timers: bytes | None = None,
        random: bytes | None = None,
        dst: bool = False,
        write_mark: int = 0,
        stuck_writes: bool = False,
        clock_offset: float = 0.0,
        wrong_sequence: bool = False,
        reply_from: socket.socket | None = None,
    ) -> None:
        self.on = on
        self.set_mark = set_mark
        self.silent = silent
        self.stuck = stuck  # acknowledges a set but never changes state
        self.power = power  # reply payload for a power query; None = no answer
        self.power_mark = power_mark
        self.timers = timers  # reply payloads for the timer reads; None = no answer
        self.random = random
        self.dst = dst
        self.write_mark = (
            write_mark  # reply to configuration writes: 0 = done, 3 = refused
        )
        self.stuck_writes = stuck_writes  # acknowledges them but keeps what it had
        self.clock_offset = clock_offset  # seconds the plug's clock is off
        self.wrong_sequence = wrong_sequence
        self.reply_from = reply_from  # answer from a different address
        self.received: list[bytes] = []
        self.times: list[float] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def _send(self, data: bytes, addr) -> None:
        sender = self.reply_from or self.transport
        for _ in range(2):
            sender.sendto(data, addr)

    def datagram_received(self, data: bytes, addr) -> None:
        self.received.append(data)
        self.times.append(time.monotonic())
        if self.silent:
            return
        cmd = int.from_bytes(data[0:4], "little")
        if cmd == 0:  # a search request
            now = dt_util.now().replace(tzinfo=None)  # HA's local time, as the plug's
            clock = now + timedelta(seconds=self.clock_offset)
            self._send(
                make_search_reply(port=self.port, clock=clock, dst=self.dst), addr
            )
            return
        sequence = int.from_bytes(data[6:8], "little")
        if self.wrong_sequence:
            sequence = (sequence + 1) & 0xFFFF
        if cmd == 0x50017:
            self._send(make_response(cmd, sequence, 0, bytes([1, int(self.on)])), addr)
        elif cmd == 0x50103:
            if self.timers is not None:
                self._send(make_response(cmd, sequence, 0, self.timers), addr)
        elif cmd == 0x50021:
            if self.random is not None:
                self._send(make_response(cmd, sequence, 0, self.random), addr)
        elif cmd in (0x50015, 0x50022, 0x50100, 0x50101, 0x50102):
            payload = data[0x80:]
            if self.write_mark == 0 and not self.stuck_writes:
                if cmd == 0x50015:
                    self.dst = bool(int.from_bytes(payload[:2], "little") & 0x1000)
                elif cmd == 0x50022:
                    self.random = bytes(payload)
                elif len(payload) == 388:
                    self.timers = bytes(payload)
                else:  # what the real plug did with a single entry: wrote it over the list
                    buf = bytearray(self.timers or bytes(388))
                    buf[: len(payload)] = payload
                    self.timers = bytes(buf)
            self._send(make_response(cmd, sequence, self.write_mark), addr)
        elif cmd == 0x50032:
            if self.power is not None:
                self._send(
                    make_response(cmd, sequence, self.power_mark, self.power), addr
                )
        elif cmd == 0x50016:
            if self.set_mark == 0 and not self.stuck:
                self.on = data[0x81] == 1
            self._send(make_response(cmd, sequence, self.set_mark), addr)

    @property
    def port(self) -> int:
        return self.transport.get_extra_info("sockname")[1]

    def commands(self, cmd: int) -> list[bytes]:
        return [d for d in self.received if int.from_bytes(d[0:4], "little") == cmd]


@pytest.fixture
async def fake_ft_plug():
    loop = asyncio.get_running_loop()
    plugs: list[FakeFtPlug] = []

    async def _make(**kw) -> FakeFtPlug:
        _transport, proto = await loop.create_datagram_endpoint(
            lambda: FakeFtPlug(**kw), local_addr=("127.0.0.1", 0)
        )
        plugs.append(proto)
        return proto

    yield _make
    for p in plugs:
        p.transport.close()

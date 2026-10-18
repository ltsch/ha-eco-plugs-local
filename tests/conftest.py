"""Shared fixtures."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest

from custom_components.eco_plugs_local import protocol as P

from .beacon_factory import make_reply


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

"""Hub lifecycle, socket and reply-filtering tests."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.eco_plugs_local import hub as hub_mod
from custom_components.eco_plugs_local.hub import CommandError, async_discover, get_hub

from .beacon_factory import make_beacon


def _beacon(hass: HomeAssistant, mac: str = "020000000001", **kw) -> None:
    get_hub(hass)._on_beacon_datagram(
        make_beacon(mac=bytes.fromhex(mac), **kw), ("127.0.0.1", 10229)
    )


async def test_refcount_and_close(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    await hub.async_acquire()
    await hub.async_acquire()
    transport = hub._transport
    await hub.async_release()
    assert hub._transport is transport and not transport.is_closing()
    await hub.async_release()
    assert hub._transport is None and transport.is_closing()


async def test_concurrent_acquire_binds_once(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    real = hub_mod._udp_socket
    with patch.object(hub_mod, "_udp_socket", side_effect=real) as bind:
        await asyncio.gather(hub.async_acquire(), hub.async_acquire())
    assert bind.call_count == 1
    await hub.async_release()
    await hub.async_release()
    assert hub._transport is None


async def test_acquire_bind_failure(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    with (
        patch.object(hub_mod, "_udp_socket", side_effect=OSError("busy")),
        pytest.raises(CommandError, match="Cannot listen"),
    ):
        await hub.async_acquire()
    assert hub._users == 0


def test_command_socket_is_exclusive() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    first = hub_mod._udp_socket(port, share=False)
    try:
        with pytest.raises(OSError):
            hub_mod._udp_socket(port, share=False).close()
    finally:
        first.close()
    a, b = hub_mod._udp_socket(port, share=True), hub_mod._udp_socket(port, share=True)
    a.close()
    b.close()


async def test_reply_from_other_host_ignored(hass: HomeAssistant, fake_plug) -> None:
    spoofer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    spoofer.bind(("127.0.0.2", 0))
    try:
        plug = await fake_plug(reply_from=spoofer)
        hub = get_hub(hass)
        await hub.async_acquire()
        _beacon(hass, port=plug.port)
        with (
            patch.object(hub_mod, "REPLY_TIMEOUT", 0.05),
            pytest.raises(CommandError, match="did not acknowledge"),
        ):
            await hub.async_set_power("020000000001", True, 30)
        await hub.async_release()
    finally:
        spoofer.close()


async def test_command_bind_failure(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    await hub.async_acquire()
    _beacon(hass)
    with patch.object(hub_mod, "_udp_socket", side_effect=OSError("busy")):
        for _ in range(2):  # lock must be released after a failure
            with pytest.raises(HomeAssistantError, match="Cannot bind"):
                await hub.async_set_power("020000000001", True, 30)
    await hub.async_release()


async def test_release_closes_command_socket(hass: HomeAssistant, fake_plug) -> None:
    plug = await fake_plug()
    hub = get_hub(hass)
    await hub.async_acquire()
    _beacon(hass, port=plug.port)
    await hub.async_set_power("020000000001", True, 30)
    command = hub._command_transport
    assert command is not None
    await hub.async_release()
    assert command.is_closing() and hub._command_transport is None


async def test_stale_sighting_rejected(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    _beacon(hass)
    hub.sightings["020000000001"].monotonic -= 100
    with pytest.raises(CommandError, match="No recent beacon"):
        await hub.async_set_power("020000000001", True, 30)


async def test_sightings_are_bounded(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    for i in range(hub_mod._MAX_SIGHTINGS + 20):
        _beacon(hass, mac=f"02{i:010x}")
    assert len(hub.sightings) <= hub_mod._MAX_SIGHTINGS


async def test_discover_keeps_shared_listener(hass: HomeAssistant) -> None:
    hub = get_hub(hass)
    await hub.async_acquire()  # as if a config entry were loaded
    with patch.object(hub_mod, "DISCOVERY_SECONDS", 0.05):
        task = asyncio.create_task(async_discover(hass))
        await asyncio.sleep(0)
        _beacon(hass)
        found = await task
    assert set(found) == {"020000000001"}
    assert hub._transport is not None
    await hub.async_release()
    assert hub._transport is None


async def test_hub_command_port_not_shareable(hass: HomeAssistant, fake_plug) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("", 0))
    port = probe.getsockname()[1]
    probe.close()
    plug = await fake_plug()
    hub = get_hub(hass)
    await hub.async_acquire()
    _beacon(hass, port=plug.port)
    with patch.object(hub_mod, "COMMAND_SOURCE_PORT", port):
        await hub.async_set_power("020000000001", True, 30)
        with pytest.raises(OSError):  # another process must not get the replies
            hub_mod._udp_socket(port, share=True).close()
    await hub.async_release()

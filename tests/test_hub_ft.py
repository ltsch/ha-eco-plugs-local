"""v1.x hub: lifecycle, request matching, confirmation and discovery."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import socket
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.eco_plugs_local import hub_ft as hub_mod
from custom_components.eco_plugs_local.hub import CommandError
from custom_components.eco_plugs_local.hub_ft import async_ft_discover, get_ft_hub

from .ft_factory import (
    make_power_payload,
    make_random_payload,
    make_response,
    make_search_reply,
    make_timer_entry,
    make_timers_payload,
)

UID = "020000000002"


def _see(hass: HomeAssistant, port: int = 80, host: str = "127.0.0.1", **kw) -> None:
    get_ft_hub(hass)._on_datagram(make_search_reply(port=port, **kw), (host, 25))


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def test_refcount_and_close(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    await hub.async_acquire()
    transport = hub._transport
    await hub.async_release()
    assert hub._transport is transport and not transport.is_closing()
    await hub.async_release()
    assert hub._transport is None and transport.is_closing()


async def test_bind_failure(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    with (
        patch.object(hub_mod, "_udp_socket", side_effect=OSError("busy")),
        pytest.raises(CommandError, match="Cannot bind"),
    ):
        await hub.async_acquire()
    assert hub._users == 0


async def test_source_port_is_exclusive(hass: HomeAssistant) -> None:
    """The plug sends replies to the sender's port, so nobody else may share it."""
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    port = hub._transport.get_extra_info("sockname")[1]
    with pytest.raises(OSError):
        hub_mod._udp_socket(port, share=False).close()
    await hub.async_release()


async def test_send_search_needs_the_socket(hass: HomeAssistant) -> None:
    with pytest.raises(CommandError, match="not open"):
        get_ft_hub(hass).send_search()


async def test_get_state_sends_the_plugs_auth_block(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(on=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    assert await hub.async_get_state(UID) is True
    (sent,) = plug.commands(0x50017)
    assert sent[0x0A:0x70] == make_search_reply()[4 : 4 + 0x66]
    plug.on = False
    assert await hub.async_get_state(UID) is False
    assert hub._pending == {}  # the duplicate reply did not leak anywhere
    await hub.async_release()


@pytest.mark.parametrize("on", [True, False])
async def test_set_power_waits_for_the_plug(
    hass: HomeAssistant, fake_ft_plug, on: bool
) -> None:
    plug = await fake_ft_plug(on=not on)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    await hub.async_set_power(UID, on)
    assert plug.on is on
    (sent,) = plug.commands(0x50016)
    assert sent[0x80:] == bytes([1, int(on)])
    await hub.async_release()


async def test_set_refused(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=False, set_mark=3)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="refused"):
        await hub.async_set_power(UID, True)
    assert plug.on is False
    await hub.async_release()


async def test_set_rejected_with_other_code(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(set_mark=7)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="code 7"):
        await hub.async_set_power(UID, True)
    await hub.async_release()


async def test_set_acknowledged_but_state_unchanged(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(on=False, stuck=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="reports off"):
        await hub.async_set_power(UID, True)
    await hub.async_release()


async def test_silent_plug(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(silent=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with (
        patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port),
        pytest.raises(CommandError, match="did not answer"),
    ):
        await hub.async_set_power(UID, True)
    assert hub._pending == {}
    await hub.async_release()


async def test_unknown_plug(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    with pytest.raises(CommandError, match="did not answer"):
        await hub.async_get_state(UID)
    await hub.async_release()


async def test_reply_with_wrong_sequence_ignored(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(wrong_sequence=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with (
        patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port),
        pytest.raises(CommandError, match="did not answer"),
    ):
        await hub.async_get_state(UID)
    await hub.async_release()


async def test_reply_from_other_host_ignored(hass: HomeAssistant, fake_ft_plug) -> None:
    spoofer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        spoofer.bind(("127.0.0.2", 0))
    except OSError:
        spoofer.close()
        pytest.skip("127.0.0.2 is not available on this host")
    try:
        plug = await fake_ft_plug(reply_from=spoofer)
        hub = get_ft_hub(hass)
        await hub.async_acquire()
        _see(hass, plug.port)
        with (
            patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port),
            pytest.raises(CommandError, match="did not answer"),
        ):
            await hub.async_get_state(UID)
        await hub.async_release()
    finally:
        spoofer.close()


async def test_unexpected_status_reply(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass)
    with (
        patch.object(
            hub,
            "_async_exchange",
            return_value=hub_mod.parse_response(
                make_response(0x50017, 1, 0, b"\x01\x05")
            ),
        ),
        pytest.raises(CommandError, match="unexpected"),
    ):
        await hub.async_get_state(UID)
    await hub.async_release()


async def test_search_finds_a_plug_that_was_never_seen(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(on=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    with patch.object(hub_mod, "SEARCH_TARGETS", (("127.0.0.1", plug.port),)):
        assert await hub.async_get_state(UID) is True
    assert hub.sighting(UID).host == "127.0.0.1"
    await hub.async_release()


async def test_discover(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug()
    hub = get_ft_hub(hass)
    with patch.object(hub_mod, "SEARCH_TARGETS", (("127.0.0.1", plug.port),)):
        found = await async_ft_discover(hass)
    assert set(found) == {UID}
    assert found[UID].device.version == "1.6.0"
    assert hub._transport is None  # nobody else held the socket


async def test_discover_keeps_a_shared_socket(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug()
    hub = get_ft_hub(hass)
    await hub.async_acquire()  # as if a config entry were loaded
    with patch.object(hub_mod, "SEARCH_TARGETS", (("127.0.0.1", plug.port),)):
        await async_ft_discover(hass)
    assert hub._transport is not None
    await hub.async_release()
    assert hub._transport is None


async def test_junk_datagrams_ignored(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    for junk in (b"", b"x" * 10, b"\x00" * 127, b"\xff" * 408, b"\x00" * 408):
        hub._on_datagram(junk, ("127.0.0.1", 25))
    assert hub.sightings == {}


async def test_sightings_are_bounded(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    for i in range(hub_mod._MAX_SIGHTINGS + 20):
        _see(hass, mac=f"02:00:00:00:{i >> 8:02x}:{i & 0xFF:02x}")
    assert len(hub.sightings) <= hub_mod._MAX_SIGHTINGS


async def test_watched_plugs_are_never_pruned(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    _see(hass)
    unwatch = hub.watch(UID)
    hub.sightings[UID].monotonic -= 10_000
    for i in range(hub_mod._MAX_SIGHTINGS + 20):
        _see(hass, mac=f"02:00:00:00:{i >> 8:02x}:{i & 0xFF:02x}")
    assert UID in hub.sightings
    unwatch()
    assert UID not in hub._watched


async def test_requests_are_serialized(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    results = await asyncio.gather(*(hub.async_get_state(UID) for _ in range(5)))
    assert results == [True] * 5
    await hub.async_release()


async def test_get_power(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(power=make_power_payload())
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    reading = await hub.async_get_power(UID, date(2026, 11, 16))
    assert (reading.volts, reading.amps, reading.watts, reading.kwh) == pytest.approx(
        (100.0, 0.5, 50.0, 3.0)
    )
    (sent,) = plug.commands(0x50032)
    assert sent[0x80 + 0x08 : 0x80 + 0x0A] == bytes([11, 16])
    await hub.async_release()


async def test_power_date_rejected(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(power=make_power_payload(), power_mark=0x33)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="date range"):
        await hub.async_get_power(UID, date(2020, 1, 1))
    await hub.async_release()


async def test_power_garbage_reply(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(power=b"\x00" * 10)
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="unexpected power"):
        await hub.async_get_power(UID, date(2026, 11, 16))
    await hub.async_release()


async def test_clock_difference_and_subscribers(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    seen = []
    unsubscribe = hub.subscribe(UID, seen.append)
    _see(hass, clock=dt_util.now().replace(tzinfo=None) + timedelta(seconds=30))
    assert hub.sighting(UID).clock_difference == pytest.approx(30, abs=2)
    assert len(seen) == 1
    unsubscribe()
    _see(hass)
    assert len(seen) == 1
    assert hub.sighting(UID).clock_difference is None  # no clock record this time


async def test_get_timers(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(
        timers=make_timers_payload(make_timer_entry()),
        random=make_random_payload(vacation=False, random=True),
    )
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    settings = await hub.async_get_timers(UID)
    assert len(settings.timers) == 1
    assert settings.timers[0].start_seconds == 28800
    assert settings.random is True and settings.vacation is False
    await hub.async_release()


async def test_get_timers_garbage_reply(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(timers=b"\x00" * 10, random=make_random_payload())
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    with pytest.raises(CommandError, match="unexpected timer"):
        await hub.async_get_timers(UID)
    await hub.async_release()


TIMERS = make_timers_payload(make_timer_entry())


async def _hub_with(hass: HomeAssistant, plug):
    hub = get_ft_hub(hass)
    await hub.async_acquire()
    _see(hass, plug.port)
    return hub


async def test_set_dst_is_confirmed_by_reading_it_back(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(dst=False)
    hub = await _hub_with(hass, plug)
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        await hub.async_set_dst(UID, True)
        assert plug.dst is True
        assert plug.commands(0x50015)[0][0x80:] == bytes.fromhex(
            "df17041200e1000000000000"
        )
        assert hub.sighting(UID).device.clock.dst is True
        await hub.async_set_dst(UID, False)
        assert plug.dst is False
    await hub.async_release()


async def test_set_dst_refused_says_why(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(dst=False, write_mark=3)
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="vendor app"):
        await hub.async_set_dst(UID, True)
    assert plug.dst is False
    await hub.async_release()


async def test_set_dst_acknowledged_but_unchanged(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(dst=False, stuck_writes=True)
    hub = await _hub_with(hass, plug)
    with (
        patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port),
        pytest.raises(CommandError, match="daylight saving off"),
    ):
        await hub.async_set_dst(UID, True)
    await hub.async_release()


async def test_set_random_changes_only_the_flag(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    before = make_random_payload(vacation=True, random=True)
    plug = await fake_ft_plug(timers=TIMERS, random=before)
    hub = await _hub_with(hass, plug)
    await hub.async_set_random(UID, False)
    assert plug.random[1] == 0
    assert plug.random[:1] + plug.random[2:] == before[:1] + before[2:]
    assert hub.timer_settings(UID).random is False
    await hub.async_set_random(UID, True)
    assert plug.random == before
    await hub.async_release()


async def test_set_random_refused_and_stuck(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(timers=TIMERS, random=make_random_payload(), write_mark=3)
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="refused"):
        await hub.async_set_random(UID, False)
    plug.write_mark, plug.stuck_writes = 0, True
    with pytest.raises(CommandError, match="random timers on"):
        await hub.async_set_random(UID, False)
    await hub.async_release()


async def test_timer_changes_send_the_whole_list(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(timers=TIMERS, random=make_random_payload())
    hub = await _hub_with(hass, plug)

    settings = await hub.async_add_timer(
        UID, ["sunday"], 10800, 11100, date(2026, 11, 16)
    )
    assert [t.timer_id for t in settings.timers] == [0, 1]
    assert settings.timers[1].days == ("sunday",)
    (sent,) = plug.commands(0x50100)
    assert len(sent[0x80:]) == 388 and sent[0x80:] == plug.timers

    settings = await hub.async_update_timer(UID, 1, start=3600)
    assert settings.timers[1].start_seconds == 3600
    assert len(plug.commands(0x50101)[0][0x80:]) == 388

    settings = await hub.async_delete_timer(UID, 1)
    assert [t.timer_id for t in settings.timers] == [0]
    assert plug.timers == TIMERS  # exactly what it started with
    assert len(plug.commands(0x50102)[0][0x80:]) == 388
    assert hub.timer_settings(UID) == settings
    await hub.async_release()


async def test_a_damaged_list_is_left_alone(hass: HomeAssistant, fake_ft_plug) -> None:
    damaged = bytearray(388)
    damaged[:32] = make_timer_entry()
    plug = await fake_ft_plug(timers=bytes(damaged), random=make_random_payload())
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="damaged"):
        await hub.async_add_timer(UID, ["monday"], 0, 60, date(2026, 11, 16))
    assert not plug.commands(0x50100) and not plug.commands(0x50101)
    assert plug.timers == bytes(damaged)
    await hub.async_release()


async def test_bad_timer_input_sends_nothing(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(timers=TIMERS, random=make_random_payload())
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="no timer 7"):
        await hub.async_delete_timer(UID, 7)
    with pytest.raises(CommandError, match="Days must be"):
        await hub.async_add_timer(UID, ["funday"], 0, 60, date(2026, 11, 16))
    assert not any(plug.commands(c) for c in (0x50100, 0x50101, 0x50102))
    await hub.async_release()


async def test_a_refused_timer_change_changes_nothing(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(timers=TIMERS, random=make_random_payload(), write_mark=3)
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="refused"):
        await hub.async_delete_timer(UID, 0)
    assert plug.timers == TIMERS
    await hub.async_release()


async def test_a_timer_change_the_plug_ignores_sends_the_old_list_back(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(
        timers=TIMERS, random=make_random_payload(), stuck_writes=True
    )
    hub = await _hub_with(hass, plug)
    with pytest.raises(CommandError, match="previous list was sent back"):
        await hub.async_delete_timer(UID, 0)
    restore = plug.commands(0x50101)
    assert restore and restore[-1][0x80:] == TIMERS
    await hub.async_release()

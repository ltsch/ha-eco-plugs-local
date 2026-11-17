"""The burst scheduler: a plug's polls go out together, once per cycle."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.eco_plugs_local import hub_ft as hub_mod
from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub_ft import get_ft_hub

from .ft_factory import (
    make_power_payload,
    make_random_payload,
    make_search_reply,
    make_timer_entry,
    make_timers_payload,
)

UID = "020000000002"


async def _cycle(hass: HomeAssistant, n: int = 1) -> None:
    """Fire the n-th interval tick (cycles are 10 s apart by default)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11 * n))
    await hass.async_block_till_done()


async def test_jobs_run_together_in_registration_order(hass: HomeAssistant) -> None:
    poller = get_ft_hub(hass).poller(UID, 10)
    order: list[str] = []

    def job(name: str):
        async def run() -> None:
            order.append(name)

        return run

    poller.register("a", 1, job("a"))
    poller.register("b", 1, job("b"))
    await _cycle(hass)
    assert order == ["a", "b"]


async def test_a_job_can_run_every_nth_cycle(hass: HomeAssistant) -> None:
    poller = get_ft_hub(hass).poller(UID, 10)
    ran: list[int] = []
    cycle = 0

    async def slow() -> None:
        ran.append(cycle)

    poller.register("slow", 3, slow)
    for cycle in range(1, 8):
        await _cycle(hass, cycle)
    assert ran == [1, 4, 7]  # cycles 0, 3 and 6


async def test_a_failing_job_does_not_stop_the_others(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    poller = get_ft_hub(hass).poller(UID, 10)
    ran: list[str] = []

    async def bad() -> None:
        raise RuntimeError("boom")

    async def good() -> None:
        ran.append("good")

    poller.register("bad", 1, bad)
    poller.register("good", 1, good)
    with caplog.at_level(logging.ERROR):
        await _cycle(hass)
    assert ran == ["good"]
    assert "Poll job bad failed" in caplog.text


async def test_cycles_never_overlap(hass: HomeAssistant) -> None:
    poller = get_ft_hub(hass).poller(UID, 10)
    started = 0
    release = asyncio.Event()

    async def slow() -> None:
        nonlocal started
        started += 1
        await release.wait()

    poller.register("slow", 1, slow)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await asyncio.sleep(0.05)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await asyncio.sleep(0.05)
    assert started == 1  # the second tick found the first cycle still running
    release.set()
    await hass.async_block_till_done()


async def test_the_poller_goes_away_with_its_last_job(hass: HomeAssistant) -> None:
    hub = get_ft_hub(hass)
    poller = hub.poller(UID, 10)
    assert hub.poller(UID, 10) is poller  # shared while something is registered
    ran = 0

    async def job() -> None:
        nonlocal ran
        ran += 1

    first = poller.register("a", 1, job)
    second = poller.register("b", 1, job)
    first()
    await _cycle(hass)
    assert ran == 1  # "b" still runs
    second()
    assert UID not in hub._pollers
    await _cycle(hass, 2)
    assert ran == 1  # the timer was cancelled
    assert hub.poller(UID, 10) is not poller


async def test_unregistering_a_replaced_job_keeps_the_new_one(
    hass: HomeAssistant,
) -> None:
    poller = get_ft_hub(hass).poller(UID, 10)
    ran: list[str] = []

    async def old() -> None:
        ran.append("old")

    async def new() -> None:
        ran.append("new")

    stop_old = poller.register("a", 1, old)
    poller.register("a", 1, new)
    stop_old()
    await _cycle(hass)
    assert ran == ["new"]


async def test_one_cycle_is_one_burst_and_the_plug_is_quiet_between(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(
        on=True,
        power=make_power_payload(),
        timers=make_timers_payload(make_timer_entry()),
        random=make_random_payload(),
    )
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        get_ft_hub(hass)._on_datagram(
            make_search_reply(port=plug.port), ("127.0.0.1", 25)
        )
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test FT Plug",
            unique_id=UID,
            data={
                "mac": "02:00:00:00:00:02",
                "device_id": "ECO-00000002",
                "family": "ft",
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.sleep(0.5)  # startup polls settle

        def commands_since(n: int) -> list[int]:
            return [int.from_bytes(d[:4], "little") for d in plug.received[n:]]

        before = len(plug.received)
        await _cycle(hass, 1)  # cycle 0: everything runs
        await asyncio.sleep(0.4)
        first = commands_since(before)
        assert {0x50017, 0x50032, 0x50103, 0x50021, 0} <= set(first)
        span = plug.times[len(plug.received) - 1] - plug.times[before]
        assert span < 0.5  # all of it, back to back

        settled = len(plug.received)
        await asyncio.sleep(0.5)
        assert len(plug.received) == settled  # nothing between cycles

        before = len(plug.received)
        await _cycle(hass, 2)  # cycle 1: only the fast jobs
        await asyncio.sleep(0.4)
        assert set(commands_since(before)) == {0x50017, 0x50032}


async def test_stale_timeout_is_never_shorter_than_three_cycles(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(on=True)
    get_ft_hub(hass)._on_datagram(make_search_reply(port=plug.port), ("127.0.0.1", 25))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test FT Plug",
        unique_id=UID,
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
        options={"poll_interval": 30, "stale_timeout": 10},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    switch = hass.data["entity_components"]["switch"].get_entity("switch.test_ft_plug")
    assert switch._stale == 90  # not 10: it would flap between two polls

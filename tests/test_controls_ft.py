"""v1.x controls: the DST and random-timers switches, and the timer services."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
import voluptuous as vol

from custom_components.eco_plugs_local import hub_ft as hub_mod, protocol_ft as P
from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub_ft import get_ft_hub

from .ft_factory import (
    make_random_payload,
    make_search_reply,
    make_timer_entry,
    make_timers_payload,
)

UID = "020000000002"
DST = "switch.test_ft_plug_daylight_saving_time"
RANDOM = "switch.test_ft_plug_random_timers"
TIMERS = "sensor.test_ft_plug_timers"
START = make_timers_payload(make_timer_entry())


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def _setup(hass: HomeAssistant, plug) -> MockConfigEntry:
    get_ft_hub(hass)._on_datagram(
        make_search_reply(
            port=plug.port, dst=plug.dst, clock=dt_util.now().replace(tzinfo=None)
        ),
        ("127.0.0.1", 25),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test FT Plug",
        unique_id=UID,
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _wait_for(hass: HomeAssistant, entity: str, state: str) -> None:
    for _ in range(100):
        if hass.states.get(entity).state == state:
            return
        await asyncio.sleep(0.02)
    assert hass.states.get(entity).state == state


def _plug_kwargs(**kw):
    return {"timers": START, "random": make_random_payload(), **kw}


async def test_dst_switch_sets_and_reads_back(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(dst=False)
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        await _setup(hass, plug)
        assert hass.states.get(DST).state == STATE_OFF
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": DST}, blocking=True
        )
        assert plug.dst is True
        assert hass.states.get(DST).state == STATE_ON
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": DST}, blocking=True
        )
        assert plug.dst is False
        assert hass.states.get(DST).state == STATE_OFF


async def test_dst_switch_refused_fails_visibly(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(dst=False, write_mark=3)
    await _setup(hass, plug)
    with pytest.raises(HomeAssistantError, match="refused"):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": DST}, blocking=True
        )
    assert hass.states.get(DST).state == STATE_OFF


async def test_random_switch_reflects_and_sets_the_flag(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(**_plug_kwargs(random=make_random_payload(random=True)))
    await _setup(hass, plug)
    assert hass.states.get(RANDOM).state == STATE_ON
    assert er.async_get(hass).async_get(RANDOM).entity_category == "config"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": RANDOM}, blocking=True
    )
    assert plug.random[1] == 0
    await _wait_for(hass, RANDOM, STATE_OFF)
    assert (
        hass.states.get(TIMERS).attributes["random_timers"] is False
    )  # one read, both agree
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": RANDOM}, blocking=True
    )
    assert plug.random[1] == 1


async def test_random_switch_refused_fails_visibly(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(**_plug_kwargs(write_mark=3))
    await _setup(hass, plug)
    with pytest.raises(HomeAssistantError, match="refused"):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": RANDOM}, blocking=True
        )
    assert hass.states.get(RANDOM).state == STATE_ON


async def test_no_random_switch_when_the_plug_does_not_answer_the_timer_reads(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(on=True))
    assert hass.states.get(RANDOM) is None
    assert hass.states.get(DST) is not None


async def test_random_switch_goes_unavailable_without_reads(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(**_plug_kwargs()))
    with patch(
        "custom_components.eco_plugs_local.entity_ft.monotonic",
        side_effect=lambda: __import__("time").monotonic() + 400,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()
        assert hass.states.get(RANDOM).state == STATE_UNAVAILABLE


async def _timer_service(hass: HomeAssistant, name: str, **data) -> None:
    await hass.services.async_call(
        DOMAIN, name, {"entity_id": TIMERS, **data}, blocking=True
    )


async def test_timer_services_add_update_and_delete(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(**_plug_kwargs())
    await _setup(hass, plug)

    await _timer_service(
        hass, "add_timer", days=["saturday", "sunday"], start="09:15:00", end="21:00:30"
    )
    timers = hass.states.get(TIMERS).attributes["timers"]
    assert hass.states.get(TIMERS).state == (
        "every day: on 08:00, off 20:30; weekends: on 09:15, off 21:00:30"
    )
    assert hass.states.get(TIMERS).attributes["count"] == 2
    assert timers[1] == {
        "id": 1,
        "days": ["saturday", "sunday"],
        "start": "09:15:00",
        "start_state": "on",
        "end": "21:00:30",
        "end_state": "off",
    }
    assert len(P.timer_list_entries(plug.timers)) == 2

    await _timer_service(
        hass, "update_timer", timer_id=1, start="10:00:00", days=["monday"]
    )
    timers = hass.states.get(TIMERS).attributes["timers"]
    assert (timers[1]["start"], timers[1]["end"], timers[1]["days"]) == (
        "10:00:00",
        "21:00:30",
        ["monday"],
    )
    assert timers[0]["id"] == 0  # the other timer wasn't touched

    await _timer_service(hass, "delete_timer", timer_id=1)
    assert hass.states.get(TIMERS).state == "every day: on 08:00, off 20:30"
    assert hass.states.get(TIMERS).attributes["count"] == 1
    assert plug.timers == START


async def test_timer_service_errors_are_visible(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(**_plug_kwargs())
    await _setup(hass, plug)
    with pytest.raises(HomeAssistantError, match="no timer 9"):
        await _timer_service(hass, "delete_timer", timer_id=9)
    assert plug.timers == START
    plug.write_mark = 3
    with pytest.raises(HomeAssistantError, match="refused"):
        await _timer_service(
            hass, "add_timer", days=["monday"], start="08:00", end="09:00"
        )
    assert plug.timers == START


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("add_timer", {"days": ["funday"], "start": "08:00", "end": "09:00"}),
        ("add_timer", {"days": [], "start": "08:00", "end": "09:00"}),
        ("add_timer", {"days": ["monday"], "start": "8 o'clock", "end": "09:00"}),
        ("add_timer", {"days": ["monday"], "start": "08:00"}),
        ("update_timer", {"timer_id": 0}),
        ("update_timer", {"timer_id": -1, "start": "08:00"}),
        ("delete_timer", {}),
    ],
)
async def test_timer_service_input_is_validated(
    hass: HomeAssistant, fake_ft_plug, service: str, data: dict
) -> None:
    plug = await fake_ft_plug(**_plug_kwargs())
    await _setup(hass, plug)
    with pytest.raises(vol.Invalid):
        await _timer_service(hass, service, **data)
    assert plug.timers == START

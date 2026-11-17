"""v1.x meter sensors, driven by a loopback fake plug."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE
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
PREFIX = "sensor.test_ft_plug_"
ZEROS = make_power_payload(
    voltage=0,
    current=0,
    power=0,
    total=0,
    energy_offset=0,
    current_offset=0,
    power_offset=0,
    voltage_offset=0,
)


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def _setup(hass: HomeAssistant, plug) -> MockConfigEntry:
    get_ft_hub(hass)._on_datagram(make_search_reply(port=plug.port), ("127.0.0.1", 25))
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


def _value(hass: HomeAssistant, name: str) -> float:
    return float(hass.states.get(PREFIX + name).state)


async def _wait_for(hass: HomeAssistant, name: str, state: str) -> None:
    for _ in range(100):
        if hass.states.get(PREFIX + name).state == state:
            return
        await asyncio.sleep(0.02)
    assert hass.states.get(PREFIX + name).state == state


async def test_sensors_report_the_meter(hass: HomeAssistant, fake_ft_plug) -> None:
    await _setup(hass, await fake_ft_plug(on=True, power=make_power_payload()))
    assert _value(hass, "voltage") == pytest.approx(100.0)
    assert _value(hass, "current") == pytest.approx(0.5)
    assert _value(hass, "power") == pytest.approx(50.0)
    assert _value(hass, "energy_today") == pytest.approx(3.0)
    energy = hass.states.get(PREFIX + "energy_today")
    assert energy.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert energy.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert energy.attributes["unit_of_measurement"] == "kWh"
    power = hass.states.get(PREFIX + "power")
    assert power.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert power.attributes["unit_of_measurement"] == "W"


async def test_asks_for_today_in_the_local_time_zone(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(power=make_power_payload())
    await _setup(hass, plug)
    today = dt_util.now().date()
    payload = plug.commands(0x50032)[0][0x80:]
    assert payload[0x08:0x0A] == bytes([today.month, today.day])
    assert payload[0x10:0x12] == bytes([today.month, today.day])


async def test_sensors_share_the_switchs_device(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    await _setup(hass, await fake_ft_plug(power=make_power_payload()))
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UID)})
    registry = er.async_get(hass)
    assert registry.async_get(PREFIX + "voltage").device_id == device.id
    assert registry.async_get("switch.test_ft_plug").device_id == device.id
    assert registry.async_get(PREFIX + "voltage").unique_id == f"{UID}_voltage"


async def test_no_sensors_on_a_plug_without_a_meter(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(power=ZEROS))
    assert hass.states.async_entity_ids("sensor") == [PREFIX + "clock_difference"]
    assert hass.states.get("switch.test_ft_plug") is not None


async def test_no_sensors_when_only_the_power_command_is_ignored(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(on=True, power=None))
    assert hass.states.async_entity_ids("sensor") == [PREFIX + "clock_difference"]


async def test_offline_at_startup_keeps_the_sensors_and_recovers(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(silent=True, power=make_power_payload())
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        await _setup(hass, plug)
        await _wait_for(hass, "power", STATE_UNAVAILABLE)
        plug.silent = False
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        for _ in range(100):
            if hass.states.get(PREFIX + "power").state != STATE_UNAVAILABLE:
                break
            await asyncio.sleep(0.02)
    assert _value(hass, "power") == pytest.approx(50.0)


async def test_sensors_go_unavailable_when_the_plug_stops_answering(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(power=make_power_payload())
    await _setup(hass, plug)
    plug.silent = True
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()
        await _wait_for(hass, "power", STATE_UNAVAILABLE)


async def test_beacon_plugs_get_no_sensors(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Beacon Plug",
        unique_id="020000000001",
        data={"mac": "02:00:00:00:00:01", "device_id": "ECO-00000001"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids("sensor") == []


async def test_unload_removes_the_sensors(hass: HomeAssistant, fake_ft_plug) -> None:
    entry = await _setup(hass, await fake_ft_plug(power=make_power_payload()))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PREFIX + "voltage").state == STATE_UNAVAILABLE
    assert get_ft_hub(hass)._transport is None


TIMERS = PREFIX + "timers"


async def test_timers_sensor_lists_the_plugs_timers(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(
        timers=make_timers_payload(make_timer_entry(mask=0x60)),
        random=make_random_payload(random=True),
    )
    await _setup(hass, plug)
    state = hass.states.get(TIMERS)
    assert state.state == "Mon, Sun: on 08:00, off 20:30"
    assert state.attributes["count"] == 1
    assert state.attributes["timers"] == [
        {
            "id": 0,
            "days": ["monday", "sunday"],
            "start": "08:00:00",
            "start_state": "on",
            "end": "20:30:00",
            "end_state": "off",
        }
    ]
    assert state.attributes["random_timers"] is True
    assert state.attributes["vacation_mode"] is False


async def test_timers_sensor_follows_changes_made_in_the_app(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(
        timers=make_timers_payload(make_timer_entry()),
        random=make_random_payload(),
    )
    await _setup(hass, plug)
    plug.timers = make_timers_payload(make_timer_entry(), make_timer_entry(timer_id=1))
    plug.random = make_random_payload(random=False, vacation=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await _wait_for(
        hass, "timers", "every day: on 08:00, off 20:30; every day: on 08:00, off 20:30"
    )
    assert hass.states.get(TIMERS).attributes["count"] == 2
    assert hass.states.get(TIMERS).attributes["vacation_mode"] is True


async def test_no_timers_sensor_when_the_plug_does_not_answer_them(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(on=True))
    assert hass.states.get(TIMERS) is None


async def test_timers_sensor_exists_for_a_plug_that_is_offline_at_startup(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(
        silent=True,
        timers=make_timers_payload(make_timer_entry()),
        random=make_random_payload(),
    )
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        await _setup(hass, plug)
        await _wait_for(hass, "timers", STATE_UNAVAILABLE)
        plug.silent = False
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await _wait_for(hass, "timers", "every day: on 08:00, off 20:30")

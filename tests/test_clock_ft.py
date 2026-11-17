"""v1.x clock entities: the DST switch and the clock-difference sensor."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import itertools
from unittest.mock import patch

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.eco_plugs_local import hub_ft as hub_mod
from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub_ft import get_ft_hub

from .ft_factory import make_search_reply

UID = "020000000002"
DST = "switch.test_ft_plug_daylight_saving_time"
CLOCK = "sensor.test_ft_plug_clock_difference"


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def _setup(hass: HomeAssistant, plug) -> MockConfigEntry:
    get_ft_hub(hass)._on_datagram(
        make_search_reply(
            port=plug.port,
            clock=dt_util.now().replace(tzinfo=None)
            + timedelta(seconds=plug.clock_offset),
            dst=plug.dst,
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


async def test_dst_switch_shows_the_plugs_flag(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(dst=True))
    assert hass.states.get(DST).state == STATE_ON


async def test_clock_entities_have_the_right_categories(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug())
    registry = er.async_get(hass)
    assert registry.async_get(DST).entity_category == "config"
    assert registry.async_get(CLOCK).entity_category == "diagnostic"


async def test_clock_sensor_reports_the_difference(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug(clock_offset=-5))
    assert float(hass.states.get(CLOCK).state) == pytest.approx(-5, abs=2)
    assert hass.states.get(CLOCK).attributes["unit_of_measurement"] == "s"


async def test_clock_entities_go_unavailable_without_replies(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    await _setup(hass, await fake_ft_plug())
    with patch(
        "custom_components.eco_plugs_local.entity_ft.monotonic",
        side_effect=lambda: __import__("time").monotonic() + 400,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()
        assert hass.states.get(DST).state == STATE_UNAVAILABLE
        assert hass.states.get(CLOCK).state == STATE_UNAVAILABLE


async def test_the_clock_is_read_every_sixth_cycle(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    """A 10 s cycle with a once-a-minute clock read: cycles 0, 6, 12 ..."""
    plug = await fake_ft_plug(on=True)
    with patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port):
        await _setup(hass, plug)
        await asyncio.sleep(0.2)
        searches = [len(plug.commands(0))]
        for cycle in range(1, 8):
            async_fire_time_changed(
                hass, dt_util.utcnow() + timedelta(seconds=11 * cycle)
            )
            await asyncio.sleep(0.25)
            searches.append(len(plug.commands(0)))
    gains = [b - a for a, b in itertools.pairwise(searches)]
    assert gains == [1, 0, 0, 0, 0, 0, 1]

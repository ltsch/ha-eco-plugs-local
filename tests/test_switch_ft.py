"""v1.x switch entity tests, driven by a loopback fake plug."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from time import monotonic
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
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

ENTITY = "switch.test_ft_plug"
UID = "020000000002"


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def _setup(hass: HomeAssistant, plug, **options) -> MockConfigEntry:
    # The plug's address is learned from its search reply, as in real use.
    get_ft_hub(hass)._on_datagram(make_search_reply(port=plug.port), ("127.0.0.1", 25))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test FT Plug",
        unique_id=UID,
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _wait_for(hass: HomeAssistant, state: str) -> None:
    for _ in range(100):
        if hass.states.get(ENTITY).state == state:
            return
        await asyncio.sleep(0.02)
    assert hass.states.get(ENTITY).state == state


async def _poll(hass: HomeAssistant, seconds: int = 11) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_state_comes_from_the_plug(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True)
    await _setup(hass, plug)
    await _wait_for(hass, STATE_ON)
    assert hass.states.get(ENTITY).attributes["ip_address"] == "127.0.0.1"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UID)})
    assert device.sw_version == "1.6.0"


async def test_follows_changes_made_elsewhere(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await fake_ft_plug(on=True)
    await _setup(hass, plug)
    await _wait_for(hass, STATE_ON)
    plug.on = False  # someone pressed the button
    await _poll(hass)
    await _wait_for(hass, STATE_OFF)


async def test_turn_off_and_on_are_confirmed(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True)
    await _setup(hass, plug)
    await _wait_for(hass, STATE_ON)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY}, blocking=True
    )
    assert plug.on is False
    assert hass.states.get(ENTITY).state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
    )
    assert plug.on is True
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_refused_command_fails_visibly(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True, set_mark=3)
    await _setup(hass, plug)
    await _wait_for(hass, STATE_ON)
    with pytest.raises(HomeAssistantError, match="refused"):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_unavailable_until_the_plug_answers(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    """No restored/guessed state: the original motivating bug."""
    plug = await fake_ft_plug(silent=True)
    await _setup(hass, plug)
    await asyncio.sleep(0.3)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    plug.silent = False
    plug.on = True
    await _poll(hass)
    await _wait_for(hass, STATE_ON)


async def test_goes_unavailable_and_relearns(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True)
    await _setup(hass, plug, stale_timeout=30)
    await _wait_for(hass, STATE_ON)
    plug.silent = True
    with (
        patch.object(hub_mod, "UNICAST_SEARCH_PORT", plug.port),
        patch(
            "custom_components.eco_plugs_local.switch_ft.monotonic",
            side_effect=lambda: monotonic() + 31,
        ),
    ):
        await _poll(hass)
        await _wait_for(hass, STATE_UNAVAILABLE)
        await asyncio.sleep(0.3)  # let this cycle finish: cycles never overlap
    # After the gap the plug's report is taken as-is, even if it changed.
    plug.silent = False
    plug.on = False
    await _poll(hass, 22)
    await _wait_for(hass, STATE_OFF)


async def test_unload_releases_the_socket(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await fake_ft_plug(on=True)
    entry = await _setup(hass, plug)
    hub = get_ft_hub(hass)
    assert hub._transport is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hub._transport is None
    assert UID not in hub._watched

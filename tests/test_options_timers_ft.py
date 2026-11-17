"""The Configure dialog of a v1.x plug: a menu for managing its timers."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
START = make_timers_payload(make_timer_entry())


@pytest.fixture(autouse=True)
def short_timeouts():
    with patch.object(hub_mod, "REPLY_TIMEOUT", 0.05):
        yield


async def _setup(hass: HomeAssistant, plug, *, load: bool = True) -> MockConfigEntry:
    get_ft_hub(hass)._on_datagram(
        make_search_reply(port=plug.port, clock=dt_util.now().replace(tzinfo=None)),
        ("127.0.0.1", 25),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test FT Plug",
        unique_id=UID,
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
    )
    entry.add_to_hass(hass)
    if load:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _plug(fake_ft_plug, timers: bytes = START):
    return await fake_ft_plug(timers=timers, random=make_random_payload())


async def _menu(hass: HomeAssistant, entry: MockConfigEntry, option: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def test_a_running_plug_shows_a_menu(hass: HomeAssistant, fake_ft_plug) -> None:
    entry = await _setup(hass, await _plug(fake_ft_plug))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "settings",
        "add_timer",
        "edit_timer",
        "delete_timer",
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 15, "stale_timeout": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"poll_interval": 15, "stale_timeout": 60}


async def test_a_plug_without_timers_goes_straight_to_the_settings(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    entry = await _setup(hass, await fake_ft_plug(on=True))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"


async def test_add_a_timer(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    result = await _menu(hass, entry, "add_timer")
    assert result["step_id"] == "add_timer"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"days": ["saturday", "sunday"], "start": "09:15:00", "end": "21:00:30"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    timers = P.timer_list_entries(plug.timers)
    assert len(timers) == 2
    state = hass.states.get("sensor.test_ft_plug_timers")
    assert state.state.endswith("weekends: on 09:15, off 21:00:30")
    assert entry.options == {}


async def test_add_without_days_asks_again(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    result = await _menu(hass, entry, "add_timer")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"days": [], "start": "09:00:00", "end": "10:00:00"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_days"}
    assert plug.timers == START


async def test_a_refused_add_shows_the_reason_and_keeps_the_form(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    result = await _menu(hass, entry, "add_timer")
    plug.write_mark = 3
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"days": ["monday"], "start": "09:00:00", "end": "10:00:00"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "write_failed"}
    assert "refused" in result["description_placeholders"]["error"]
    assert plug.timers == START


async def test_edit_a_timer(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    result = await _menu(hass, entry, "edit_timer")
    assert result["step_id"] == "edit_timer"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timer": "0"}
    )
    assert result["step_id"] == "edit_timer_details"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"days": ["monday"], "start": "07:00:00", "end": "20:30:00"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (timer,) = P.timer_list_entries(plug.timers)
    assert timer[3] == 0x20  # Monday
    assert hass.states.get("sensor.test_ft_plug_timers").state == (
        "Mon: on 07:00, off 20:30"
    )


async def test_delete_a_timer(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await _plug(
        fake_ft_plug,
        make_timers_payload(
            make_timer_entry(), make_timer_entry(timer_id=1, mask=0x20)
        ),
    )
    entry = await _setup(hass, plug)
    result = await _menu(hass, entry, "delete_timer")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timer": "1"}
    )
    assert result["step_id"] == "confirm_delete"
    assert result["description_placeholders"]["timer"] == "Mon: on 08:00, off 20:30"
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_now"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert plug.timers == START


async def test_edit_and_delete_abort_when_there_is_nothing_to_pick(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    plug.timers = make_timers_payload()  # removed in the app meanwhile
    for option in ("edit_timer", "delete_timer"):
        result = await _menu(hass, entry, option)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_timers"


async def test_a_silent_plug_aborts_the_pick(hass: HomeAssistant, fake_ft_plug) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)
    plug.silent = True
    result = await _menu(hass, entry, "edit_timer")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_timer_steps_abort_when_the_entry_is_not_running(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    entry = await _setup(hass, await _plug(fake_ft_plug), load=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "settings"  # no menu: nothing to manage yet
    flow = hass.config_entries.options._progress[result["flow_id"]]
    for step in (flow.async_step_add_timer, flow.async_step_edit_timer):
        out = await step()
        assert out["type"] is FlowResultType.ABORT
        assert out["reason"] == "not_loaded"


async def test_the_pickers_and_the_delete_menu_can_go_back(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    plug = await _plug(fake_ft_plug)
    entry = await _setup(hass, plug)

    for option in ("edit_timer", "delete_timer"):
        result = await _menu(hass, entry, option)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"timer": "back"}
        )
        assert result["type"] is FlowResultType.MENU

    result = await _menu(hass, entry, "delete_timer")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timer": "0"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    assert result["type"] is FlowResultType.MENU
    assert plug.timers == START


async def test_the_poll_interval_is_a_number_box(
    hass: HomeAssistant, fake_ft_plug
) -> None:
    entry = await _setup(hass, await _plug(fake_ft_plug))
    result = await _menu(hass, entry, "settings")
    schema = result["data_schema"].schema
    box = next(v for k, v in schema.items() if k == "poll_interval")
    assert box.config["mode"] == "box"

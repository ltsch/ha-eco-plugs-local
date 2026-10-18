"""Switch entity tests, driven by synthetic beacons and a loopback fake plug."""

from __future__ import annotations

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

from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub import get_hub

from .beacon_factory import make_beacon

ENTITY = "switch.test_plug"


async def _setup(
    hass: HomeAssistant, uid: str = "020000000001", title: str = "Test Plug", **options
) -> MockConfigEntry:
    mac = ":".join(uid[i : i + 2] for i in range(0, 12, 2))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=uid,
        data={"mac": mac, "device_id": "ECO-00000001"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _beacon(hass: HomeAssistant, on: bool, host: str = "127.0.0.1", **kw) -> None:
    get_hub(hass)._on_beacon_datagram(make_beacon(on=on, **kw), (host, 10229))


async def test_unavailable_until_first_beacon(hass: HomeAssistant) -> None:
    """No restored/guessed state: the original motivating bug."""
    await _setup(hass)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    _beacon(hass, True)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON
    assert hass.states.get(ENTITY).attributes["ip_address"] == "127.0.0.1"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "020000000001")})
    assert device.sw_version == "V2.3.0"


async def test_debounce(hass: HomeAssistant) -> None:
    await _setup(hass)  # default debounce 2
    _beacon(hass, True)
    _beacon(hass, False)  # single blip ignored
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON
    _beacon(hass, True)
    _beacon(hass, False)
    _beacon(hass, False)  # two in a row accepted
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_debounce_option(hass: HomeAssistant) -> None:
    await _setup(hass, debounce=1)
    _beacon(hass, True)
    _beacon(hass, False)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_other_plugs_ignored(hass: HomeAssistant) -> None:
    await _setup(hass)
    _beacon(hass, True, mac=bytes.fromhex("020000000099"))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_goes_stale_and_relearns(hass: HomeAssistant) -> None:
    await _setup(hass, stale_timeout=30)
    _beacon(hass, True)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON
    with patch(
        "custom_components.eco_plugs_local.switch.monotonic",
        side_effect=lambda: monotonic() + 31,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
        assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    # After the gap, the plug's report is accepted at once despite debounce 2.
    _beacon(hass, False)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_first_beacon_after_gap_not_stale(hass: HomeAssistant) -> None:
    """A beacon arriving before the stale timer fires must not resurrect old state."""
    await _setup(hass, stale_timeout=30)
    _beacon(hass, True)
    await hass.async_block_till_done()
    entity = hass.data["entity_components"]["switch"].get_entity(ENTITY)
    entity._last_seen -= 31  # a gap the 5 s stale timer hasn't noticed yet
    _beacon(hass, False)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_two_plugs_share_hub(hass: HomeAssistant) -> None:
    first = await _setup(hass)
    second = await _setup(hass, uid="020000000002", title="Other Plug")
    _beacon(hass, True)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON
    assert hass.states.get("switch.other_plug").state == STATE_UNAVAILABLE
    hub = get_hub(hass)
    assert await hass.config_entries.async_unload(first.entry_id)
    assert hub._transport is not None
    assert await hass.config_entries.async_unload(second.entry_id)
    assert hub._transport is None


async def test_options_change_applies(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.config_entries.async_update_entry(entry, options={"debounce": 1})
    await hass.async_block_till_done()
    _beacon(hass, True)
    _beacon(hass, False)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_setup_retry_on_bind_failure(hass: HomeAssistant) -> None:
    from homeassistant.config_entries import ConfigEntryState

    with patch(
        "custom_components.eco_plugs_local.hub._udp_socket",
        side_effect=OSError("busy"),
    ):
        entry = await _setup_no_assert(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert get_hub(hass)._users == 0


async def _setup_no_assert(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Plug",
        unique_id="020000000001",
        data={"mac": "02:00:00:00:00:01", "device_id": "ECO-00000001"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_turn_on_round_trip(hass: HomeAssistant, fake_plug) -> None:
    plug = await fake_plug()
    await _setup(hass)
    _beacon(hass, False, port=plug.port)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_OFF

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
    )
    assert hass.states.get(ENTITY).state == STATE_ON
    assert [p[0x04] for p in plug.received[:2]] == [0x16, 0x17]
    assert plug.decoded(0)[0x0C:0x10] == (123456).to_bytes(4, "little")
    assert plug.decoded(0)[0x10:0x1A] == b"abcdEFGh12"
    assert plug.received[0][0x50] == 1
    assert len(plug.received) == 2  # stopped retrying after the first ACK

    # A stale beacon still saying "off" right after the ACK must not flip it back.
    _beacon(hass, False, port=plug.port)
    _beacon(hass, False, port=plug.port)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_turn_off_no_ack_raises(hass: HomeAssistant, fake_plug) -> None:
    plug = await fake_plug(reply=False)
    await _setup(hass)
    _beacon(hass, True, port=plug.port)
    await hass.async_block_till_done()
    with (
        patch("custom_components.eco_plugs_local.hub.REPLY_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError, match="did not acknowledge"),
    ):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": ENTITY}, blocking=True
        )
    assert len(plug.received) == 6  # 3 attempts x 2 sub-types
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_wrong_state_reply_raises(hass: HomeAssistant, fake_plug) -> None:
    plug = await fake_plug(wrong_state=True)
    await _setup(hass)
    _beacon(hass, True, port=plug.port)
    await hass.async_block_till_done()
    with (
        patch("custom_components.eco_plugs_local.hub.REPLY_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError, match="reports on"),
    ):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_unload_releases_listener(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hub = get_hub(hass)
    assert hub._transport is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hub._transport is None


async def test_platform_failure_releases_hub(hass: HomeAssistant) -> None:
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        side_effect=RuntimeError("boom"),
    ):
        await _setup_no_assert(hass)
    hub = get_hub(hass)
    assert hub._users == 0 and hub._transport is None


async def test_duplicate_copies_do_not_satisfy_debounce(hass: HomeAssistant) -> None:
    await _setup(hass)  # debounce 2
    _beacon(hass, True)
    off = make_beacon(on=False, key=b"\x09\x09\x09\x09")
    for _ in range(2):  # one beacon, received on two interfaces
        get_hub(hass)._on_beacon_datagram(off, ("127.0.0.1", 10229))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_ON

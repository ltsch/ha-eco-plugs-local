"""Config and options flow tests."""

from __future__ import annotations

import time
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eco_plugs_local import protocol as P, protocol_ft as PF
from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub import CommandError, Sighting
from custom_components.eco_plugs_local.hub_ft import FtSighting

from .beacon_factory import make_beacon
from .ft_factory import make_search_reply

DISCOVER = "custom_components.eco_plugs_local.config_flow.async_discover"
FT_DISCOVER = "custom_components.eco_plugs_local.config_flow.async_ft_discover"


def _found() -> dict[str, Sighting]:
    b = P.parse_beacon(make_beacon())
    return {b.unique_id: Sighting(b, "192.0.2.10", time.monotonic())}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    with patch(DISCOVER, return_value=_found()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    with patch(
        "custom_components.eco_plugs_local.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "020000000001"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Plug"
    assert result["data"] == {"mac": "02:00:00:00:00:01", "device_id": "ECO-00000001"}
    assert result["result"].unique_id == "020000000001"


async def test_no_devices(hass: HomeAssistant) -> None:
    with patch(DISCOVER, return_value={}):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_already_configured_is_hidden(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="020000000001").add_to_hass(hass)
    with patch(DISCOVER, return_value=_found()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="020000000001",
        data={"mac": "02:00:00:00:00:01", "device_id": "ECO-00000001"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"debounce": 3, "stale_timeout": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"debounce": 3, "stale_timeout": 60}


async def test_port_unavailable(hass: HomeAssistant) -> None:
    with patch(DISCOVER, side_effect=CommandError("busy")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_unavailable"


def _ft_found() -> dict[str, FtSighting]:
    d = PF.parse_search_reply(make_search_reply())
    return {d.unique_id: FtSighting(d, "192.0.2.20", time.monotonic())}


async def test_ft_plug_flow(hass: HomeAssistant) -> None:
    with patch(DISCOVER, return_value={}), patch(FT_DISCOVER, return_value=_ft_found()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    with patch(
        "custom_components.eco_plugs_local.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "020000000002"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test FT Plug"
    assert result["data"] == {
        "mac": "02:00:00:00:00:02",
        "device_id": "ECO-00000002",
        "family": "ft",
    }
    assert result["result"].unique_id == "020000000002"


async def test_both_families_are_listed(hass: HomeAssistant) -> None:
    with (
        patch(DISCOVER, return_value=_found()),
        patch(FT_DISCOVER, return_value=_ft_found()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    (field,) = result["data_schema"].schema
    assert set(result["data_schema"].schema[field].container) == {
        "020000000001",
        "020000000002",
    }


async def test_one_busy_port_does_not_hide_the_other_family(
    hass: HomeAssistant,
) -> None:
    with (
        patch(DISCOVER, side_effect=CommandError("busy")),
        patch(FT_DISCOVER, return_value=_ft_found()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM


async def test_busy_port_is_reported_when_nothing_was_found(
    hass: HomeAssistant,
) -> None:
    with (
        patch(DISCOVER, return_value={}),
        patch(FT_DISCOVER, side_effect=CommandError("busy")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["reason"] == "port_unavailable"
    assert result["description_placeholders"]["port"] == "8900"


async def test_both_ports_busy(hass: HomeAssistant) -> None:
    with (
        patch(DISCOVER, side_effect=CommandError("busy")),
        patch(FT_DISCOVER, side_effect=CommandError("busy")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["reason"] == "port_unavailable"
    assert result["description_placeholders"]["port"] == "10228, 8900"


async def test_ft_options_flow_has_poll_interval_not_debounce(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="020000000002",
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert [k.schema for k in result["data_schema"].schema] == [
        "poll_interval",
        "stale_timeout",
    ]
    defaults = {k.schema: k.default() for k in result["data_schema"].schema}
    assert defaults == {"poll_interval": 10, "stale_timeout": 30}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 20, "stale_timeout": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"poll_interval": 20, "stale_timeout": 60}


@pytest.mark.parametrize("interval", [2, 61])
async def test_ft_poll_interval_is_bounded(hass: HomeAssistant, interval: int) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="020000000002",
        data={"mac": "02:00:00:00:00:02", "device_id": "ECO-00000002", "family": "ft"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"poll_interval": interval, "stale_timeout": 30}
        )

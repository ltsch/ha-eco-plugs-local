"""Config and options flow tests."""

from __future__ import annotations

import time
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eco_plugs_local import protocol as P
from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.hub import Sighting

from .beacon_factory import make_beacon

DISCOVER = "custom_components.eco_plugs_local.config_flow.async_discover"


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
    from custom_components.eco_plugs_local.hub import CommandError

    with patch(DISCOVER, side_effect=CommandError("busy")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_unavailable"

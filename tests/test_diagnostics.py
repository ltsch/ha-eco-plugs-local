"""Diagnostics must not leak identifying data."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eco_plugs_local.const import DOMAIN
from custom_components.eco_plugs_local.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.eco_plugs_local.hub import get_hub

from .beacon_factory import FAKE, make_beacon


async def test_diagnostics_redacted(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Plug",
        unique_id="020000000001",
        data={"mac": "02:00:00:00:00:01", "device_id": "ECO-00000001"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    get_hub(hass)._on_beacon_datagram(make_beacon(), ("192.0.2.10", 10229))

    text = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (
        FAKE["token"].decode(),
        FAKE["token"].hex(),
        FAKE["short_id"].hex(),
        "192.0.2.10",
        "02:00:00:00:00:01",
        "ECO-00000001",
        "Test Plug",
        str(FAKE["eco_id"]),
        str(FAKE["key"]),
    ):
        assert secret not in text, secret
    assert '"is_on": true' in text
    assert '"firmware": 230' in text

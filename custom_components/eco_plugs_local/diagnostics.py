"""Diagnostics for Eco Plugs (Local)."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EcoPlugsConfigEntry

TO_REDACT = {
    "mac",
    "token",
    "eco_id",
    "short_id",
    "key",
    "device_id",
    "name",
    "ip",
    "host",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EcoPlugsConfigEntry
) -> dict[str, Any]:
    hub = entry.runtime_data
    sighting = hub.sightings.get(entry.unique_id or "")
    beacon: dict[str, Any] | None = None
    if sighting is not None:
        beacon = {
            k: (v.hex() if isinstance(v, bytes) else v)
            for k, v in asdict(sighting.beacon).items()
        }
        beacon["host"] = sighting.host
        beacon["seconds_since_seen"] = round(time.monotonic() - sighting.monotonic, 1)
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_beacon": async_redact_data(beacon, TO_REDACT) if beacon else None,
    }

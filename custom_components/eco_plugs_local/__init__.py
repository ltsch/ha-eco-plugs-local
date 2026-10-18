"""Local LAN control for Eco Plugs (KAB) smart plugs, no cloud required."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .hub import CommandError, EcoPlugsHub, get_hub

PLATFORMS = [Platform.SWITCH]

type EcoPlugsConfigEntry = ConfigEntry[EcoPlugsHub]


async def async_setup_entry(hass: HomeAssistant, entry: EcoPlugsConfigEntry) -> bool:
    """Set up one plug."""
    hub = get_hub(hass)
    try:
        await hub.async_acquire()
    except CommandError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.async_release()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EcoPlugsConfigEntry) -> bool:
    """Unload one plug."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_release()
    return unloaded


async def _async_options_updated(
    hass: HomeAssistant, entry: EcoPlugsConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

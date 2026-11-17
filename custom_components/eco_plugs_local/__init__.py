"""Local LAN control for Eco Plugs (KAB) devices, no cloud required."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_FAMILY, FAMILY_FT
from .hub import CommandError, EcoPlugsHub, get_hub
from .hub_ft import EcoPlugsFtHub, get_ft_hub

PLATFORMS = [Platform.SWITCH]
PLATFORMS_FT = [Platform.SWITCH, Platform.SENSOR]

type EcoPlugsConfigEntry = ConfigEntry[EcoPlugsHub | EcoPlugsFtHub]


def _platforms(entry: ConfigEntry) -> list[Platform]:
    return PLATFORMS_FT if entry.data.get(CONF_FAMILY) == FAMILY_FT else PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: EcoPlugsConfigEntry) -> bool:
    """Set up one plug."""
    hub = (
        get_ft_hub(hass) if entry.data.get(CONF_FAMILY) == FAMILY_FT else get_hub(hass)
    )
    try:
        await hub.async_acquire()
    except CommandError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, _platforms(entry))
    except BaseException:
        await hub.async_release()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EcoPlugsConfigEntry) -> bool:
    """Unload one plug."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry)
    )
    if unloaded:
        await entry.runtime_data.async_release()
    return unloaded


async def _async_options_updated(
    hass: HomeAssistant, entry: EcoPlugsConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

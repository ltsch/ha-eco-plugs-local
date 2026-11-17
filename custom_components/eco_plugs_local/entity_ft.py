"""Base for v1.x entities fed by the plug's search replies (its clock record)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_DEVICE_ID,
    CONF_MAC,
    DOMAIN,
    FT_SLOW_POLL_SECONDS,
    MANUFACTURER,
)
from .hub import CommandError
from .hub_ft import EcoPlugsFtHub, FtSighting
from .protocol_ft import TimerSettings

# A search reply arrives about every FT_SLOW_POLL_SECONDS; allow two to go missing.
_STALE_SECONDS = FT_SLOW_POLL_SECONDS * 3


def ft_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The same description from every platform, so the platforms can set up in any order."""
    mac: str = entry.data[CONF_MAC]
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or mac.replace(":", "").lower())},
        connections={(CONNECTION_NETWORK_MAC, mac)},
        manufacturer=MANUFACTURER,
        model="Eco Plugs device",
        name=entry.title,
        serial_number=entry.data.get(CONF_DEVICE_ID),
        configuration_url=f"homeassistant://config/integrations/integration/{DOMAIN}",
    )


class FtClockEntity(Entity):
    """Shows something from the plug's clock record, and is unavailable without one."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: EcoPlugsFtHub, entry: ConfigEntry, key: str) -> None:
        self._hub = hub
        self._uid = entry.unique_id or entry.data[CONF_MAC].replace(":", "").lower()
        self._attr_unique_id = f"{self._uid}_{key}"
        self._attr_device_info = ft_device_info(entry)
        self._seen = 0.0
        self._has_clock = False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.subscribe(self._uid, self._on_sighting))
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._check_stale, timedelta(seconds=10)
            )
        )
        if sighting := self._hub.sighting(self._uid):
            self._take(sighting)

    @property
    def available(self) -> bool:
        return self._has_clock and monotonic() - self._seen <= _STALE_SECONDS

    def _take(self, sighting: FtSighting) -> None:
        self._has_clock = sighting.device.clock is not None
        self._seen = monotonic()
        self._update(sighting)

    def _update(self, sighting: FtSighting) -> None:
        raise NotImplementedError

    @callback
    def _on_sighting(self, sighting: FtSighting) -> None:
        self._take(sighting)
        self.async_write_ha_state()

    @callback
    def _check_stale(self, _now: Any = None) -> None:
        if self._seen and not self.available:
            self.async_write_ha_state()


class FtSettingsEntity(Entity):
    """Shows something from the plug's timers and flags, once they have been read."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: EcoPlugsFtHub, entry: ConfigEntry, key: str) -> None:
        self._hub = hub
        self._uid = entry.unique_id or entry.data[CONF_MAC].replace(":", "").lower()
        self._attr_unique_id = f"{self._uid}_{key}"
        self._attr_device_info = ft_device_info(entry)
        self._seen = 0.0

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.subscribe_settings(self._uid, self._on_settings))
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._check_stale, timedelta(seconds=10)
            )
        )
        if settings := self._hub.timer_settings(self._uid):
            self._take(settings)

    @property
    def available(self) -> bool:
        return self._seen > 0 and monotonic() - self._seen <= _STALE_SECONDS

    def _take(self, settings: TimerSettings) -> None:
        self._seen = monotonic()
        self._update(settings)

    def _update(self, settings: TimerSettings) -> None:
        raise NotImplementedError

    @callback
    def _on_settings(self, settings: TimerSettings) -> None:
        self._take(settings)
        self.async_write_ha_state()

    @callback
    def _check_stale(self, _now: Any = None) -> None:
        if self._seen and not self.available:
            self.async_write_ha_state()


async def probe_support[T](
    hub: EcoPlugsFtHub, uid: str, fetch: Callable[[], Awaitable[T]]
) -> tuple[T | None, bool]:
    """One attempt at ``fetch``. The flag is False only if the plug answers status
    queries but not this one, which means it doesn't support it."""
    try:
        return await fetch(), True
    except CommandError:
        try:
            await hub.async_get_state(uid, attempts=1)
        except CommandError:
            return None, True  # offline: can't tell, so keep the entities
        return None, False

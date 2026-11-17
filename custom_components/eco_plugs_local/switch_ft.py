"""Switch entity for v1.x plugs. State comes only from the plug (status replies or
command confirmations); it is never restored. See docs/ARCHITECTURE.md."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from . import EcoPlugsConfigEntry
from .const import (
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_STALE_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    FT_SLOW_POLL_SECONDS,
)
from .entity_ft import FtClockEntity, FtSettingsEntity, ft_device_info
from .hub import CommandError
from .hub_ft import EcoPlugsFtHub, FtSighting
from .protocol_ft import TimerSettings

_LOGGER = logging.getLogger(__name__)


class EcoPlugFtSwitch(SwitchEntity):
    """A v1.x plug's relay."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, hub: EcoPlugsFtHub, entry: EcoPlugsConfigEntry) -> None:
        self._hub = hub
        mac: str = entry.data[CONF_MAC]
        self._uid = entry.unique_id or mac.replace(":", "").lower()
        self._interval = float(
            entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        # Never call the plug unavailable between two polls.
        self._stale = max(
            float(entry.options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT)),
            3 * self._interval,
        )
        self._attr_unique_id = self._uid
        self._attr_device_info = ft_device_info(entry)
        self._attr_is_on = None
        self._last_seen = 0.0
        self._polling = False
        self._firmware: str | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.watch(self._uid))
        poller = self._hub.poller(self._uid, self._interval)
        slow = max(1, round(FT_SLOW_POLL_SECONDS / self._interval))
        self.async_on_remove(poller.register("status", 1, self._async_poll))
        # A search reply carries the plug's clock, so ask for one now and then.
        self.async_on_remove(poller.register("clock", slow, self._async_read_clock))
        self.hass.async_create_background_task(
            self._async_poll(), f"{DOMAIN} first poll"
        )

    @property
    def available(self) -> bool:
        return self._last_seen > 0 and monotonic() - self._last_seen <= self._stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sighting = self._hub.sighting(self._uid)
        return {"ip_address": sighting.host if sighting else None}

    async def _async_poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        try:
            state = await self._hub.async_get_state(self._uid)
        except CommandError as err:
            _LOGGER.debug("Poll failed: %s", err)
            if self._attr_is_on is not None and not self.available:
                self._attr_is_on = None  # whatever we knew before the gap is stale
                self.async_write_ha_state()
            return
        finally:
            self._polling = False
        was_available = self.available
        self._last_seen = monotonic()
        self._update_firmware()
        if not was_available or state != self._attr_is_on:
            self._attr_is_on = state
            self.async_write_ha_state()

    async def _async_read_clock(self) -> None:
        await self._hub.async_refresh(self._uid, broadcast=False)

    @callback
    def _update_firmware(self) -> None:
        sighting = self._hub.sighting(self._uid)
        if sighting is None or sighting.device.version == self._firmware:
            return
        self._firmware = sighting.device.version
        if (device := self.device_entry) is not None:
            dr.async_get(self.hass).async_update_device(
                device.id, sw_version=self._firmware
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        await self._hub.async_set_power(self._uid, on)
        self._attr_is_on = on
        self._last_seen = monotonic()  # the plug just answered
        self.async_write_ha_state()


class EcoPlugFtDstSwitch(FtClockEntity, SwitchEntity):
    """The plug's daylight-saving flag, which its timers may use."""

    _attr_translation_key = "dst"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: EcoPlugsFtHub, entry: EcoPlugsConfigEntry) -> None:
        super().__init__(hub, entry, "dst")
        self._attr_is_on = None

    def _update(self, sighting: FtSighting) -> None:
        clock = sighting.device.clock
        self._attr_is_on = None if clock is None else clock.dst

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        await self._hub.async_set_dst(self._uid, on)
        if sighting := self._hub.sighting(self._uid):
            self._take(sighting)
        self.async_write_ha_state()


class EcoPlugFtRandomSwitch(FtSettingsEntity, SwitchEntity):
    """The app's "random 30" flag: the plug moves its timers' edges a little each day."""

    _attr_translation_key = "random_timers"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: EcoPlugsFtHub, entry: EcoPlugsConfigEntry) -> None:
        super().__init__(hub, entry, "random_timers")
        self._attr_is_on = None

    def _update(self, settings: TimerSettings) -> None:
        self._attr_is_on = settings.random

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_set_random(self._uid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_set_random(self._uid, False)

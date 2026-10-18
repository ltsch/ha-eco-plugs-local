"""Switch entity. State comes only from the plug (beacons or command ACKs);
it is never restored. See docs/ARCHITECTURE.md for the state machine."""

from __future__ import annotations

from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import EcoPlugsConfigEntry
from .const import (
    ACK_GRACE_SECONDS,
    CONF_DEBOUNCE,
    CONF_DEVICE_ID,
    CONF_MAC,
    CONF_STALE_TIMEOUT,
    DEFAULT_DEBOUNCE,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    MANUFACTURER,
)
from .hub import EcoPlugsHub, Sighting


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoPlugsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch for a config entry."""
    async_add_entities([EcoPlugSwitch(entry.runtime_data, entry)])


class EcoPlugSwitch(SwitchEntity):
    """A plug's relay."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, hub: EcoPlugsHub, entry: EcoPlugsConfigEntry) -> None:
        self._hub = hub
        mac: str = entry.data[CONF_MAC]
        self._uid = entry.unique_id or mac.replace(":", "").lower()
        self._debounce = int(entry.options.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE))
        self._stale = float(
            entry.options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT)
        )
        self._attr_unique_id = self._uid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._uid)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            manufacturer=MANUFACTURER,
            model="Eco Plugs smart plug",
            name=entry.title,
            serial_number=entry.data.get(CONF_DEVICE_ID),
        )
        self._attr_is_on = None
        self._pending: bool | None = None
        self._pending_count = 0
        self._ack_until = 0.0
        self._last_seen = 0.0
        self._host: str | None = None
        self._firmware: str | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.subscribe(self._uid, self._on_beacon))
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._check_stale, timedelta(seconds=5)
            )
        )
        if sighting := self._hub.recent(self._stale).get(self._uid):
            self._on_beacon(sighting, write=False)

    @property
    def available(self) -> bool:
        return self._last_seen > 0 and monotonic() - self._last_seen <= self._stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ip_address": self._host}

    @callback
    def _check_stale(self, _now: Any = None) -> None:
        if self._last_seen and not self.available and self._attr_is_on is not None:
            self._forget()
            self.async_write_ha_state()

    @callback
    def _forget(self) -> None:
        self._attr_is_on = None
        self._pending, self._pending_count = None, 0

    @callback
    def _on_beacon(self, sighting: Sighting, write: bool = True) -> None:
        was_available = self.available
        if not was_available:
            self._forget()  # whatever we knew before the gap is stale
        changed = not was_available or sighting.host != self._host
        self._last_seen = sighting.monotonic
        self._host = sighting.host
        if sighting.beacon.firmware_version != self._firmware:
            self._firmware = sighting.beacon.firmware_version
            self._update_sw_version()
        reported = sighting.beacon.is_on

        if self._attr_is_on is None:
            self._attr_is_on = reported
            self._pending, self._pending_count = None, 0
            changed = True
        elif reported == self._attr_is_on:
            self._pending, self._pending_count = None, 0
        elif monotonic() < self._ack_until:
            pass  # likely a beacon built before the confirmed command
        else:
            if reported == self._pending:
                self._pending_count += 1
            else:
                self._pending, self._pending_count = reported, 1
            if self._pending_count >= self._debounce:
                self._attr_is_on = reported
                self._pending, self._pending_count = None, 0
                changed = True

        if changed and write:
            self.async_write_ha_state()

    @callback
    def _update_sw_version(self) -> None:
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self._uid)}):
            registry.async_update_device(device.id, sw_version=self._firmware)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        await self._hub.async_set_power(self._uid, on, self._stale)
        self._attr_is_on = on
        self._pending, self._pending_count = None, 0
        self._last_seen = monotonic()  # the plug just answered
        self._ack_until = self._last_seen + ACK_GRACE_SECONDS
        self.async_write_ha_state()

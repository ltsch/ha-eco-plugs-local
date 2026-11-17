"""Voltage, current, power and energy sensors for v1.x plugs that have a meter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import EcoPlugsConfigEntry
from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    FT_SLOW_POLL_SECONDS,
)
from .entity_ft import FtClockEntity, ft_device_info, probe_support
from .hub import CommandError
from .hub_ft import EcoPlugsFtHub, FtSighting
from .protocol_ft import WEEKDAYS, PowerReading, TimerSettings, describe_timers

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EcoPlugSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PowerReading], float]


SENSORS = (
    EcoPlugSensorDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.volts,
    ),
    EcoPlugSensorDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.amps,
    ),
    EcoPlugSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.watts,
    ),
    # The plug only reports energy over a date range, so this is the day's total.
    # It falls back to ~0 at midnight, which HA treats as a new meter cycle.
    EcoPlugSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda r: r.kwh,
    ),
)


class EcoPlugsPowerCoordinator(DataUpdateCoordinator[PowerReading]):
    """Polls the plug's meter."""

    def __init__(
        self, hass: HomeAssistant, entry: EcoPlugsConfigEntry, hub: EcoPlugsFtHub
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} power",
            update_interval=None,  # refreshed by the plug's burst scheduler
        )
        self.hub = hub
        self.uid = entry.unique_id or ""

    async def _async_update_data(self) -> PowerReading:
        try:
            return await self.hub.async_get_power(self.uid, dt_util.now().date())
        except CommandError as err:
            raise UpdateFailed(str(err)) from err


class EcoPlugsTimersCoordinator(DataUpdateCoordinator[TimerSettings]):
    """Reads the plug's timers now and then."""

    def __init__(
        self, hass: HomeAssistant, entry: EcoPlugsConfigEntry, hub: EcoPlugsFtHub
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} timers",
            update_interval=None,
        )
        self.hub = hub
        self.uid = entry.unique_id or ""

    async def _async_update_data(self) -> TimerSettings:
        try:
            return await self.hub.async_get_timers(self.uid)
        except CommandError as err:
            raise UpdateFailed(str(err)) from err


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoPlugsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the clock sensor, and the meter and timer sensors the plug supports."""
    hub = entry.runtime_data
    uid = entry.unique_id or ""
    entities: list[SensorEntity] = [EcoPlugClockSensor(hub, entry)]
    interval = float(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
    poller = hub.poller(uid, interval)
    slow = max(1, round(FT_SLOW_POLL_SECONDS / interval))

    # One attempt each, so an offline plug delays startup by seconds, not tens of them.
    reading, supported = await probe_support(
        hub, uid, lambda: hub.async_get_power(uid, dt_util.now().date(), attempts=1)
    )
    offline = reading is None and supported
    if supported and (offline or (reading is not None and reading.metered)):
        power = EcoPlugsPowerCoordinator(hass, entry, hub)
        if reading is not None:
            power.async_set_updated_data(reading)
        else:
            entry.async_create_background_task(
                hass, power.async_refresh(), f"{DOMAIN} first power poll"
            )
        entities.extend(EcoPlugSensor(power, d) for d in SENSORS)
        entry.async_on_unload(poller.register("power", 1, power.async_refresh))

    settings = None
    timers_supported = True
    if not offline:
        settings, timers_supported = await probe_support(
            hub, uid, lambda: hub.async_get_timers(uid, attempts=1)
        )
    if timers_supported:
        timers = EcoPlugsTimersCoordinator(hass, entry, hub)
        if settings is not None:
            timers.async_set_updated_data(settings)
        else:
            entry.async_create_background_task(
                hass, timers.async_refresh(), f"{DOMAIN} first timers poll"
            )
        entities.append(EcoPlugTimersSensor(timers))
        # A change made by a switch or a service shows here at once, not a minute later.
        entry.async_on_unload(
            hub.subscribe_settings(uid, timers.async_set_updated_data)
        )
        entry.async_on_unload(poller.register("timers", slow, timers.async_refresh))
    async_add_entities(entities)
    if timers_supported:
        _register_timer_services()


class EcoPlugSensor(CoordinatorEntity[EcoPlugsPowerCoordinator], SensorEntity):
    """One meter reading."""

    entity_description: EcoPlugSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoPlugsPowerCoordinator,
        description: EcoPlugSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.uid}_{description.key}"
        self._attr_device_info = ft_device_info(coordinator.config_entry)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return self.entity_description.value_fn(data) if data else None


class EcoPlugClockSensor(FtClockEntity, SensorEntity):
    """How far the plug's clock is from Home Assistant's. Its schedules run on that clock."""

    _attr_translation_key = "clock_difference"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, hub: EcoPlugsFtHub, entry: EcoPlugsConfigEntry) -> None:
        super().__init__(hub, entry, "clock_difference")
        self._attr_native_value = None

    def _update(self, sighting: FtSighting) -> None:
        self._attr_native_value = sighting.clock_difference


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _state(value: int) -> str:
    return {1: "on", 0: "off"}.get(value, str(value))


class EcoPlugTimersSensor(CoordinatorEntity[EcoPlugsTimersCoordinator], SensorEntity):
    """The plug's timers in words. They run on the plug, even when HA is down."""

    _attr_has_entity_name = True
    _attr_translation_key = "timers"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EcoPlugsTimersCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.uid}_timers"
        self._attr_device_info = ft_device_info(coordinator.config_entry)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return describe_timers(data.timers) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not data:
            return None
        return {
            "count": len(data.timers),
            "timers": [
                {
                    "id": t.timer_id,
                    "days": list(t.days),
                    "start": _clock(t.start_seconds),
                    "start_state": _state(t.start_state),
                    "end": _clock(t.end_seconds),
                    "end_state": _state(t.end_state),
                }
                for t in data.timers
            ],
            "random_timers": data.random,
            "vacation_mode": data.vacation,
        }

    async def async_add_timer(self, days: list[str], start: time, end: time) -> None:
        """Add a timer: on at ``start``, off at ``end``, on the given days."""
        coordinator = self.coordinator
        settings = await coordinator.hub.async_add_timer(
            coordinator.uid, days, _seconds(start), _seconds(end), dt_util.now().date()
        )
        coordinator.async_set_updated_data(settings)

    async def async_update_timer(
        self,
        timer_id: int,
        days: list[str] | None = None,
        start: time | None = None,
        end: time | None = None,
    ) -> None:
        """Change a timer. What isn't given stays as it was."""
        coordinator = self.coordinator
        settings = await coordinator.hub.async_update_timer(
            coordinator.uid,
            timer_id,
            days,
            None if start is None else _seconds(start),
            None if end is None else _seconds(end),
        )
        coordinator.async_set_updated_data(settings)

    async def async_delete_timer(self, timer_id: int) -> None:
        """Remove a timer."""
        coordinator = self.coordinator
        settings = await coordinator.hub.async_delete_timer(coordinator.uid, timer_id)
        coordinator.async_set_updated_data(settings)


_DAYS = vol.All(cv.ensure_list, vol.Length(min=1), [vol.In(WEEKDAYS)])
_TIMER_ID = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _register_timer_services() -> None:
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "add_timer",
        {
            vol.Required("days"): _DAYS,
            vol.Required("start"): cv.time,
            vol.Required("end"): cv.time,
        },
        "async_add_timer",
    )
    platform.async_register_entity_service(
        "update_timer",
        vol.All(
            cv.make_entity_service_schema(
                {
                    vol.Required("timer_id"): _TIMER_ID,
                    vol.Optional("days"): _DAYS,
                    vol.Optional("start"): cv.time,
                    vol.Optional("end"): cv.time,
                }
            ),
            cv.has_at_least_one_key("days", "start", "end"),
        ),
        "async_update_timer",
    )
    platform.async_register_entity_service(
        "delete_timer", {vol.Required("timer_id"): _TIMER_ID}, "async_delete_timer"
    )

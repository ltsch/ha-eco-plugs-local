"""Config flow: pick a plug from the devices heard or found on the LAN."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    CONF_DEBOUNCE,
    CONF_DEVICE_ID,
    CONF_FAMILY,
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_STALE_TIMEOUT,
    DEFAULT_DEBOUNCE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DISCOVERY_SECONDS,
    DOMAIN,
    FAMILY_FT,
    FAMILY_KAB,
)
from .hub import CommandError, async_discover
from .hub_ft import EcoPlugsFtHub, async_ft_discover
from .protocol import BEACON_LISTEN_PORT
from .protocol_ft import SOURCE_PORT as FT_SOURCE_PORT, WEEKDAYS, describe_timer

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE = "device"


@dataclass(frozen=True, slots=True)
class _Found:
    family: str
    title: str
    mac: str
    device_id: str
    host: str


class EcoPlugsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eco Plugs (Local)."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, _Found] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            found = self._found[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(found.mac.replace(":", "").lower())
            self._abort_if_unique_id_configured()
            data = {CONF_MAC: found.mac, CONF_DEVICE_ID: found.device_id}
            if found.family == FAMILY_FT:
                data[CONF_FAMILY] = FAMILY_FT
            return self.async_create_entry(title=found.title, data=data)

        kab, ft = await asyncio.gather(
            async_discover(self.hass),
            async_ft_discover(self.hass),
            return_exceptions=True,
        )
        found: dict[str, _Found] = {}
        failed_ports: list[int] = []
        for family, port, result in (
            (FAMILY_KAB, BEACON_LISTEN_PORT, kab),
            (FAMILY_FT, FT_SOURCE_PORT, ft),
        ):
            if isinstance(result, CommandError):
                _LOGGER.debug("Discovery (%s) failed: %s", family, result)
                failed_ports.append(port)
            elif isinstance(result, BaseException):
                raise result
            elif family == FAMILY_KAB:
                for uid, s in result.items():
                    b = s.beacon
                    found[uid] = _Found(
                        family, b.name or b.device_id, b.mac, b.device_id, s.host
                    )
            else:
                for uid, s in result.items():
                    d = s.device
                    found[uid] = _Found(
                        family, d.alias or d.device_id, d.mac, d.device_id, s.host
                    )

        placeholders = {
            "seconds": str(int(DISCOVERY_SECONDS)),
            "port": ", ".join(map(str, failed_ports)),
        }
        configured = self._async_current_ids(include_ignore=True)
        self._found = {uid: f for uid, f in found.items() if uid not in configured}
        if not self._found:
            return self.async_abort(
                reason="port_unavailable" if failed_ports else "no_devices_found",
                description_placeholders=placeholders,
            )

        choices = {
            uid: f"{f.title} ({f.host}, {f.mac})" for uid, f in self._found.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(choices)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EcoPlugsOptionsFlow()


def _seconds_box(low: int, high: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


def _timer_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("days", default=list(WEEKDAYS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WEEKDAYS),
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                    translation_key="weekday",
                )
            ),
            vol.Required("start", default="08:00:00"): selector.TimeSelector(),
            vol.Required("end", default="20:00:00"): selector.TimeSelector(),
        }
    )


def _seconds(value: str) -> int:
    t = cv.time(value)
    return t.hour * 3600 + t.minute * 60 + t.second


def _time_string(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class EcoPlugsOptionsFlow(OptionsFlow):
    """Polling and staleness, and for a v1.x plug the menu that manages its timers."""

    def __init__(self) -> None:
        self._timer_id: int | None = None

    @property
    def _uid(self) -> str:
        return self.config_entry.unique_id or ""

    @property
    def _hub(self) -> EcoPlugsFtHub | None:
        """The hub, if this is a v1.x plug whose entry is running."""
        entry = self.config_entry
        if (
            entry.data.get(CONF_FAMILY) != FAMILY_FT
            or entry.state is not ConfigEntryState.LOADED
        ):
            return None
        return entry.runtime_data

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        hub = self._hub
        if hub is None or hub.timer_settings(self._uid) is None:
            return await self.async_step_settings()
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_timer", "edit_timer", "delete_timer"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )
        opts = self.config_entry.options
        schema: dict[Any, Any] = {}
        if self.config_entry.data.get(CONF_FAMILY) == FAMILY_FT:
            schema[
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                )
            ] = _seconds_box(3, 60)
        else:
            schema[
                vol.Required(
                    CONF_DEBOUNCE, default=opts.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE)
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX
                )
            )
        schema[
            vol.Required(
                CONF_STALE_TIMEOUT,
                default=opts.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
            )
        ] = _seconds_box(10, 600)
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))

    def _done(self) -> ConfigFlowResult:
        # The options themselves are unchanged, so the entry isn't reloaded.
        return self.async_create_entry(data=self.config_entry.options)

    async def async_step_add_timer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (hub := self._hub) is None:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            if not user_input["days"]:
                errors["base"] = "no_days"
            else:
                try:
                    await hub.async_add_timer(
                        self._uid,
                        user_input["days"],
                        _seconds(user_input["start"]),
                        _seconds(user_input["end"]),
                        dt_util.now().date(),
                    )
                except CommandError as err:
                    errors["base"] = "write_failed"
                    placeholders["error"] = str(err)
                else:
                    return self._done()
        schema = _timer_schema()
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="add_timer",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _pick_timer(
        self, step_id: str, user_input: dict[str, Any] | None, then
    ) -> ConfigFlowResult:
        if (hub := self._hub) is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            if user_input["timer"] == "back":
                return await self.async_step_init()
            self._timer_id = int(user_input["timer"])
            return await then()
        try:
            settings = await hub.async_get_timers(self._uid)
        except CommandError:
            return self.async_abort(reason="cannot_connect")
        if not settings.timers:
            return self.async_abort(reason="no_timers")
        options = [
            selector.SelectOptionDict(value=str(t.timer_id), label=describe_timer(t))
            for t in settings.timers
        ]
        options.append(
            selector.SelectOptionDict(value="back", label="Back to the menu")
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("timer"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    def _chosen_timer(self):
        hub = self._hub
        settings = hub.timer_settings(self._uid) if hub else None
        if settings is None:
            return None
        return next((t for t in settings.timers if t.timer_id == self._timer_id), None)

    async def async_step_edit_timer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._pick_timer(
            "edit_timer", user_input, self.async_step_edit_timer_details
        )

    async def async_step_edit_timer_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (hub := self._hub) is None or (timer := self._chosen_timer()) is None:
            return self.async_abort(reason="no_timers")
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            if not user_input["days"]:
                errors["base"] = "no_days"
            else:
                try:
                    await hub.async_update_timer(
                        self._uid,
                        timer.timer_id,
                        user_input["days"],
                        _seconds(user_input["start"]),
                        _seconds(user_input["end"]),
                    )
                except CommandError as err:
                    errors["base"] = "write_failed"
                    placeholders["error"] = str(err)
                else:
                    return self._done()
        values = user_input or {
            "days": list(timer.days),
            "start": _time_string(timer.start_seconds),
            "end": _time_string(timer.end_seconds),
        }
        return self.async_show_form(
            step_id="edit_timer_details",
            data_schema=self.add_suggested_values_to_schema(_timer_schema(), values),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_delete_timer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._pick_timer(
            "delete_timer", user_input, self.async_step_confirm_delete
        )

    async def async_step_confirm_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._hub is None or (timer := self._chosen_timer()) is None:
            return self.async_abort(reason="no_timers")
        return self.async_show_menu(
            step_id="confirm_delete",
            menu_options=["delete_now", "init"],
            description_placeholders={"timer": describe_timer(timer)},
        )

    async def async_step_delete_now(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (hub := self._hub) is None or self._timer_id is None:
            return self.async_abort(reason="not_loaded")
        try:
            await hub.async_delete_timer(self._uid, self._timer_id)
        except CommandError as err:
            return self.async_abort(
                reason="write_failed", description_placeholders={"error": str(err)}
            )
        return self._done()

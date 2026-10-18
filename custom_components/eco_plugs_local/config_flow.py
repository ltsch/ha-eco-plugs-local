"""Config flow: pick a plug from the beacons heard on the LAN."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
import voluptuous as vol

from .const import (
    CONF_DEBOUNCE,
    CONF_DEVICE_ID,
    CONF_MAC,
    CONF_STALE_TIMEOUT,
    DEFAULT_DEBOUNCE,
    DEFAULT_STALE_TIMEOUT,
    DISCOVERY_SECONDS,
    DOMAIN,
)
from .hub import CommandError, Sighting, async_discover
from .protocol import BEACON_LISTEN_PORT

CONF_DEVICE = "device"


class EcoPlugsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eco Plugs (Local)."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, Sighting] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            sighting = self._found[user_input[CONF_DEVICE]]
            beacon = sighting.beacon
            await self.async_set_unique_id(beacon.unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=beacon.name or beacon.device_id,
                data={CONF_MAC: beacon.mac, CONF_DEVICE_ID: beacon.device_id},
            )

        placeholders = {
            "seconds": str(int(DISCOVERY_SECONDS)),
            "port": str(BEACON_LISTEN_PORT),
        }
        try:
            found = await async_discover(self.hass)
        except CommandError:
            return self.async_abort(
                reason="port_unavailable", description_placeholders=placeholders
            )
        configured = self._async_current_ids(include_ignore=True)
        self._found = {uid: s for uid, s in found.items() if uid not in configured}
        if not self._found:
            return self.async_abort(
                reason="no_devices_found", description_placeholders=placeholders
            )

        choices = {
            uid: f"{s.beacon.name or s.beacon.device_id} ({s.host}, {s.beacon.mac})"
            for uid, s in self._found.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(choices)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EcoPlugsOptionsFlow()


class EcoPlugsOptionsFlow(OptionsFlow):
    """Tune debounce and staleness."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEBOUNCE, default=opts.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE)
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                    vol.Required(
                        CONF_STALE_TIMEOUT,
                        default=opts.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
                }
            ),
        )

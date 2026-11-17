# Changelog

## Unreleased

- Support for plugs on **v1.x firmware**. They send no beacon, so Home Assistant finds
  them with the app's UDP search, polls their state every 10 s by default (the *Poll interval* option), and confirms on/off
  commands by reading the state back. Verified on one unit with scripts and in Home
  Assistant (acceptance test steps 1–7).
- `tools/ecoplugs.py` gains `ft-search`, `ft-status`, `ft-on` and `ft-off` for v1.x plugs.
- Fix: the firmware version is written to the device with the entity's own device entry, not
  the `device_registry.async_get_device` lookup Home Assistant deprecates (it stops
  working in 2027.8).
- v1.x plugs with a meter get voltage, current, power and energy-today sensors, read with
  each poll. On one unit the readings matched the app's power screen, and the sensors appeared and
  updated in Home Assistant.
- v1.x plugs get a *Clock difference* diagnostic sensor (the plug's clock minus Home
  Assistant's), a *Daylight saving time* switch and a *Random timers* switch. Every write
  reads the plug back to confirm it. The plug's clock itself can't be set.
- v1.x plugs get a *Timers* diagnostic sensor (the plug's timers and its random and vacation
  flags) and the services `add_timer`, `update_timer` and `delete_timer`, which send the
  whole timer list as the app does. `tools/ecoplugs.py ft-timers` prints the timers.
- The *Timers* sensor's state is the timers in words (`weekdays: on 07:00, off 22:30`)
  instead of a count, which is now the `count` attribute. **Configure** on a running v1.x
  plug opens a menu to add, change or delete a timer, with a day picker and time pickers.
  The timer pickers and the delete confirmation have a way back to the menu, the interval fields are number boxes that
  show their value, and the device page links to the integration.
- All of a v1.x plug's polls go out together, once per cycle. The plug lets one client write
  at a time and holds that for a few seconds after the client's last packet, so spread-out
  polls locked the vendor app out. At the default interval the app can control the plug in
  the gaps. See the README.
- Setup lists both kinds of plug. The options screen hides *Debounce* for v1.x plugs.
- Docs: [PROTOCOL_FT.md](docs/PROTOCOL_FT.md), covering the v1.x wire format and the
  app's other commands (schedules, random mode, time zone, power metering, Wi-Fi setup).

## 0.1.1 (2026-10-18)

- Fix: the Bug report issue template was invalid YAML and didn't appear on GitHub.
- Brand icon (light and dark), shown by Home Assistant 2026.3 and later.

## 0.1.0 (2026-10-18)

First release, tested on one device (see docs/COMPATIBILITY.md).

- Local switch control: state comes from the device's beacon, and commands are
  confirmed by its ACK. State is never restored.
- Checksum validation, debounce that ignores duplicate copies of a beacon, and
  staleness handling. A bounded device cache.
- Discovery-based setup, options, and redacted diagnostics.
- `tools/ecoplugs.py` CLI with `listen`, `dump`, `on` and `off`.
- Docs: protocol, reverse-engineering notes, live-test evidence, the HA acceptance
  test, and a compatibility list.

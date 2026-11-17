# Eco Plugs (Local) for Home Assistant

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ltsch&repository=ha-eco-plugs-local&category=integration)

Local control of Wi-Fi switches, plugs and timers managed by the **Eco Plugs** (KAB)
app, such as DEWENWILS timer boxes. There's no cloud, no Alexa, and no vendor
account involved. Home Assistant switches the device with the same UDP command the
official app uses, then waits for the device to confirm. (These docs call any such
device a "plug".)

Two kinds of plug are supported, and Home Assistant finds both automatically:

- **Current firmware:** the plug broadcasts a beacon every ~2 s, and Home Assistant
  reads the relay state from it.
- **v1.x firmware** (the app shows a `1.x` version): the plug sends nothing on its
  own, so Home Assistant asks it for its state every 10 s by default.

It's for devices set up in the **"ECO Plugs"** app. **This is an early release, tested
on one device of each kind.** Please report yours (see
[Testing and feedback](#testing-and-feedback)).

## Why

With cloud or Alexa control, Home Assistant only knows the state it last *asked
for*. After a power cut, that guess can be wrong, and nothing notices. For the well
pump that motivated this project, that meant days without water. This integration
only reports what the plug itself says:

- **No restored or guessed state.** After a restart or an outage, the switch is
  *unavailable* until the plug reports in.
- **Confirmed commands.** On/off succeeds only when the plug replies with the
  requested state. Otherwise the service call fails, so automations can react.
- **Fast updates.** Changes made from the app or the plug's button show up on the
  next beacon (about 2 s) for current-firmware plugs. A v1.x plug is polled, so
  changes show within about 10 s by default.

## Requirements

- Home Assistant 2025.2 or later, with **host networking**. HA OS and Supervised use
  it by default. Docker needs `network_mode: host`. The beacon is a broadcast, so it
  can't reach a bridged container.
- The plug and Home Assistant on the **same subnet/VLAN**, with no Wi-Fi client
  isolation between them.
- UDP **10228** and **9090** free on the HA host for current-firmware plugs. They only
  accept commands from source port 9090. v1.x plugs need UDP **8900** free instead, for
  the same reason.
- Tested with "cloud support" **off** in the app. Behavior with it on is untested.

## Install

1. Click **Open in HACS** at the top of this page. Alternatively, go to HACS → ⋮ →
   **Custom repositories** and add `https://github.com/ltsch/ha-eco-plugs-local` as
   type **Integration**. Install **Eco Plugs (Local)**, then restart Home Assistant.
2. **Settings → Devices & services → Add integration → Eco Plugs (Local)**, then pick
   a plug from the ones it hears.

To install manually instead, copy `custom_components/eco_plugs_local` into
`config/custom_components/` and restart.

## Meter sensors

A v1.x plug with a meter also gets four sensors: **voltage**, **current**, **power** and
**energy today**, read with each poll. Energy today is the plug's own total for the day, and
it starts again at midnight in Home Assistant's time zone. Home Assistant's Energy
dashboard accepts it. Power factor isn't exposed. A plug without a meter gets none of
these, and a plug that's offline when Home Assistant starts gets them unavailable until it
answers. They matched the app's power screen on the one unit tried. With the relay off,
voltage is still read, power is 0 W and current can show a few hundredths of an amp.

## Clock, daylight saving and timers

A v1.x plug runs its own timers on its own clock, and keeps running them when Home Assistant
is down.

| Entity | What it does |
|---|---|
| **Daylight saving time** (switch, configuration) | The plug's DST flag, the same as the app's checkbox. What the flag does to the plug's timers isn't known |
| **Random timers** (switch, configuration) | The app's "random 30" flag |
| **Clock difference** (sensor, diagnostic) | How many seconds the plug's clock is ahead of Home Assistant's. Near zero on the one unit tried |
| **Timers** (sensor, diagnostic) | The timers in words, for example `weekdays: on 07:00, off 22:30; weekends: on 09:00, off 23:00`, or `No timers`. The attributes hold the count, each timer's id, days, and on and off times, and whether the random and vacation flags are set |

To change the timers in the interface, open the plug's integration entry and choose
**Configure**. A running plug offers a menu: add a timer, change one, or delete one. Pick the
days and the on and off times and the plug takes them, with the checks described below.

The same changes are available as three services for automations, aimed at the **Timers** sensor. Each one reads the
plug's list, sends the whole list back with the change, reads it again to check, and puts the
old list back if the plug didn't take it.

```yaml
action: eco_plugs_local.add_timer
target: {entity_id: sensor.my_plug_timers}
data: {days: [saturday, sunday], start: "09:00:00", end: "21:30:00"}  # on at start, off at end
---
action: eco_plugs_local.update_timer     # whatever you leave out stays as it was
target: {entity_id: sensor.my_plug_timers}
data: {timer_id: 1, start: "10:00:00"}
---
action: eco_plugs_local.delete_timer
target: {entity_id: sensor.my_plug_timers}
data: {timer_id: 1}
```

The plug holds up to 12 timers. A list the plug has scrambled is never written to. Not
changeable here: vacation mode (shown, not tested), the plug's clock (the app only sets it
during first-time setup, together with the Wi-Fi details), and Wi-Fi.

A change fails with a clear message if another client, such as the vendor app, used the plug in
the last few seconds. See below, and try again.

## Using the vendor app alongside

The plug lets one client write at a time, and a client keeps that for a few seconds after
its last packet, reads included. The integration sends all of a v1.x plug's polls together
once per poll interval, so the plug is free for about half of each 10 s cycle and the app
can control it then. A command that lands just after a poll is refused (the app says "Wi-Fi
unstable"): try again. A poll interval under about 5 s keeps the plug busy and locks the
app out. To use the app for longer, disable the plug's device in Home Assistant.

## Changing your Wi-Fi

A v1.x plug can't be moved to another router from Home Assistant, and the app has no way
to do it either. The app's help says to reset the plug and set it up again in the app.
Home Assistant finds the plug by its MAC address, so the entry should keep working
afterwards. That is untested.

## Options

Go to **Configure** on the integration card.

| Option | Default | Range | Meaning |
|---|---|---|---|
| Debounce | 2 | 1–10 | Number of agreeing beacons in a row needed to accept a change you didn't command (not shown for v1.x plugs) |
| Poll interval | 10 s | 3–60 | (v1.x plugs only) Seconds between polls. All of a plug's polls go out together. Under about 5 s the vendor app can't control the plug |
| Stale timeout | 30 s | 10–600 | Time without a beacon, or a reply for v1.x plugs (never less than three polls), before the switch goes unavailable |

The entity also has an `ip_address` attribute. The device page shows the plug's
firmware.

## Troubleshooting

`tools/ecoplugs.py` is a dependency-free CLI for checking a plug. HACS doesn't
install it. Clone this repo and run it on any machine on the plug's subnet, with
Python 3.10 or newer:

```bash
python3 tools/ecoplugs.py listen      # current firmware: hears the beacon
python3 tools/ecoplugs.py ft-search   # v1.x firmware: asks, and lists what answers
```

`ft-status` reads a v1.x plug's state and clock, `ft-timers` its timers, and `ft-on`/`ft-off` switch it. Use
`ft-search --masked` for a device report. A VPN or a second network can take the default
route, and the search broadcast then never reaches the plug. Pass `--host <plug IP>`
to search it directly. `--broadcast <address>` sends the search to another broadcast
address instead, but that hasn't been verified against a real plug.

| Message | Meaning |
|---|---|
| No Eco Plugs devices were found | Broadcasts aren't reaching HA. Check the subnet, client isolation, and host networking. For a current-firmware plug, `listen` confirms it's broadcasting. A v1.x plug is silent until asked, so use `ft-search` instead of `listen`. |
| Could not listen on UDP port 10228 | Another program on the HA host has the port. |
| Cannot bind UDP source port 9090 or 8900 | Something else (an add-on, for example) is using that port. |
| No recent beacon from this plug | The plug is offline, or its beacons stopped. |
| did not acknowledge | Beacons are arriving, but the command got no reply. Check that unicast UDP between HA and the plug isn't blocked. |
| Plug replied but reports on/off | The plug refused or overrode the change. |
| The plug did not answer; is it online? | (v1.x) The plug didn't reply to a status query or command, even after a new search. |
| The plug refused the command | (v1.x) The plug replied "no permit". It only accepts commands from source port 8900, so check that nothing rewrites the port, such as NAT or a bridged container. |

For debug logs, add `custom_components.eco_plugs_local: debug` under `logger:`.
The CLI's `on`/`off` and `ft-on`/`ft-off` switch the real load. Read
[LIVE_TESTING.md](docs/LIVE_TESTING.md#safety-first) first.

## Testing and feedback

Devices known to work are listed in **[COMPATIBILITY.md](docs/COMPATIBILITY.md)**.
Only one of each kind has been tested so far, so every report helps, including "it doesn't work".

- **Device works, or doesn't:** open a
  [Device report](https://github.com/ltsch/ha-eco-plugs-local/issues/new?template=device_report.yml).
  It asks for the product name as sold, the app version, and the output of
  `tools/ecoplugs.py dump`. That output hides your MAC, IP, pairing token and names.
  Reports end up in COMPATIBILITY.md.
- **Something misbehaves in HA:** open a
  [Bug report](https://github.com/ltsch/ha-eco-plugs-local/issues/new?template=bug_report.yml)
  with the diagnostics download from the integration card. It's redacted.
- **Please don't post** MACs, IPs, pairing tokens, packet captures, or logs you
  haven't scrubbed.

## Security

The plugs' protocols have no real authentication. Any device on the same LAN can forge
beacons or replies, which can show a false state or fake a command confirmation.
A **v1.x plug also puts its own password, and the name and password of your Wi-Fi
network, in clear text** in every search reply. The one unit tested didn't check the
plug's password. This integration reads only the plug's own part and never stores or
logs the Wi-Fi details. Keep untrusted devices off that network segment, and
never expose UDP 10228, 9090 or 8900 beyond it.

## Documentation

| Doc | Contents |
|---|---|
| [PROTOCOL.md](docs/PROTOCOL.md) | Wire format (current firmware), with an evidence level for each fact |
| [PROTOCOL_FT.md](docs/PROTOCOL_FT.md) | Wire format for v1.x firmware, including the app's other commands |
| [REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) | How it was found, and how to redo it for new app builds |
| [LIVE_TESTING.md](docs/LIVE_TESTING.md) | Results from real hardware, safety, and procedures |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code structure and design decisions |
| [COMPATIBILITY.md](docs/COMPATIBILITY.md) | Devices known to work |
| [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md) | Working on this repo (people and AI agents) |

## Disclaimer

This project isn't affiliated with KAB, DEWENWILS, or Eco Plugs. Those names are
trademarks of their owners. It was built through interoperability analysis, for
local control of hardware you own, and it doesn't redistribute vendor code. **Use at
your own risk. Don't rely on it for safety-critical control.** If a plug drives
something important, alert on the *unavailable* state and on failed commands.

## License

MIT. See [LICENSE](LICENSE).

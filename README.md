# Eco Plugs (Local) for Home Assistant

Local control of **Eco Plugs** (KAB) Wi-Fi smart plugs. There's no cloud, no Alexa,
and no vendor account involved. Home Assistant reads the relay state from the beacon
the plug broadcasts on your LAN. It switches the plug with the same UDP command the
official app uses, then waits for the plug to confirm.

This is for current-firmware plugs that use the **"ECO Plugs"** Android app. See
[COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Why

With cloud or Alexa control, Home Assistant only knows the state it last *asked
for*. After a power cut, that guess can be wrong, and nothing notices. For the well
pump that motivated this project, that meant days without water. This integration
only reports what the plug itself says:

- **No restored or guessed state.** After a restart or an outage, the switch is
  *unavailable* until the plug reports in.
- **Confirmed commands.** On/off succeeds only when the plug replies with the
  requested state. Otherwise the service call fails, so automations can react.
- **Push, not polling.** Changes made from the app or the plug's button show up on
  the next beacon, about 2 s later.

## Requirements

- Home Assistant 2025.2 or later, with **host networking**. HA OS and Supervised use
  it by default. Docker needs `network_mode: host`. The beacon is a broadcast, so it
  can't reach a bridged container.
- The plug and Home Assistant on the **same subnet/VLAN**, with no Wi-Fi client
  isolation between them.
- UDP **10228** and **9090** free on the HA host. The plug only accepts commands from
  source port 9090.
- Tested with "cloud support" **off** in the app. Behavior with it on is untested.

## Install

1. HACS → ⋮ → **Custom repositories**. Add
   `https://github.com/ltsch/ha-eco-plugs-local` as type **Integration**, install
   **Eco Plugs (Local)**, then restart Home Assistant.
2. **Settings → Devices & services → Add integration → Eco Plugs (Local)**, then pick
   a plug from the ones it hears.

To install manually instead, copy `custom_components/eco_plugs_local` into
`config/custom_components/` and restart.

## Options

Go to **Configure** on the integration card.

| Option | Default | Range | Meaning |
|---|---|---|---|
| Debounce | 2 | 1–10 | Number of agreeing beacons in a row needed to accept a change you didn't command |
| Stale timeout | 30 s | 10–600 | Time without a beacon before the switch goes unavailable (checked every 5 s) |

The entity also has an `ip_address` attribute. The device page shows the plug's
firmware.

## Troubleshooting

`tools/ecoplugs.py` is a dependency-free CLI for checking a plug. HACS doesn't
install it. Clone this repo and run it on any machine on the plug's subnet:

```bash
python3 tools/ecoplugs.py listen
```

| Message | Meaning |
|---|---|
| No Eco Plugs beacons were heard | Broadcasts aren't reaching HA. Check the subnet, client isolation, and host networking. Use `listen` to confirm the plug is broadcasting. |
| Could not listen on UDP port 10228 | Another program on the HA host has the port. |
| Cannot bind UDP source port 9090 | Something else (an add-on, for example) is using port 9090. |
| No recent beacon from this plug | The plug is offline, or its beacons stopped. |
| did not acknowledge | Beacons are arriving, but the command got no reply. Check that unicast UDP between HA and the plug isn't blocked. |
| Plug replied but reports on/off | The plug refused or overrode the change. |

For debug logs, add `custom_components.eco_plugs_local: debug` under `logger:`.
The CLI's `on`/`off` switch the real load. Read
[LIVE_TESTING.md](docs/LIVE_TESTING.md#safety-first) first.

## Security

The plug's protocol has no authentication. Any device on the same LAN can forge
beacons or replies, which can show a false state or fake a command confirmation.
Keep untrusted devices off that network segment, and never expose UDP 10228 or 9090
beyond it.

## Documentation

| Doc | Contents |
|---|---|
| [PROTOCOL.md](docs/PROTOCOL.md) | Wire format, with an evidence level for each fact |
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

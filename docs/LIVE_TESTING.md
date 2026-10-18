# Live testing

This file owns what has been **observed on real hardware**. It's the source for every
**[live]** tag in [PROTOCOL.md](PROTOCOL.md). Add an entry whenever you test,
including negative results.

## Safety first

These plugs drive real loads, such as pumps and heaters.

- Get the owner's explicit approval for each live command. Agents must ask every
  time.
- **Start with a no-op:** send the state the plug is already in. An ACK proves the
  full path without touching the load.
- For a first real toggle, have someone ready to check the load and restore it.
- Never leave a command loop unattended.

## Test unit

| | |
|---|---|
| Hardware | Eco Plugs single-outlet plug (DEWENWILS), switching a well pump |
| Firmware | V2.3.0 |
| App | 2.98.260526.81124, with "cloud support" **off** for every command test |
| Network | consumer mesh Wi-Fi, with a wired Linux controller on the same subnet |

## Evidence log

Identifiers are omitted.

**2026-08-16/17: state fields.**
- Two app toggles were captured from the plug's cloud traffic. Status `0x50` followed
  both.
- The same capture held 148 beacons, about 2.05 s apart. All of them pass the
  checksum, and `0xF0` followed both toggles within one beacon.
- Two isolated "off" beacons appeared about 16 s apart during steady "on". Their
  checksums were valid and no toggle explains them, so they look like genuine plug
  reports. This is why unsolicited changes are debounced.

**2026-10-18: write path.**
- Each attempt before the final fix got nothing back
  ([REVERSE_ENGINEERING.md §5](REVERSE_ENGINEERING.md#5-why-the-first-commands-failed)).
- **ON** (0x16 then 0x17, port 1022 from 9090, fields from a fresh beacon): the plug
  replied `0x69` with state=1, the beacon read 1, and the pump was confirmed running.
- **OFF**: the reply read state=0 and the beacon read 0.
- The pairing token was unchanged from two months earlier, across power cycles.

**2026-10-18: integration code (`protocol.py` through the CLI).**
- `listen` decoded live beacons correctly.
- `off` sent to the already-off plug got `ACK: plug reports off` on the first attempt.

**Not yet observed:** the integration running in a live HA instance, cloud support
on, `0x17` alone, a skewed plug clock, other firmware, and other models.

## Procedures

Run these from a clone of the repo on the plug's subnet. Stop any HA instance using
this integration on the same host first, because both need port 9090.

```bash
python3 tools/ecoplugs.py listen --seconds 20
```

Watch the state follow app or button toggles.

```bash
python3 tools/ecoplugs.py dump
```

Gives a masked hexdump for reports.

```bash
python3 tools/ecoplugs.py off --mac aa:bb:cc:dd:ee:ff
```

A no-op while the plug is already off. Expect `ACK: plug reports off`.

To capture traffic (keep captures out of the repo):

```bash
tcpdump -i <iface> -w eco.pcap 'udp and (port 10228 or port 10229 or port 1022 or port 9090)'
```

An entry should record the date, firmware, the cloud-support setting, the exact
command, the reply, what the next beacon said, and whether anyone checked the load
physically.

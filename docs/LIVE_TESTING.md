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

**2026-10-18: Home Assistant acceptance test** (HAOS, integration from `main` at
`eacfc73`, HA VM bridged on the plug's subnet). Steps 1–6 passed. Step 7 wasn't run.
1. The plug was discovered automatically.
2. The device page showed firmware V2.3.0, and the switch matched the beacon.
3. A cloud-side toggle (Alexa) showed in HA 2 s after the beacon.
4. A no-op `turn_on` returned OK in 0.68 s, and `last_changed` didn't change.
5. `turn_off` then `turn_on` both returned OK in under 0.9 s. HA updated from the
   ACK about 2 s before the next beacon agreed, with no flicker from the stale
   beacon. The relay change was confirmed by the beacon, not physically observed.
6. After an HA Core restart, the switch showed the beacon-derived state with a fresh
   `last_changed` and `restored=False`. The *unavailable* window was shorter than
   the 0.5 s polling interval.

**Not yet observed:** cloud support on, `0x17` alone, a skewed plug clock, other
firmware, and other models.

## Procedures

### CLI

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

### Home Assistant acceptance test

Run this on a real HA instance before each release. Before you start, disable any
other automation or cloud path that controls the same plug, or it will fight the
test.

1. **Install** following the [README](../README.md#install). Then add the plug from
   **Add integration**, and on the integration card choose **⋮ → Enable debug
   logging**.
2. **Device info:** the device page shows the plug's firmware, and the switch
   matches the vendor app.
3. **Push updates:** toggle the plug from the app or its button. HA should follow
   within about two beacons.
4. **No-op command:** in **Developer tools → Actions**, send the state the plug is
   already in (`switch.turn_on` or `switch.turn_off`). It should succeed with no
   error.
5. **Real toggle:** send the opposite state. The load should change physically, and
   HA should update immediately. Restore the state the load needs.
6. **Restart:** restart HA. The switch should be *unavailable* briefly, then show the
   real state. It must never show a restored value.
7. **Staleness (optional):** unplug the plug. The switch should go *unavailable*
   after the stale timeout. Plug it back in, and the real state should return on the
   first beacon.
8. **Record:** add an evidence-log entry with a pass or fail for each step, attach
   **⋮ → Download diagnostics** (it's redacted), include any `eco_plugs_local` log
   lines, and turn debug logging off.

If step 1 finds no plug, broadcasts aren't reaching HA. See the
[README troubleshooting](../README.md#troubleshooting). A VM needs a bridged
network, not NAT.

### Writing an entry

An entry should record the date, firmware, the cloud-support setting, the exact
command, the reply, what the next beacon said, and whether anyone checked the load
physically.

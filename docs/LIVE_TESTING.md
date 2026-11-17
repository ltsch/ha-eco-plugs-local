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
| Hardware | DEWENWILS Pool Pump Timer, Outdoor Smart Wi-Fi Timer Box (40A, 120–277V, 2HP), hardwired to a well pump |
| Firmware | V2.3.0 |
| App | 2.98.260526.81124, with "cloud support" **off** for every command test |
| Network | consumer mesh Wi-Fi, with a wired Linux controller on the same subnet |

### Second test unit (v1.x)

| | |
|---|---|
| Hardware | An unbranded single-outlet plug with the model code CT-065W printed on the back, nothing attached. The app shows power data for it. |
| Firmware | V1.6.0. The app offered no update. |
| App | 2.98.260526.81124 |
| Network | wired and Wi-Fi hosts on the plug's subnet |

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
7. Inconclusive. A bridge rule on the hypervisor dropped all 25 of the plug's
   frames to the HA VM's virtual NIC for 50 s, and HA stayed available. The VM
   also has a passed-through USB Wi-Fi adapter, which probably received the same
   broadcasts. That would also mean every beacon arrived twice, which led to the
   duplicate-copy fix in 0.1.0. Not re-run.

**Not yet observed:** going *unavailable* on a live instance (step 7), cloud
support on, `0x17` alone, a skewed plug clock, other
firmware, and other models.

### v1.x plug ([PROTOCOL_FT.md](PROTOCOL_FT.md))

Identifiers are omitted. Everything used throwaway scripts that sent only the packets
named, except the Home Assistant test. The unit is the second one above. Every send was
approved first.

**2026-11-15: discovery and basics.**
- No beacon on 10228 or 10229 (a 90 s listener and host captures). The app broadcasts a
  128-byte search from port 8900 to ports 5888 and 25.
- One search burst from port 8900 got five identical 408-byte replies from plug port 25:
  version `1.6.0`, command port 80, and the plug's password in clear text.
- A status query to port 80 was answered (twice, same sequence). Port 6000 stayed silent.
  It read off, then on after the plug was switched on from the app.
- Status queries and no-op sets: an all-zero auth block is ignored, and a wrong password
  is accepted. Status works from any source port. Sets from another port get mark 3. A
  unicast search is answered on port 25 but not 5888.
- A no-op set and one real set (on to off) got mark 0, and a status query 3 s later agreed.

**2026-11-16: Home Assistant acceptance test** (HA Container 2026.10.2, Docker, host
networking, nothing attached). Steps 1–7 passed: discovery, firmware shown, following
changes from the app and the button within seconds, no-op and real toggles, restart, and
*unavailable* when unplugged and back after. Diagnostics were redacted. The log's one
warning about the integration (a deprecated registry call) was fixed afterwards.

**2026-11-16: meter and clock.**
- `0x50032` with a resistive load of about 40 W gave 56-byte replies. One-day and 30-day
  ranges were accepted with different totals, and a start of 2020 was refused (mark 51).
- About 124 V, 0.30 A and 38 W matched the app's power screen (124.4 V, 0.3 A, PF 1.00,
  38 W). Over three minutes the energy total grew at 38.2–38.5 W against 38.4 W
  instantaneous. With the relay off the sensors showed 126.2 V, 0 W and 0.02 A.
- The plug's clock read within 2 s of the host's, with its DST flag set although DST
  wasn't in effect.

**2026-11-16: timers and configuration.**
- `0x50103` (388 bytes, one every-day timer) and `0x50021` (44 bytes, vacation off, random
  on) decoded as in PROTOCOL_FT.md §5.3. The app's timer screen matched.
- While Home Assistant polled, a DST change, a no-op random write and a timer edit all got
  mark 3. The app also failed to change DST or toggle the relay.
- From an idle plug (device disabled), DST flipped and restored with the clock and offset
  unchanged, and the random record was rewritten, turned off and restored. Timer edit, add
  and delete each got mark 0 and read back exactly. My first timer edit sent one 32-byte
  entry instead of the whole 388-byte list the app sends. It corrupted the list, and a
  whole-list write repaired it exactly.

**2026-11-16: who may write.** A poller imitating the integration ran from one address
while a second address, sending from port 8900, sent no-op relay commands. They were
refused 8 times out of 8 while the poller ran, and the plug answered every poll. After the
poller stopped, writes were refused up to 2.6 s later and accepted from 4.7 s. So one client
may write at a time, and keeps that for about 3 to 4.7 s after its last packet, reads
included (PROTOCOL_FT.md §5.4).

The plug once changed state with nobody touching it. The user thinks it was the plug's own
timer, which isn't confirmed.

**Not yet observed:** the integration's own writes (the DST and random switches and the timer
services) on the real plug, which send the packets the scripts tested, a real load, another v1.x unit or firmware, whether the plug enforces
a password if one is set, how long until the switch goes *unavailable*, energy today
across midnight, what the DST flag does to timer times,
and whether the write hold is per address or per address and port.

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

For a v1.x plug, `ft-search`, `ft-status`, `ft-on` and `ft-off` do the same job. `ft-on`
and `ft-off` read the state first, then confirm by reading it back. As above, the
first real switch should be a no-op, so send the state the plug is already in. To
watch the app's own search, capture
`tcpdump -n -q 'udp port 5888 or udp port 25'` while restarting the app.

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

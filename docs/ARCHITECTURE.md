# Architecture

How the integration is built, and why. The wire formats are in [PROTOCOL.md](PROTOCOL.md)
(beacon plugs) and [PROTOCOL_FT.md](PROTOCOL_FT.md) (v1.x plugs).
Tunable values are in `const.py`.

## Layout

| Path | Role |
|---|---|
| `protocol.py` | Pure Python, no HA imports: beacon parse/validate, command build, reply parse |
| `hub.py` | One per HA instance: the beacon listener, the device cache, subscriptions, the command sender |
| `protocol_ft.py` | Pure Python, no HA imports: v1.x search, command build, reply parse |
| `hub_ft.py` | One per HA instance: the v1.x socket, device cache, polling and confirmed commands |
| `switch.py` | The beacon plug's entity and its state machine, and the platform entry point |
| `switch_ft.py` | The v1.x plug's relay switch, and its DST and random-timers switches |
| `entity_ft.py` | The v1.x device description, and the base for entities fed by the plug's clock or timer reads |
| `poll_ft.py` | The v1.x burst scheduler: a plug's polls go out together, once per cycle |
| `sensor.py` | The v1.x plug's meter, clock and timers sensors, and the timer services |
| `config_flow.py` | Discovery-based setup (both families at once), and options. For a running v1.x plug the options are a menu: the settings, or add, change and delete a timer through the hub's checked timer writes |
| `diagnostics.py` | A redacted copy of the last beacon or search reply |
| `brand/` | Icons in HA's local brand-image format (original artwork, not vendor logos) |
| `tools/ecoplugs.py` | A CLI built on `protocol.py` and `protocol_ft.py`. It loads them by path, so HA's `__init__` never runs. |

## Decisions

- **No HA imports in `protocol.py`.** It stays testable on its own, and could become
  a library later.
- **One shared hub.** Beacon port 10228 and source port 9090 are fixed by the
  firmware, so they belong to the host, not to each plug. Config entries and the
  config flow reference-count the hub (`async_acquire`/`async_release`, under a
  lock). Both sockets close when the last user releases it.
- **Socket sharing.** The beacon socket allows other listeners on the host
  (`SO_REUSEADDR`/`SO_REUSEPORT`). The command socket is exclusive, so no other
  process can receive the plug's replies. It's bound on first use and kept until
  release. Replies are only accepted while a command is in flight, and only from
  that plug's beacon IP.
- **Nothing device-specific is stored.** An entry holds the MAC and device id. Each
  command is rebuilt from the latest beacon, so DHCP, token and firmware changes
  need no reconfiguration.
- **Duplicate copies are dropped.** A host on two networks (wired plus Wi-Fi, say)
  receives each broadcast twice. Copies are identified by the per-packet key and
  device clock, and dropped, so they can't satisfy the debounce on their own.
- **Bounded cache.** Beacons are unauthenticated. To keep forged MACs from growing the
  device cache, unsubscribed sightings expire and the cache is capped.
- **State is never restored or assumed.** There's no `RestoreEntity` and no
  optimistic state.

## v1.x plugs

Plugs on v1.x firmware send nothing unsolicited, so they're a separate family. A config
entry carries `family: "ft"` (beacon entries have no `family` key), and the integration
picks the hub and entity class from it.

- **One shared socket, bound to port 8900.** The plug accepts set commands only from
  that source port, so it belongs to the host. It is exclusive, and it's reference
  counted like the beacon hub's sockets.
- **Polling, one burst per cycle.** The plug sends nothing unsolicited. It also lets one
  client write at a time, and holds that for a few seconds after the client's last packet,
  reads included ([PROTOCOL_FT.md §5.4](PROTOCOL_FT.md)). Polls on separate timers would
  keep the hold for good and lock the vendor app out. So each plug has an `FtPoller`
  (`poll_ft.py`): entities register their refresh with it, and every cycle (10 s by
  default, the *Poll interval* option, 3 to 60 s) it runs them back to back, in under a
  second. Slow jobs, the timers and the clock read, run every sixth cycle. Cycles never
  overlap. There is no debounce, because no isolated false reports have been seen. The
  stale timeout is never shorter than three cycles.
- **Replies are matched by command, sequence and source address.** The plug answers
  everything twice, so a copy can never satisfy a later request.
- **The auth block stays in memory.** Each command echoes bytes from the plug's latest
  search reply, which include its password. The block is never stored, logged, or put
  in diagnostics (`FtDevice.auth` is hidden from `repr`). The Wi-Fi
  name and password in the same reply are never read. If the plug stops
  answering, the hub searches again (unicast, then broadcast), so a new IP needs no
  reconfiguration.
- **Commands are confirmed.** After a set is acknowledged (mark 0), the hub polls
  until the plug reports the requested state. Otherwise it raises `CommandError`.
- **Bounded cache.** As for beacons: configured plugs stay, other entries expire and
  the cache is capped.
- **Meter sensors.** A `DataUpdateCoordinator` reads the power command in each burst. The sensors exist
  only if the meter answers with non-zero data at setup. A plug that answers status but
  not power gets none, and an offline plug gets them unavailable until it answers. The
  startup probe makes one attempt per request, so an offline plug delays setup by seconds.
- **Clock and DST.** Every search reply carries the plug's clock and its DST flag. The
  hub tells subscribed entities about each reply (`FtClockEntity`). The slow burst jobs
  ask for one reply a minute, and these entities go unavailable after three minutes
  without one. The *Daylight saving time* switch sends the app's checkbox payload and
  confirms by reading the flag back from a search reply. The integration never writes the
  clock: the app only sets it inside the setup rewrite, which also carries the Wi-Fi details.
- **Timers and flags.** A coordinator reads the plug's timers and its random and vacation
  flags in the slow burst jobs. The hub keeps the latest read and tells subscribers, so the
  *Timers* sensor and the *Random timers* switch agree at once, including after a write.
  Setup probes once: they exist unless the plug answers status queries but not the timer
  reads.
- **Writes are checked.** Every configuration write reads first, sends, and reads back.
  A timer change always carries the whole 388-byte list, because the plug writes whatever
  it is sent over its list (a single entry once scrambled it). A list that doesn't look
  healthy (`timer_list_entries`) is never written to, and a change the plug doesn't take
  sends the previous list back. The plug refuses writes (mark 3) for a few seconds after
  another client's last packet, and the error says so.
- **One device description.** The platforms set up at the same time. Every v1.x entity uses
  `ft_device_info`, so whichever platform runs first creates the complete, named device.
- **Energy is the plug's total for the local day.** The plug only reports energy over a
  date range, and wide ranges are refused, so the request covers today in Home Assistant's
  time zone. The sensor is `total_increasing`, and Home Assistant treats the fall at
  midnight as a new cycle.

## Entity state machine

This is the beacon plug's. A v1.x plug is simpler: it's unavailable until a status query
succeeds, shows what the plug reports, and goes unavailable (forgetting its state)
after the stale timeout without an answer.

```
 unavailable ── first beacon ──▶ known
     ▲                             │ beacon agrees        → keep
     │ stale timeout               │ disagrees, in grace   → ignore
     │ (state forgotten)           │ disagrees N in a row  → flip
     └─────────────────────────────┤ command ACKed         → set, start grace
```

- **Debounce:** unsolicited changes need N agreeing beacons in a row. This filters
  the isolated reports described in [LIVE_TESTING.md](LIVE_TESTING.md).
- **ACK grace:** after a confirmed command, beacons built before the change are
  ignored.
- **Staleness:** with no beacons, the entity goes unavailable and forgets its state.
  The first beacon after a gap is accepted as-is.

## Commands (`hub.async_set_power`, beacon plugs)

Under the command lock, the hub takes the latest fresh sighting and sends the
`COMMAND_SUBTYPES`. It retries up to `COMMAND_ATTEMPTS` times. It succeeds only on a
`0x69` reply from that host carrying the requested state. Otherwise it raises
`CommandError`, so the service call fails visibly.

## Tests

- `beacon_factory.py` builds synthetic beacons. `real_beacons.py` holds real
  firmware output with the identifiers substituted. That pins the decoder
  independently of `protocol.py`.
- The golden command vector pins the wire format. See AGENTS.md rule 1.
- `conftest.py` patches the ports to ephemeral ones and removes the broadcast
  addresses, so tests never touch real plugs. `FakePlug` is a loopback endpoint that
  ACKs, stays silent, sends the wrong state, or replies from another address.
  `FakeFtPlug` does the same for v1.x, including the duplicate replies, refused
  sets, and acknowledged-but-unchanged sets. `ft_factory.py` builds synthetic replies
  with the offsets written out by hand.

## Extending

- **New entities** (dimmer, firmware sensor): subscribe to the hub the way
  `switch.py` does.
- **New message types:** add builders to `protocol.py`. Add golden vectors once a type
  is confirmed live, and document it in PROTOCOL.md.
- **Push discovery:** the hub could start `SOURCE_INTEGRATION_DISCOVERY` flows for
  unknown MACs.

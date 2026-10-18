# Architecture

How the integration is built, and why. The wire format is in [PROTOCOL.md](PROTOCOL.md).
Tunable values are in `const.py`.

## Layout

| Path | Role |
|---|---|
| `protocol.py` | Pure Python, no HA imports: beacon parse/validate, command build, reply parse |
| `hub.py` | One per HA instance: the beacon listener, the device cache, subscriptions, the command sender |
| `switch.py` | The entity and its state machine |
| `config_flow.py` | Discovery-based setup and options |
| `diagnostics.py` | A redacted copy of the last beacon |
| `tools/ecoplugs.py` | A CLI built on `protocol.py`. It loads `protocol.py` by path, so HA's `__init__` never runs. |

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
- **Bounded cache.** Beacons are unauthenticated. To keep forged MACs from growing the
  device cache, unsubscribed sightings expire and the cache is capped.
- **State is never restored or assumed.** There's no `RestoreEntity` and no
  optimistic state.

## Entity state machine

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

## Commands (`hub.async_set_power`)

Under the command lock, the hub takes the latest fresh sighting and sends the
`COMMAND_SUBTYPES`. It retries up to `COMMAND_ATTEMPTS` times. It succeeds only on a
`0x69` reply from that host carrying the requested state. Otherwise it raises
`CommandError`, so the service call fails visibly.

## Tests

- `beacon_factory.py` builds synthetic beacons. `real_beacons.py` holds real
  firmware output with the identifiers substituted. That pins the decoder
  independently of `protocol.py`.
- The golden command vector pins the wire format. See AGENTS.md rule 1.
- `conftest.py` patches both ports to ephemeral ones, so tests never touch real
  plugs. `FakePlug` is a loopback endpoint that ACKs, stays silent, sends the wrong
  state, or replies from another address.

## Extending

- **New entities** (dimmer, firmware sensor): subscribe to the hub the way
  `switch.py` does.
- **New message types:** add builders to `protocol.py`. Add golden vectors once a type
  is confirmed live, and document it in PROTOCOL.md.
- **Push discovery:** the hub could start `SOURCE_INTEGRATION_DISCOVERY` flows for
  unknown MACs.

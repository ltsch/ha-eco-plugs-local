# Agent and contributor guide

These rules apply to both AI agents and humans. [README.md](README.md) says what the
project does.

## Read first

| Doc | When |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | before touching any integration code |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | before touching `protocol.py` or `hub.py`; it defines the evidence tags |
| [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) | for new app or firmware versions, or before "simplifying" the protocol |
| [docs/LIVE_TESTING.md](docs/LIVE_TESTING.md) | before any contact with real hardware |
| [CONTRIBUTING.md](CONTRIBUTING.md) | for setup and releases |

## Rules

1. **Protocol changes need evidence.** Don't change ports, offsets, constants,
   sub-types, or the golden vectors in `tests/` without a code anchor
   (REVERSE_ENGINEERING.md §6) or a LIVE_TESTING.md entry. A plug that doesn't
   understand a command fails silently, so a wrong change can pass every test.
2. **No real device data.** That means no MACs, IPs, pairing tokens, names, SSIDs,
   packet captures, APKs, or decompiled vendor code. Use `tests/beacon_factory.py` or
   `tests/real_beacons.py`.
3. **Don't send commands to real plugs** without explicit human approval each time.
   Follow [LIVE_TESTING.md](docs/LIVE_TESTING.md#safety-first).
4. **`protocol.py` imports nothing from Home Assistant.**
5. **Never restore or assume state.**
6. **State each fact once,** in the doc that owns it, and link to it from everywhere
   else. Update that doc in the same change. Keep text short.

## Ask a human before

- sending any packet to a real plug, even `listen` or `dump`, if you're unsure
  whose network you're on;
- changing a protocol constant or a golden vector;
- committing, pushing, tagging, or publishing a release;
- adding a dependency or any network call;
- handling anything that looks like real device data.

## Verify

Setup is in [CONTRIBUTING.md](CONTRIBUTING.md#setup).

```bash
ruff check . && ruff format --check . && pytest -q
```

CI runs the same checks, plus hassfest and HACS validation. `main` is protected:
changes land through a pull request with passing checks (squash merge only).

## Done means

- The checks pass, and new behavior has a test.
- Every doc that owns a changed fact is updated, and `CHANGELOG.md` has an entry under
  *Unreleased*.
- The diff contains no real device identifiers.
- You claim live verification only if there's a LIVE_TESTING.md entry for it.

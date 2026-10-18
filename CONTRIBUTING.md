# Contributing

The rules are in [AGENTS.md](AGENTS.md) and apply to everyone.

## Setup

You need Python 3.13.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
```

Then run the checks from [AGENTS.md](AGENTS.md#verify) inside the venv. To try the
integration in Home Assistant, symlink `custom_components/eco_plugs_local` into a dev
instance's `config/custom_components/`.

## Ways to help

- **Device reports.** These are the most useful contribution. Use the issue template.
- **Open protocol questions:** [PROTOCOL.md §9](docs/PROTOCOL.md#9-open-questions).
- **Features:** [ARCHITECTURE.md](docs/ARCHITECTURE.md#extending).

## Releases

1. Pass the [Home Assistant acceptance test](docs/LIVE_TESTING.md#home-assistant-acceptance-test)
   and log the result.
2. Bump `version` in `custom_components/eco_plugs_local/manifest.json`.
3. Move the *Unreleased* entries in `CHANGELOG.md` under the new version.
4. Tag `vX.Y.Z` and publish a GitHub release. HACS installs from releases.

## Legal

This project is for interoperability with hardware you own. Describe what the vendor
code does. Don't copy it.

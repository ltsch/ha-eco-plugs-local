# Changelog

## Unreleased

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

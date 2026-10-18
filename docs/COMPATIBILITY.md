# Compatibility

To add a result, open a **Device report** issue and include the output of
`tools/ecoplugs.py dump`.

| Hardware | Firmware | App | Beacon | Command | Cloud support | Date |
|---|---|---|---|---|---|---|
| Eco Plugs single-outlet plug (DEWENWILS) | V2.3.0 | 2.98.260526.81124 | ✅ | ✅ ACK + physical | off | 2026-10-18 |

**Likely compatible:** plugs managed by the "ECO Plugs" app whose beacon matches
[PROTOCOL.md §2.1](PROTOCOL.md#21-validation-live). The vendor SDK
(`com.kab.KabNetManager`) is shared across white-label brands.

**Probably not compatible:** older units handled by the community `ecoplug` component.
Forum reports say they use a different protocol. If `tools/ecoplugs.py listen` hears
nothing from your plug, yours is probably one of these.

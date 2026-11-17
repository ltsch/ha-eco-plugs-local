# Compatibility

This is the single list of devices people have tested. Add yours by opening a
**[Device report](https://github.com/ltsch/ha-eco-plugs-local/issues/new?template=device_report.yml)**.
A maintainer adds a row here and links the report.

**Status:**
- ✅ works: discovered, state tracks, commands ACKed.
- ⚠️ partial: some of that works. The notes say what.
- ❌ doesn't work.

| Device (as sold) | Firmware | App | Status | Notes | Report |
|---|---|---|---|---|---|
| DEWENWILS Pool Pump Timer, Outdoor Smart Wi-Fi Timer Box (40A, 120–277V, 2HP) | V2.3.0 | 2.98.260526.81124 | ✅ | Hardwired, switching a well pump. HA acceptance test passed. Cloud support was off. | [LIVE_TESTING](LIVE_TESTING.md#evidence-log) |
| Unbranded single-outlet plug, model code CT-065W on the back | V1.6.0 | 2.98.260526.81124 | ✅ | Nothing attached, so the relay was heard and seen but no load was switched. HA acceptance test steps 1–7 passed. These plugs have no beacon, so they use the [v1.x protocol](PROTOCOL_FT.md) and are polled every 10 s by default. The vendor app can control the plug between polls, not during them (see "Using the vendor app alongside" in the README). DST, the random flag and timers can be changed from Home Assistant (the writes were tested with scripts, not yet through the integration). Meter sensors (voltage, current, power, energy today) read the same as the app's power screen, and appear and update in Home Assistant. | [LIVE_TESTING](LIVE_TESTING.md#v1x-plug-protocol_ftmd) |

## Which devices might work

- **Probably:** anything set up in the **"ECO Plugs"** app (`com.kab.unlimit`),
  whatever the brand on the box. The vendor SDK is shared by white-label brands
  such as DEWENWILS. A quick check: `tools/ecoplugs.py listen` hears the device.
- **Probably:** plugs on **v1.x firmware** that the app shows with a `1.x` version.
  They don't send a beacon, so `tools/ecoplugs.py listen` hears nothing. The
  integration finds them by searching, and `tools/ecoplugs.py ft-search` does the same.
  Only one unit has been tried.
- **Probably not:** older units supported by the community `ecoplug` component.
  Forum reports say they use a different protocol.

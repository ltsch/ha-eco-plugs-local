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

## Which devices might work

- **Probably:** anything set up in the **"ECO Plugs"** app (`com.kab.unlimit`),
  whatever the brand on the box. The vendor SDK is shared by white-label brands
  such as DEWENWILS. A quick check: `tools/ecoplugs.py listen` hears the device.
- **Probably not:** older units supported by the community `ecoplug` component.
  Forum reports say they use a different protocol.

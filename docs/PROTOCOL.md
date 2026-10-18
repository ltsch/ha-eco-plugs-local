# Eco Plugs local protocol

This is the reference that `custom_components/eco_plugs_local/protocol.py` implements.
It covers current-firmware Eco Plugs (KAB) plugs and the "ECO Plugs" Android app
(`com.kab.unlimit`). It was derived from app **2.98.260526.81124** and plug firmware
**V2.3.0**.

**Evidence tags:**

- **[live]**: confirmed on a real plug. The record is in [LIVE_TESTING.md](LIVE_TESTING.md).
- **[code]**: read from the app's smali or native code, but not exercised on a plug.
- **[inferred]**: plausible, but not confirmed.

App class and field names (`$d`, `a.f`, ...) are R8-obfuscated and will change between
builds. See [REVERSE_ENGINEERING.md §6](REVERSE_ENGINEERING.md#6-re-deriving-for-a-new-app-build)
for stable anchors.

All integers are little-endian. The app reads them as signed int32. `protocol.py`
reads them unsigned (the bits are identical), except Key. Strings are NUL-padded and
fixed-width. The obfuscation is XOR with keys that travel in the clear: it isn't
cryptography, and nothing is authenticated.

## 1. Transport

Plain UDP. **[live]**

| Flow | From | To | Size |
|---|---|---|---|
| Beacon, ~every 2 s | plug:10229 | 255.255.255.255:**10228** | 272 |
| Command | controller:**9090** | plug:**command port** (beacon `0x4C`, 1022) | 152 |
| Command reply | plug:1022 | controller:9090 | 152 |
| Cloud heartbeat | plug:1022 | cloud host:port (from the beacon) | 152 |
| Clock sync (§6) **[code]** | app:10228 | plug:10229 | 36 |

- **Source port 9090 is required.** The plug ignores commands from any other source
  port, silently. **[live]**
- Port 10229 is the beacon's *source*. Commands sent there are silently ignored. **[live]**

## 2. Beacon

The app's discovery thread (`KabNetManager/a$g`) binds 10228 and parses each datagram
with `KabNetDefine$d.a()`.

### 2.1 Validation **[live]**

1. Raw bytes `0x05..0x08` == `55 aa 55 aa`. Raw bytes `0x0A..0x12` == `"ECO Plugs"`.
2. De-obfuscate (§8.1).
3. `uint32 @ 0x10C` == the byte sum of the decoded bytes `0x14..0x10B` (native `CPPCheckSum`).

### 2.2 Fields

The offsets are the parser's literal constants **[code]**. The meanings are **[live]**
unless tagged otherwise. **Bold** rows are echoed into commands or read by the
integration.

| Offset | Size | `$d` | Meaning |
|---|---|---|---|
| `0x14` | 4 | `c` | constant 2002 |
| `0x18` | 4 | `d` | **firmware**: 230 is shown as `V2.3.0` (`v/100`, `v%100/10`, `v%10`) |
| `0x1C` | 4 | `e` | plug clock, unix seconds UTC (§6) |
| `0x20` | 4 | `f` | MAC-derived |
| `0x24` | 4 | `g` | **EcoID** (numeric device id) |
| `0x28` | 4 | `h` | MAC-derived. It equals EcoID on the test unit, but the app uses `0x24`. |
| `0x2C` | 16 | `i` | **device id string**, e.g. `ECO-1A2B3C4D` |
| `0x3C` | 16 | `j` | the plug's IP, as ASCII |
| `0x4C` | 4 | `k` | **command port** |
| `0x50` | 64 | `l` | **cloud host name** |
| `0x90` | 6 | `m` | IPv4 as raw bytes (formatted from 4): the cloud host's address |
| `0x94` | 6 | `n` | IPv4 as raw bytes; zero on the test unit **[code]** |
| `0x98` | 12 | `o` | **pairing token**. Stable for months on the test unit. |
| `0xA4` | 32 | `p` | **short id string** |
| `0xC4` | 32 | `q` | **friendly name** (UTF-8) |
| `0xE4` | 8 | `r` | **MAC** (first 6 bytes) |
| `0xEC` | 4 | `s` | per-packet XOR key (§8.1) |
| `0xF0` | 4 | `t` | **relay state**: 1 = on |
| `0xF4` | 4 | `u` | **dimmer value**: 0 on an on/off plug |
| `0xF8`–`0x100` | 4 each | `v`–`x` | unknown; zero |
| `0x104` | 4 | `y` | **cloud port** |
| `0x108` | 4 | `z` | **Key** (signed) |
| `0x10C` | 4 | `A` | checksum (§2.1) |

Every command field comes from the latest beacon, so a controller stores no secrets.

## 3. Set-power command

The command is serialized by `KabNetDefine$j.m()` and filled in by
`KabNetManager/a.f()`. Every field below is **[live]** unless tagged otherwise.

| Offset | Size | Value |
|---|---|---|
| `0x00` | 4 | **TimeMark** nonce, which is also the obfuscation key. Unix seconds work [live]; the app uses ms truncated to 32 bits (§7) |
| `0x04` | 4 | routing sub-type (§7) |
| `0x0C` | 4 | EcoID (beacon `0x24`) |
| `0x10` | 12 | pairing token (beacon `0x98`) |
| `0x24` | ≤28 | short id (beacon `0xA4`) |
| `0x40` | 4 | Key (beacon `0x108`) |
| `0x44` | 4 | constant `0x12345678` |
| `0x4C` | 4 | message type `0x6A` |
| `0x50` | 4 | desired state, 1/0 (`ds_OutState`) |
| `0x54` | 4 | dimmer value (`ds_DimmableValue`) **[code]** |
| rest | | 0 |

Fill `0x00..0x47` first, obfuscate `0x10..0x47` (§8.2), then write `0x4C` onward
(plaintext). That's the order `m()` uses. Each of these fields was required: see
[REVERSE_ENGINEERING.md §5](REVERSE_ENGINEERING.md#5-why-the-first-commands-failed).

## 4. Status packet (`0x69`)

The plug sends these as cloud heartbeats, and to the controller as the **reply to a
command**. The reply carries the new state. **[live]** They're parsed by
`KabNetDefine$j.a()`.

| Offset | Size | Meaning |
|---|---|---|
| `0x00` | 4 | nonce, used as the §8.3 key |
| `0x04` | 4 | sub-type |
| `0x0C` | 4 | device id |
| `0x10`–`0x2B`, `0x38`–`0x47` | | obfuscated (§8.3). `0x40` decodes to Key; `0x44` holds a counter, or `0x12345678` during events. |
| `0x4C` | 4 | `0x69` |
| `0x50` | 4 | **relay state** |
| `0x60` | 4 | plug clock |
| `0x64` | 4 | timezone offset, in seconds |
| `0x68` | 1 | constant 1. This is not state. |
| `0x6C` | 32 | friendly name |

## 5. Other message types **[code]**

These are not implemented. The payload offsets come from `$j.m()`.

| Type | App inputs | Payload | Purpose [inferred] |
|---|---|---|---|
| `0x69` | n/a | header only | status, which is also sendable |
| `0x6A` | `ds_OutState`, `ds_DimmableValue` | `0x50`, `0x54` | set power (§3) |
| `0x6D`–`0x6F` | `OneTask` | from `0x54` | schedule entries |
| `0x70` | `$j.D` | byte at `0x51` | unknown |
| `0x73` | `NewName` | `0x6C` | rename |
| `0x75` | `DstSetting` | byte at `0x68` | daylight saving |
| `0x76` | `TimeZoneOff`, `DeviceName`, `WiFiSSID`, `WiFiPass`, `DstSetting` | `0x50`/`0x54`/`0x74`/`0x94`/`0xD4`, **0xE4-byte** packet | Wi-Fi provisioning |
| `0x78`, `0x79` | `sunriseDate`, `sunsetDate` | from `0x50`, **0x330-byte** packet | sunrise/sunset schedules |
| `0x83` | `begin`, `end` | shorts at `0x60`, `0x62` | time window |

The app also hard-codes `192.168.5.1`, which is probably the plug's setup-mode
address. [inferred]

## 6. Clock sync **[code]**

On every valid beacon, if `|phone UTC − beacon 0x1C| > 30 s`, the app sends 36 bytes
to the beacon's source address from its 10228 socket:

`"ECO Plugs   "` (12 bytes) · rand · rand · now · rand · now · rand

That's six uint32s at `0x0C`–`0x20`. Each random value is in `0..65534`, and `now` is
unix seconds UTC. This isn't implemented. Commands worked with the clock already
correct; behaviour with a skewed clock is untested.

## 7. How the app sends a command **[code]**

`MainActivity$i2.onClick` calls `a.e(ecoId, 0x6A, bundle)`:

- It sets TimeMark = `(int) System.currentTimeMillis()`.
- It records a retry entry, which `a$a` replays with the **same** TimeMark at ticks
  2, 4, 6, 10, 16, 26 and 42, giving up at 50. The tick length wasn't traced.

For each send, `a.f()` builds the `$j` (§3) and queues it:

- **`0x17` × 4** to the LAN endpoint (the beacon's source IP : command port).
- **`0x16` × 2** to a second endpoint, which LAN discovery leaves as `""`:0. On a
  LAN, nothing is sent.
- **`0x66` × 1** to the cloud host/port from the beacon, when `$c.k > 5 && $c.n > 10`.
  `k` counts sends since the last beacon from the plug; `n` counts sends since the
  device record was created.

A sender thread drains the queue from the 9090 socket.

The integration sends `0x16` then `0x17`, both to the LAN endpoint, and resends until
it gets an ACK (`const.py`). That combination is **[live]**.

## 8. Obfuscation

The routines live in `libnative-lib.so`, JNI class `com.kab.KabNetManager.KabNetDefine`.
Each one is its own inverse.

### 8.1 Beacon: `CPPParseBytesBroadcastEncryption` **[live]**

```python
key = raw[0xEC:0xF0]
for g, rel in enumerate(range(0, 0xF8, 4)):  # bytes 0x14..0x10B
    pos = 0x14 + rel
    if pos == 0xEC:
        continue  # the key itself
    out[pos + 0] = raw[pos + 0] ^ key[0] ^ (2 * g & 0xFF)
    out[pos + 1] = raw[pos + 1] ^ key[1] ^ (rel & 0xFF)
    out[pos + 2] = raw[pos + 2] ^ key[2] ^ (3 * g & 0xFF)
    out[pos + 3] = raw[pos + 3] ^ key[3] ^ (g & 0xFF)
```

### 8.2 Command: `CPPtoBytesEncryption` **[live]**

```python
k = buf[0:4]
for g in range(14):  # bytes 0x10..0x47
    for i, mult in enumerate((9, 13, 17, 19)):
        buf[0x10 + 4 * g + i] ^= k[i] ^ (g * mult & 0xFF)
```

### 8.3 Status: `CPPParseBytesEncryption` **[live]** (on heartbeats)

```python
k = buf[0:4]
for g in (*range(0, 7), *range(10, 14)):  # 0x10..0x2B, 0x38..0x47
    for i, mult in enumerate((18, 8, 9, 13)):
        buf[0x10 + 4 * g + i] ^= k[i] ^ (g * mult & 0xFF)
```

The integration doesn't need this, because the reply's state at `0x50` is plaintext.

## 9. Open questions

Record results in [LIVE_TESTING.md](LIVE_TESTING.md).

1. Is `0x17` alone enough, the way the app sends it on a LAN? Try
   `--subtypes 0x17` as a no-op.
2. Do local commands work with "cloud support" on?
3. How do other firmware versions and models behave, such as dimmers and
   multi-outlet units?
4. Are any of the §5 message types or the §6 clock sync ever needed?
5. What causes the rare isolated "off" beacons with valid checksums? See
   [LIVE_TESTING.md](LIVE_TESTING.md).

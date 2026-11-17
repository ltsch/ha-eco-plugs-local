# How the protocol was reverse-engineered

This covers how each piece of [PROTOCOL.md](PROTOCOL.md) was found, what failed, and
how to redo the work for a new app build. Read it before you "simplify" protocol
code. Most shortcuts were tried first, and they failed.

## 1. Goal

Read the plug's real relay state, and send commands it confirms, without the cloud.
The motivation is in the [README](../README.md#why). The app already worked with
cloud support off, so a LAN protocol had to exist.

## 2. Tools

- `apktool d` to decode the APK into smali.
- `objdump -d` on `lib/x86_64/libnative-lib.so`. The x86_64 build is the easiest to
  hand-trace. Each routine was ported to Python by hand.
- `tcpdump` and scapy for captures.
- In phase 1 only: a scoped ARP spoof of the plug, to see its cloud traffic.

Nothing vendor-derived or captured is committed. See [AGENTS.md](../AGENTS.md#rules).

## 3. Phase 1: state from cloud traffic

1. **Finding the plug.** It has an "ECO Plugs Enterprise" MAC OUI, and its last 4 MAC
   octets match the `ECO-XXXXXXXX` id shown in the app.
2. **Watching from the wrong place.** A wired host never sees Wi-Fi clients' unicast
   traffic. Only broadcasts leak through, and one of those was the beacon.
3. **The wrong tool.** mitmproxy caught nothing, because the protocol is UDP, not TLS.
4. **ARP-spoofing the plug alone** exposed its 152-byte cloud packets. Two app toggles
   were captured with timestamps.
5. **`KabNetDefine$j.a()`** gave the field offsets, which put state at `0x50`.
   `CPPParseBytesEncryption` decodes a counter window that is not state.

Red herrings: the lone byte read at `0x68` (it's a constant), and an "incrementing"
region that was an artifact of the obfuscation.

## 4. Phase 2: the beacon and the write path

- **The beacon carries everything.** Once `CPPParseBytesBroadcastEncryption` was
  ported, `0xF0` tracked the toggles. It's local, broadcast, and needs no spoofing.
- **Take offsets from the parser's `const/16` literals**, not from hexdumps. An early
  table was shifted by about 8 bytes, and the `CPPCheckSum` validation was missed
  until `$d.a()` was read in full.
- **Use `grep -F` for smali class names.** `$` is a regex metacharacter, and plain
  `grep` silently missed the callers that mattered.
- **The send path**, once traced:

```
MainActivity$i2.onClick → a.e() [TimeMark, retries] → a.f() [builds $j, queues per endpoint]
  → $j.m() → CPPtoBytesEncryption → sender thread on socket 9090 (a.c)
a$g.run() [10228 socket] → $d.a() [validate + parse] → device record $c (feeds a.f)
```

## 5. Why the first commands failed

Every failure was silent: no reply, no relay change, and no ICMP error. The plug only
answered once **all** of these were fixed:

| Mistake | Fix | Evidence |
|---|---|---|
| Sent to 10229 | Send to the beacon's command port (1022) | `$d.k` @ `0x4C` |
| Ephemeral source port | Bind 9090 | `a.c()`: `0x2382` |
| Sub-type `0x04` = 0 | `0x16`/`0x17` | `a.f()` |
| No pairing token | Beacon `0x98` → command `0x10` | `$c.J` ← `$d.o` |
| Header copied from an old capture | EcoID, short id and Key from the live beacon | `$c.H/O/P` ← `$d.g/p/z` |
| Counter at `0x44` | Constant `0x12345678` | `a.f()` |

**Lesson:** trace the app's real code path end to end before you test live. A silent
UDP endpoint gives you no signal to iterate on.

## 6. Re-deriving for a new app build

| To find | Search for |
|---|---|
| Native routines | `nm -D libnative-lib.so`, then `Java_com_kab_KabNetManager_KabNetDefine_CPP*` |
| Beacon parser | the caller of `CPPCheckSum` + `CPPParseBytesBroadcastEncryption`; `"ECO Plugs"` |
| Discovery thread | `"LANSerarchDev OK"` (sic), `0x27f4` |
| Command builder | `"SendCmd2 EcoID="`, `0x12345678` |
| Entry point and retries | the caller of the builder with `currentTimeMillis` |
| Command socket | `0x2382` |

1. Diff the three native `CPP*` routines against the previous build. If they're
   unchanged, PROTOCOL.md §8 holds.
2. Diff the beacon parser's offset literals against PROTOCOL.md §2.2.
3. Diff the `$c` → `$j` mapping and the sub-type constants against §3 and §7.
4. Run `tools/ecoplugs.py dump` and check the decoded values against your reading.
5. Test live by following [LIVE_TESTING.md](LIVE_TESTING.md#safety-first). Update
   PROTOCOL.md, LIVE_TESTING.md and COMPATIBILITY.md.

## 7. Dead ends

- **Loading the real `.so` on glibc** through a fake JNIEnv fails, because bionic's
  `LIBC` symbol versioning blocks it. If you need bit-exact native output, use an
  Android emulator or an NDK harness.
- **Sniffing the app's own LAN command.** Mesh nodes switch client-to-client traffic
  locally, so a third host never sees it.
- **Cloud control.** It works, but the cloud dependency is exactly what this project
  removes.

## 8. The v1.x ("Further") stack

A plug on v1.x firmware never sent the beacon, so the integration couldn't find it. The
APK holds a second, older stack for such devices, in `com.further.net`. Its protocol is
in [PROTOCOL_FT.md](PROTOCOL_FT.md).

- **How it was found.** Listening on 10228 and 10229 heard nothing. A capture while
  the phone app restarted showed the phone broadcasting 128-byte datagrams to ports
  5888 and 25. Searching the dex for those numbers (`SMARTCTRL_BROADCAST_PORT`,
  `SMARTCTRL_BROADCAST_PORT2`) led to the stack.
- **Tools.** `apktool d -r` (smali only), plus a small throwaway dex reader for
  `static final` constants. Nothing decoded is committed.
- **Take layouts from `toBytes()` and `ParseBytes()`.** The same class holds both,
  and they can disagree. The request's `ParseBytes` reads the serial at a different
  offset from the one `toBytes` writes, and `toBytes` is what goes on the wire.
- **The send path.** The app queues a packet containing the head, the whole device
  record and the payload. The sender thread re-reads it and sends only the head and
  the payload. A hexdump of the queue would mislead you.

| To find | Search for |
|---|---|
| Ports | `FtSmartDefine`: `SMARTCTRL_BROADCAST_PORT` (5888), `_PORT2` (25), `SMARTCTRL_DEFAULT_PORT` (6000) |
| Search request | `FtNetManager.internalSerarchDev` (sic), `FtSmartDefine$DvsSearchRequest.toBytes`, the 8900 bind (`0x22c4`) |
| Search reply | `FtSmartDefine$DvsSearchRespond.ParseBytes`, `FtNetManager$HandleBroadcastThread.run` (the `0x1770` port check) |
| Command head | `FtSmartDefine$SmartCtrlHead.toBytes`, `FtNetManager.sendCmd`, `FtNetManager$SendCMDThread.run` |
| Switch commands | `0x50016` in `MainActivity$i2`, `0x50017` in `MainActivity$r2`, `FtSmartDefine$SwitchStatus` |
| Other commands | the `CMD_*` constants in `FtSmartDefine` |

To redo this for a new app build, diff the same offsets against PROTOCOL_FT.md, then
test with the safety rules in [LIVE_TESTING.md](LIVE_TESTING.md#safety-first). The plug
silently ignores a malformed command, so check the packet by a status query before
trying a set.

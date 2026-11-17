# Eco Plugs v1.x ("Further") local protocol

This is the reference that `custom_components/eco_plugs_local/protocol_ft.py`
implements. It covers plugs on **v1.x firmware**, which don't send the beacon described
in [PROTOCOL.md](PROTOCOL.md). The same "ECO Plugs" Android app (`com.kab.unlimit`)
contains both stacks. This one lives in `com.further.net`, and its class names are
prefixed `Ft`. It was derived from app **2.98.260526.81124** and plug firmware **V1.6.0**.

**Evidence tags:**

- **[live]**: confirmed on a real plug. The record is in [LIVE_TESTING.md](LIVE_TESTING.md).
- **[code]**: read from the app's smali, but not exercised on a plug.
- **[inferred]**: plausible, but not confirmed.

Class and field names are R8-obfuscated in places and may change between builds. See
[REVERSE_ENGINEERING.md §8](REVERSE_ENGINEERING.md#8-the-v1x-further-stack) for stable
anchors. All integers are little-endian. Strings are NUL-padded and fixed-width. There
is no obfuscation and no cryptography.

## 1. Transport

Plain UDP. **[live]**

| Flow | From | To | Size |
|---|---|---|---|
| Search request | controller:**8900** | 255.255.255.255:**5888** and `:25`, or one plug's `:25` | 128 |
| Search reply | plug:25 | controller:8900 | 408 |
| Command | controller:8900 | plug:**command port** (search reply `0x194`, 80 on the test unit) | 128 + N |
| Command reply | plug:80 | the command's source port | 128 + N |

- **The plug sends every reply twice**, byte for byte identical. Match replies by
  command and sequence (§3), and drop the copy. **[live]**
- **Set commands need source port 8900.** From any other port the plug replies with
  mark 3 (no permit) and does nothing. Status queries work from any source port, and
  the reply goes to the sender's port. **[live]**
- A **unicast search to the plug's port 25** is answered. One to port 5888 isn't. **[live]**
- Nothing is sent unsolicited, so state must be polled. No beacon was heard on
  10228 or 10229. **[live]**
- The app's default command port is 6000, and its LAN handler drops any search reply
  whose port field isn't 6000 (`FtNetManager$HandleBroadcastThread.run`). **[code]**
  The test unit reports 80 and nothing answered on 6000. **[live]** This may be why
  v1.x plugs vanish from newer app versions. **[inferred]**

## 2. Search

The app's `FtNetManager.internalSerarchDev` (sic) builds a `DvsSearchRequest` and sends
it twice from a socket bound to 8900.

### 2.1 Request (128 bytes) **[code]**, accepted by the plug **[live]**

| Offset | Size | Field |
|---|---|---|
| `0x00` | 1 | `req_type`: 0 |
| `0x04` | 20 | `sn`: empty |
| `0x18` | 2 | local year |
| `0x1A` | 1 | local month (1–12) |
| `0x1B` | 1 | local day |
| `0x1C` | 4 | local seconds since midnight |
| rest | | 0 |

### 2.2 Reply (408 bytes, `DvsSearchRespond.ParseBytes`) **[code]**, decoded **[live]**

| Offset | Size | Meaning |
|---|---|---|
| `0x00` | 1 | `req_type`: 0 |
| `0x04` | `0x16C` | settings block (`BoxSetting`) |
| `0x04` | 6 | **firmware version**, text, e.g. `1.6.0` |
| `0x0A` | 32 | **device id**, text, e.g. `ECO-1A2B3C4D`, as shown in the app |
| `0x2A` | 32 | friendly name (alias), UTF-8 |
| `0x4A` | 32 | **device password**, in clear text |
| `0x170` | 18 | **MAC**, text `aa:bb:cc:dd:ee:ff` |
| `0x182` | 16 | the plug's IP, text |
| `0x194` | 4 | **command port** |

The rest of the settings block holds the time zone, Wi-Fi, area, cloud-server and
config records. **[code]** The Wi-Fi record (block offset `0x74`) carries the **name
and password of the Wi-Fi network the plug joined, in clear text**. **[live]**
Nothing uses it, and the integration reads only the first `0x66` bytes of the block
and never keeps the rest. It treats the source address of the datagram as the plug's
address, not the IP in the text field.

The time-zone record (block offset `0x68`, 12 bytes) holds the plug's own clock: the
year as a short with `0x1000` flagging DST, the month, the day, the seconds since
midnight as an int at `+4`, and an offset in seconds as an int at `+8`. **[code]** On the
test unit it matched the host's local date and time to within 2 s, although its DST flag
was set while DST wasn't in effect. **[live]**

## 3. Commands

Every command is one datagram: a 128-byte head (`SmartCtrlHead.toBytes`), then
`externLen` bytes of payload. **[code]**, with get, set and the failure marks **[live]**

| Offset | Size | Field |
|---|---|---|
| `0x00` | 4 | command type (§4) |
| `0x04` | 2 | `mark`: 0 in a request. **In a reply, 0 = success**, 3 = no permit |
| `0x06` | 2 | sequence, echoed in the reply. The app picks 1..`0x7FD7`. |
| `0x08` | 2 | `externLen`: payload length |
| `0x0A` | `0x66` | **auth block**: bytes `0x04..0x69` of the search reply (version, device id, alias, password) |
| `0x70` | 4 | date: 0 |
| `0x74` | 4 | send time: low 32 bits of ms, echoed in the reply |
| `0x78` | 4 | flags: 0 |
| `0x7C` | 4 | random id |

- A command with an all-zero auth block is **silently ignored**. **[live]**
- The password **isn't checked**. A status query and a no-op set with a wrong
  password, and the correct id, were accepted. **[live]** Which part of the block the
  plug does match is unknown.
- The random id was arbitrary. The app sends a static per-install id. **[live]**,
  **[code]**
- The plug's reply is the head with the same command type, plus the reply payload.

## 4. Switch commands

| Command | Type | Payload | Reply payload |
|---|---|---|---|
| Get switch status | `0x50017` | none | `[switch id, status]`: id 1, status 0 = off, 1 = on |
| Set switch | `0x50016` | `[switch id, status]` | none. Mark 0 = done. |

All **[live]**, on a single-outlet plug. A set reply carries no state, so the
integration follows it with a status query. About 3 s after a set, the state read
back was the new one. Nothing faster was measured. The app builds these in `MainActivity$i2` (set) and `$r2`
(get) with `SwitchStatus.toBytes()`. **[code]**

## 5. Other commands **[code]**

None of these has been sent to a plug. "Changes config" means the app sends them to
change the plug's stored setup. All values are from the `FtSmartDefine` structs.

| Command | Type | Notes |
|---|---|---|
| Get setting | `0x50005` | The reply is the 364-byte settings block, **including the password and the Wi-Fi credentials** |
| Post setting | `0x50004` | Overwrites the alias, password, Wi-Fi, area and clock. The app sends it only during setup (§5.2). |
| Modify time zone | `0x50015` | The app's DST checkbox. The 12-byte payload is the year `0x07DF` (2015), or `0x17DF` with the DST flag, then April 18th, 57600 s and offset 0. It flipped only the flag: the date, time and offset didn't move. **[live]** Refused with mark 3 while Home Assistant was polling the plug (§5.4). |
| Schedule get all | `0x50103` | Read-only. See §5.3. **[live]** |
| Schedule add / edit / delete | `0x50100`, `0x50101`, `0x50102` | The payload is the **whole 388-byte list**, not one entry (§5.3). An edit with a single entry corrupted the list. **[live]** |
| Get today's windows | `0x50104` | Reply: 141 bytes, up to 20 on/off windows. The app never sends it. |
| Get / set random mode | `0x50021`, `0x50020` | Get is read-only, see §5.3 **[live]**. Set `0x50020` is untested. |
| Schedule random | `0x50022` | The app's "random 30" toggle: the 44-byte random-mode record (§5.3). Accepted when the plug was otherwise idle, and the flag read back. **[live]** |
| Post astronomic table | `0x50018` | 366 daily 3-byte sunrise/sunset entries, which the app computes from your location |
| Power metering | `0x50032` | Read-only. See §5.1. |
| Get Wi-Fi hotspots | `0x50012` | The plug scans for networks. The app sends it only in setup mode (§5.2). |
| Get setting status | `0x50105` | Setup progress, with the result in the mark (§5.2) |
| Modify alias or password | `0x50013` | Can lock you out |
| Connect router | `0x50011` | The app never sends it and only logs the reply. The payload is unknown. |
| Restart, restore | `0x50002`, `0x50003` | The app never sends them. **Restore is a factory reset.** |
| Upgrade | `0x50201`, `0x50203` | Firmware flash. The app has no sender and no firmware address. **Never send.** |
| Night lamp | `0x50023` | The app sends `[1, 0]` after every discovery, with no UI. Probably a query. **[inferred]** |
| Modify IP / port | `0x50014` | No handler in the app |

**Schedules, astronomic timers and random modes are uploaded to the plug and run
there.** The app has no local scheduler, and it asks the plug for "today's windows".
**[inferred]** The app sets the plug's clock only inside Post setting, which sends the
phone's local date and time and a time-zone offset. Whether the plug keeps time by
itself, over NTP, or through the vendor cloud isn't known. A command's header carries
no clock. **[code]**

### 5.1 Power metering **[code]**

Only on plugs with a meter, which the app shows power data for. Read-only. The app sends
`0x50032` every 3 s. Never send `0x50033`, which writes the calibration. The request and
reply below were exercised on one metering plug **[live]**.

**Request payload** (56 bytes, `BoxPowerDetect.toBytes`; the rest is 0):

| Offset | Size | Field |
|---|---|---|
| `0x04` | 4 | start year |
| `0x08` | 1 | start month (1–12) |
| `0x09` | 1 | start day |
| `0x0C` | 4 | end year: today |
| `0x10` | 1 | end month: today |
| `0x11` | 1 | end day: today |

The app's default start is 30 days ago, and the user can change it.

**Reply payload** (at least 56 bytes, mark 0, `BoxPowerDetect.ParseBytes`):

| Offset | Size | Field |
|---|---|---|
| `0x00` | 1 | flag: the high word of the energy counter |
| `0x04`–`0x11` | | the request's month and day are echoed. The two year fields hold other values on the test unit, and the app ignores them. |
| `0x14` | 4 | energy total, unsigned |
| `0x18` | 4 | current, raw |
| `0x1C` | 4 | power, raw |
| `0x20` | 4 | voltage, raw |
| `0x24` | 4 | energy offset |
| `0x28` | 4 | current offset |
| `0x2C` | 4 | power offset |
| `0x30` | 4 | voltage offset |

**Scaling** (`MainActivity$d.handleMessage`). A value is 0 unless its raw value and its
offset are both above 0. Results are rounded to 2 decimals, half up.

- amps = 10⁹ / (current raw × current offset)
- volts = 10¹¹ / (voltage raw × voltage offset)
- watts = 10¹² / (power raw × power offset)
- kWh = total × 100 / energy offset, with total = flag × 2³² + total when flag is above
  0. The unit is **[inferred]** from the app's cost label.
- power factor = watts / (volts × amps), or 1.00 when watts reaches volts × amps

The raw values look like pulse periods from a metering chip, and the offsets look like
per-unit calibration. **[inferred]**

- The energy figure is a **total over the requested date range**, not a lifetime
  counter. **[live]** One day (start and end both today) and 30 days were accepted and
  gave different totals. A start of 2020-01-01 was rejected with mark 51 (`0x33`),
  so the plug limits the range. The limit isn't known.
- On the test unit with a resistive load of about 40 W, the formulas gave about 124 V,
  0.30 A and 38 W. Watts agreed with volts × amps to about 2%. **[live]** The app's
  power screen showed 124.4 V, 0.3 A, power factor 1.00 and 38 W at the same time.
  **[live]**
- Over three minutes with the load on, the energy total grew by 277–279 counts every
  30 s, which is 38.2–38.5 W on average against 38.4 W instantaneous. That confirms
  the kWh scaling. **[live]**
- With the relay off the meter still reads the line voltage, so the sensors don't depend on
  the relay state. **[live]** The test unit then showed 126.2 V, 0 W and 0.02 A.
- A plug without a meter answers mark 0 with the current, voltage and power raw values
  all 0. Mark `0x33` means the date range was rejected.

### 5.2 Wi-Fi setup **[code]**

- **Setup mode.** The user joins the plug's own hotspot (its name starts with `ECO-`) by
  hand. The plug's address there is in 192.168.4.x. **[inferred]** The app scans with
  Get Wi-Fi hotspots every 3 s (a 0x3CE-byte `WIFI_INFOR`: up to 10 items of 0x54 bytes,
  with RSSI, SSID and auth).
- **Post setting** sends the 364-byte settings block with the Wi-Fi record at `0x74`
  (`ConnetWifiInfor`, 0x82 bytes: SSID 64 bytes, password 64 bytes at `0x40`, a flag),
  the alias, area and time zone. The app then polls Get setting status every 3 s. Mark 0
  means joined, 6 still working, 3 no permission, and anything else a failure ("password
  maybe incorrect").
- **The app can't change the Wi-Fi of a plug that is already on the network.** Post
  setting has no sender outside setup mode. The app's help says that after the router,
  its name or its password changes, the plug must be reset and set up again. To reset a
  plug-in model, hold its button while plugging it in for 3–5 s, until the Wi-Fi light
  goes off. A hard-wired model has a reset button, which is pressed with a toothpick
  while the power button is held for 3–5 s. **[help page]**, untested here
- **Time.** No NTP address appears anywhere in the app. It sends the phone's local date
  and time with Post setting. How the plug keeps time isn't known, but the clock in its
  search reply (§2.2) matched the host's local time to within 2 s. **[live]**

### 5.3 Schedules and random mode

Both reads are **[live]** on one unit. For writes see §5.4.

**Get schedules** (`0x50103`, no payload) returns mark 0 and 388 bytes: a short count at
`0x00`, then 12 entries of 32 bytes from `0x04`. Unused entries are all zero.

| Offset | Size | Field |
|---|---|---|
| `0x00` | 1 | id |
| `0x01` | 1 | type |
| `0x02` | 1 | mode: 2 on the test unit's programmable timer |
| `0x03` | 1 | weekday mask: `0x7F` is every day. The bits are in the table below. |
| `0x04` | 1 | device id |
| `0x05`–`0x0B` | | after-start/end offsets: 0 here. Unused. |
| `0x0C` | 2, 1, 1 | start year, month, day |
| `0x10` | 4 | the on time, as seconds since midnight |
| `0x14` | 1 | the status at that time: 1 is on |
| `0x16` | 2, 1, 1 | end year, month, day |
| `0x1A` | 1 | the status at the end time: 0 is off |
| `0x1C` | 4 | the off time, as seconds since midnight |

**Writing timers.** Add, edit and delete (`0x50100`, `0x50101`, `0x50102`) send the
**whole list** as the payload: 388 bytes laid out as in the reply, with a short count, two
zero bytes and 12 entries from `0x04`. The app keeps its copy of the list from the last
read, changes it, and sends all of it. **[code]** Sending one 32-byte entry is wrong: the
plug wrote it over the start of its list, which destroyed the count and moved every entry
by four bytes, so the next read showed no timers. **[live]** Sending the whole list again with
`0x50101` (a count of 1, two zero bytes, the entry, and 11 empty slots) was accepted with
mark 0, and the next read matched it exactly. **[live]**

Edit, add and delete were each tested with a whole-list payload, from an otherwise idle
plug, and each read back exactly as sent. **[live]**

- **Edit** (`0x50101`) replaces the list. The app has changed the matching entry first.
- **Add** (`0x50100`) appends an entry, whose id is the lowest one not in use (0 for the first),
  and raises the count by one.
- **Delete** (`0x50102`) removes the entry, moves the later ones down, and lowers the count.

The weekday bits come from the app's day buttons (their drawable names). **[code]**
`0x01` Saturday, `0x02` Friday, `0x04` Thursday, `0x08` Wednesday, `0x10` Tuesday,
`0x20` Monday, `0x40` Sunday. The app's timer screen matched the decoded entry on the test
unit. **[live]**

The test unit held one entry: every day, on at one time and off at a later one, with both
dates set to the day the timer was made. The plug follows it on its own. **[inferred]**

**Get random mode** (`0x50021`, no payload) returns mark 0 and 44 bytes (`RDMSetting`):

| Offset | Size | Field |
|---|---|---|
| `0x00` | 1 | the vacation mode flag: 0 here |
| `0x01` | 1 | the per-schedule random flag: 1 here (the app's "random 30" button) |
| `0x04` | 12 | the vacation start, as a time-zone record: all 0 here |
| `0x10` | 12 | the vacation end: all 0 here |
| `0x1C` | 1 | action type: 0 |
| `0x1D` | 12 | random bytes, which the app generates and uploads |

What the random bytes do isn't known. The app's "random 30" suggests each timer edge moves
by up to 30 minutes. **[inferred]**

### 5.4 Configuration writes depend on what else is talking to the plug

All **[live]**, on the test unit (firmware V1.6.0), sent from source port 8900 with the
plug's own auth block:

- **While Home Assistant polled the plug every few seconds,** a DST change (`0x50015`), a
  no-op write of the random-mode record (`0x50022`) and a timer edit (`0x50101`) all got
  mark 3 (no permit) and changed nothing. The vendor app also failed to change DST or
  toggle the relay, and worked again once the Home Assistant device was disabled. Home
  Assistant itself kept switching the relay throughout.
- **With nothing else polling,** the same DST and random-mode writes got mark 0. DST
  flipped and flipped back with the clock and offset unchanged, and the random flag was
  turned off and back on. The app changed DST, the relay and a timer too.

Two more tests narrowed it down, both **[live]**:

- **Not a shared source port.** A script on another computer polled from an ephemeral port
  (so not 8900), imitating the integration's load: status every 5 s, power every 3 s, and
  timers and a search every minute. The app still couldn't toggle the relay (its other
  screens loaded).
- **Not overload.** The plug answered every one of the script's 61 polls in the first
  test, none missed, while it was refusing the app.

That leaves a permission rule, which two more passes measured. **[live]** A script on one
local address polled like the integration while a second address, sending from port 8900
like the app, sent no-op relay commands (the relay's current state):

- While the poller polled every few seconds, the writer was refused 8 times out of 8,
  although it tried every 4 s and the poller never wrote. The writer's refused attempts
  didn't take over.
- Once the poller stopped, the writer was refused 2.6 s after the poller's last packet and
  accepted 6.6 s after it. With a 15 s poll and one attempt every 12 s, writes were refused
  1.7 s and 1.9 s after a poll, and accepted 4.7 s and later.

So **one client at a time may write, and it keeps that for about 3 to 4.7 s after its last
packet of any kind, reads included.** Another client's write inside that time gets mark 3.
After it, the write is accepted and that client holds the right in turn. Reads from other
clients are always answered. Polling more often than every ~3 s keeps the right for good.
Whether the hold is per address or per address and port isn't known.

## 6. Open questions
## 6. Open questions

Record results in [LIVE_TESTING.md](LIVE_TESTING.md).

1. Which part of the auth block does the plug match? Does a plug with a password
   *set* enforce it?
2. Do other v1.x firmware versions and models behave the same, such as dimmers,
   multi-outlet units and metering plugs? Do they report command port 80?
3. Does the plug's clock survive a power cut? Do on-plug schedules keep running?
4. Is the write hold (§5.4) per address or per address and port? Does a read from another
   client during the hold extend it? Is the timeout a fixed 3 s, 4 s or something else?
5. What does the plug do on a no-op set, and is the relay touched? The test unit had
   nothing attached.
6. What is the longest date range the plug accepts? What does a one-day range return
   across midnight, and which day does the plug use for "today"? Is the 0.02 A seen with
   the relay off the plug's own draw, or a measurement offset?
7. Does the DST flag shift when schedules fire? The plug refuses to change it, so it can
   only be read. The test unit's flag was set while DST wasn't in effect, and its clock
   was right.

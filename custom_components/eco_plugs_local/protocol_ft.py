"""Eco Plugs "Further" LAN protocol (v1.x firmware): pure Python, no Home Assistant imports.

Field map, evidence and rationale: docs/PROTOCOL_FT.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import ipaddress
import random
import re
import struct
import time

SOURCE_PORT = 8900  # the plug refuses set commands from any other source port
SEARCH_PORTS = (5888, 25)
DEFAULT_COMMAND_PORT = 80

SEARCH_LENGTH = 128
SEARCH_REPLY_LENGTH = 0x198
HEAD_LENGTH = 0x80

CMD_GET_SWITCH = 0x50017
CMD_SET_SWITCH = 0x50016
CMD_GET_POWER = 0x50032
CMD_GET_TIMERS = 0x50103
CMD_GET_RANDOM = 0x50021
CMD_SET_RANDOM = 0x50022
CMD_SET_TIMEZONE = 0x50015
CMD_ADD_TIMER = 0x50100
CMD_EDIT_TIMER = 0x50101
CMD_DELETE_TIMER = 0x50102
MAX_TIMERS = 12
MARK_OK = 0
MARK_NO_PERMIT = 3
MARK_BAD_DATE = 0x33
POWER_LENGTH = 56
TIMERS_LENGTH = 388
RANDOM_LENGTH = 44
_TIMER_SIZE = 32
_TIMER_SLOTS = MAX_TIMERS
# Bit of the weekday mask for each day, from the app's day buttons.
_DAY_BITS = {
    "monday": 0x20,
    "tuesday": 0x10,
    "wednesday": 0x08,
    "thursday": 0x04,
    "friday": 0x02,
    "saturday": 0x01,
    "sunday": 0x40,
}

_AUTH_START = 0x0A
_AUTH_END = 0x70
_AUTH_LENGTH = _AUTH_END - _AUTH_START  # version 6, name/alias/password 32 each
_REPLY_SETTINGS = 4
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _cstr(buf: bytes, off: int, length: int) -> bytes:
    return buf[off : off + length].split(b"\x00", 1)[0]


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


_CLOCK_OFFSET = _REPLY_SETTINGS + 0x68  # the settings block's time-zone record
_DST_FLAG = 0x1000
# What the app's DST checkbox sends: the flag plus a fixed placeholder date and time.
_DST_PLACEHOLDER = (2015, 4, 18, 57600, 0)


@dataclass(frozen=True, slots=True)
class PlugClock:
    """The plug's own clock, from its search reply (PROTOCOL_FT.md §2.2)."""

    local: datetime  # wall-clock time, no time zone
    dst: bool
    offset_seconds: int  # relative to Taipei, as the app defines it


def parse_clock(payload: bytes) -> PlugClock | None:
    year_raw, month, day, seconds, offset = struct.unpack_from(
        "<HBBii", payload, _CLOCK_OFFSET
    )
    if not 0 <= seconds < 86400:
        return None
    try:
        local = datetime(year_raw & ~_DST_FLAG, month, day) + timedelta(seconds=seconds)
    except ValueError:
        return None
    return PlugClock(local, bool(year_raw & _DST_FLAG), offset)


@dataclass(frozen=True, slots=True)
class FtDevice:
    """A validated search reply."""

    mac: str  # "aa:bb:cc:dd:ee:ff", stable unique id
    device_id: str  # e.g. "ECO-1A2B3C4D", as shown in the app
    alias: str
    version: str  # firmware, e.g. "1.6.0"
    ip: str  # device's self-reported IP
    port: int
    clock: PlugClock | None
    # Echoed into every command (PROTOCOL_FT.md §3). It holds the device password.
    auth: bytes = field(repr=False)

    @property
    def unique_id(self) -> str:
        return self.mac.replace(":", "").lower()


def build_search(now: datetime | None = None) -> bytes:
    """Build the broadcast search request (PROTOCOL_FT.md §2)."""
    now = now or datetime.now()  # the app sends local wall-clock time
    buf = bytearray(SEARCH_LENGTH)
    struct.pack_into("<H", buf, 0x18, now.year)
    buf[0x1A] = now.month
    buf[0x1B] = now.day
    struct.pack_into("<i", buf, 0x1C, now.hour * 3600 + now.minute * 60 + now.second)
    return bytes(buf)


def parse_search_reply(payload: bytes) -> FtDevice | None:
    """Decode and validate a search reply. Returns None for anything else."""
    if len(payload) != SEARCH_REPLY_LENGTH:
        return None
    mac = _text(_cstr(payload, 0x170, 18)).lower()
    ip = _text(_cstr(payload, 0x182, 16))
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    version = _text(_cstr(payload, _REPLY_SETTINGS, 6))
    device_id = _text(_cstr(payload, _REPLY_SETTINGS + 6, 32))
    if not _MAC_RE.match(mac) or not version or not device_id:
        return None
    port = struct.unpack_from("<i", payload, 0x194)[0]
    return FtDevice(
        mac=mac,
        device_id=device_id,
        alias=_text(_cstr(payload, _REPLY_SETTINGS + 0x26, 32)),
        version=version,
        ip=ip,
        port=port if 0 < port < 65536 else DEFAULT_COMMAND_PORT,
        clock=parse_clock(payload),
        auth=payload[_REPLY_SETTINGS : _REPLY_SETTINGS + _AUTH_LENGTH],
    )


def new_sequence() -> int:
    return random.randint(1, 0x7FD7)


def build_command(
    cmd: int,
    device: FtDevice,
    payload: bytes = b"",
    sequence: int | None = None,
    rand_id: int | None = None,
    sendtime: int | None = None,
) -> bytes:
    """Build a command: a 128-byte head plus ``payload`` (PROTOCOL_FT.md §3)."""
    if sequence is None:
        sequence = new_sequence()
    if rand_id is None:
        rand_id = random.randint(1, 0x7FFFFFFF)
    if sendtime is None:
        sendtime = int(time.time() * 1000) & 0x7FFFFFFF
    buf = bytearray(HEAD_LENGTH)
    struct.pack_into("<I", buf, 0x00, cmd)
    struct.pack_into("<H", buf, 0x06, sequence)
    struct.pack_into("<H", buf, 0x08, len(payload))
    buf[_AUTH_START:_AUTH_END] = device.auth
    struct.pack_into("<i", buf, 0x74, sendtime)
    struct.pack_into("<I", buf, 0x7C, rand_id)
    return bytes(buf) + payload


def build_get_switch(device: FtDevice, sequence: int | None = None) -> bytes:
    return build_command(CMD_GET_SWITCH, device, sequence=sequence)


def build_set_switch(device: FtDevice, on: bool, sequence: int | None = None) -> bytes:
    return build_command(
        CMD_SET_SWITCH, device, bytes([1, 1 if on else 0]), sequence=sequence
    )


@dataclass(frozen=True, slots=True)
class Response:
    cmd: int
    mark: int
    sequence: int
    payload: bytes


def parse_response(data: bytes) -> Response | None:
    """Decode a command reply, or None if it is too short or inconsistent."""
    if len(data) < HEAD_LENGTH:
        return None
    cmd = struct.unpack_from("<I", data, 0)[0]
    mark, sequence, extern = struct.unpack_from("<HHH", data, 4)
    if len(data) < HEAD_LENGTH + extern:
        return None
    return Response(cmd, mark, sequence, data[HEAD_LENGTH : HEAD_LENGTH + extern])


def switch_state(response: Response) -> bool | None:
    """Relay state from a get-switch reply, or None (PROTOCOL_FT.md §4)."""
    if (
        response.cmd != CMD_GET_SWITCH
        or response.mark != MARK_OK
        or len(response.payload) != 2
        or response.payload[0] != 1
        or response.payload[1] not in (0, 1)
    ):
        return None
    return response.payload[1] == 1


def power_request_payload(day: date) -> bytes:
    """Payload asking for the energy used from ``day`` to ``day`` (PROTOCOL_FT.md §5.1)."""
    buf = bytearray(POWER_LENGTH)
    struct.pack_into("<i", buf, 0x04, day.year)
    buf[0x08] = day.month
    buf[0x09] = day.day
    struct.pack_into("<i", buf, 0x0C, day.year)
    buf[0x10] = day.month
    buf[0x11] = day.day
    return bytes(buf)


def build_get_power(device: FtDevice, day: date, sequence: int | None = None) -> bytes:
    return build_command(
        CMD_GET_POWER, device, power_request_payload(day), sequence=sequence
    )


@dataclass(frozen=True, slots=True)
class PowerReading:
    volts: float
    amps: float
    watts: float
    kwh: float  # energy over the requested date range
    metered: bool  # False when every value is zero, as on a plug without a meter


def parse_power(response: Response) -> PowerReading | None:
    """Decode a power reply, applying the app's scaling (PROTOCOL_FT.md §5.1)."""
    p = response.payload
    if (
        response.cmd != CMD_GET_POWER
        or response.mark != MARK_OK
        or len(p) < POWER_LENGTH
    ):
        return None
    high = p[0]
    total = struct.unpack_from("<I", p, 0x14)[0]
    current, power, voltage, e_off, c_off, p_off, v_off = struct.unpack_from(
        "<7i", p, 0x18
    )

    def scaled(numerator: float, raw: int, offset: int) -> float:
        return numerator / (raw * offset) if raw > 0 and offset > 0 else 0.0

    kwh = 0.0
    if total > 0 and e_off > 0:
        kwh = ((high << 32) + total if high > 0 else total) * 100 / e_off
    return PowerReading(
        volts=scaled(1e11, voltage, v_off),
        amps=scaled(1e9, current, c_off),
        watts=scaled(1e12, power, p_off),
        kwh=kwh,
        metered=any(
            v > 0 for v in (current, power, voltage, total, e_off, c_off, p_off, v_off)
        ),
    )


@dataclass(frozen=True, slots=True)
class Timer:
    """One on-plug timer: it switches on at ``start_seconds`` and off at ``end_seconds``."""

    timer_id: int
    kind: int
    mode: int
    days: tuple[str, ...]  # Monday first
    start_seconds: int  # since midnight
    start_state: int  # 1 is on
    end_seconds: int
    end_state: int  # 0 is off


@dataclass(frozen=True, slots=True)
class TimerSettings:
    timers: tuple[Timer, ...]
    random: bool  # the app's "random 30" flag
    vacation: bool


def parse_timers(response: Response) -> tuple[Timer, ...] | None:
    """Decode a get-timers reply (PROTOCOL_FT.md §5.3). Empty slots are skipped."""
    p = response.payload
    if (
        response.cmd != CMD_GET_TIMERS
        or response.mark != MARK_OK
        or len(p) < TIMERS_LENGTH
    ):
        return None
    timers = []
    for slot in range(_TIMER_SLOTS):
        offset = 4 + _TIMER_SIZE * slot
        entry = p[offset : offset + _TIMER_SIZE]
        if not any(entry):
            continue
        start = struct.unpack_from("<i", entry, 0x10)[0]
        end = struct.unpack_from("<i", entry, 0x1C)[0]
        if not (0 <= start < 86400 and 0 <= end < 86400):
            continue
        timers.append(
            Timer(
                timer_id=entry[0],
                kind=entry[1],
                mode=entry[2],
                days=tuple(d for d, bit in _DAY_BITS.items() if entry[3] & bit),
                start_seconds=start,
                start_state=entry[0x14],
                end_seconds=end,
                end_state=entry[0x1A],
            )
        )
    return tuple(timers)


@dataclass(frozen=True, slots=True)
class RandomMode:
    random: bool  # the app's "random 30" flag
    vacation: bool
    raw: bytes = field(
        repr=False
    )  # the 44 bytes as read: a write changes one flag in them


def parse_random(response: Response) -> RandomMode | None:
    """Decode a get-random-mode reply (PROTOCOL_FT.md §5.3)."""
    p = response.payload
    if (
        response.cmd != CMD_GET_RANDOM
        or response.mark != MARK_OK
        or len(p) < RANDOM_LENGTH
    ):
        return None
    return RandomMode(
        random=bool(p[1]), vacation=bool(p[0]), raw=bytes(p[:RANDOM_LENGTH])
    )


def random_payload(current: RandomMode, random: bool) -> bytes:
    """The record to write: what was read, with only the random flag changed."""
    raw = bytearray(current.raw)
    raw[1] = int(random)
    return bytes(raw)


def dst_payload(on: bool) -> bytes:
    """The app's DST checkbox payload. It sets only the DST flag (PROTOCOL_FT.md §5)."""
    year, month, day, seconds, offset = _DST_PLACEHOLDER
    return struct.pack(
        "<HBBii", year | (_DST_FLAG if on else 0), month, day, seconds, offset
    )


# --- editing the timer list. Every change sends the WHOLE list (PROTOCOL_FT.md §5.3).


def timer_list_entries(raw: bytes) -> list[bytes] | None:
    """The 32-byte entries of a healthy list, or None if it looks damaged.

    Healthy means the right length, entries packed from the first slot, and a count
    that matches. A list the plug has scrambled is never written to.
    """
    if len(raw) != TIMERS_LENGTH:
        return None
    count = struct.unpack_from("<h", raw, 0)[0]
    slots = [
        raw[4 + _TIMER_SIZE * i : 4 + _TIMER_SIZE * (i + 1)] for i in range(MAX_TIMERS)
    ]
    present = [e for e in slots if any(e)]
    if count != len(present) or slots[: len(present)] != present:
        return None
    return present


def _list_from(entries: list[bytes]) -> bytes:
    raw = bytearray(TIMERS_LENGTH)
    struct.pack_into("<h", raw, 0, len(entries))
    for i, entry in enumerate(entries):
        raw[4 + _TIMER_SIZE * i : 4 + _TIMER_SIZE * (i + 1)] = entry
    return bytes(raw)


WEEKDAYS = tuple(_DAY_BITS)  # Monday first


def _mask(days: tuple[str, ...] | list[str]) -> int:
    unknown = [d for d in days if d not in _DAY_BITS]
    if unknown or not days:
        raise ValueError(f"Days must be some of {', '.join(_DAY_BITS)}")
    return sum(_DAY_BITS[d] for d in set(days))


def _check_seconds(seconds: int) -> int:
    if not 0 <= seconds < 86400:
        raise ValueError("A time must be within one day")
    return seconds


def with_timer_added(
    raw: bytes, days: list[str], start: int, end: int, today: date
) -> bytes:
    """The list with a new timer: on at ``start``, off at ``end`` (seconds since midnight)."""
    entries = timer_list_entries(raw)
    if entries is None:
        raise ValueError("The plug's timer list looks damaged")
    if len(entries) >= MAX_TIMERS:
        raise ValueError(f"The plug holds at most {MAX_TIMERS} timers")
    used = {e[0] for e in entries}
    entry = bytearray(_TIMER_SIZE)
    entry[0] = next(i for i in range(256) if i not in used)  # the lowest unused id
    entry[2] = 2  # programmable, like the app's own timers
    entry[3] = _mask(days)
    struct.pack_into("<hBB", entry, 0x0C, today.year, today.month, today.day)
    struct.pack_into("<i", entry, 0x10, _check_seconds(start))
    entry[0x14] = 1  # on
    struct.pack_into("<hBB", entry, 0x16, today.year, today.month, today.day)
    entry[0x1A] = 0  # off
    struct.pack_into("<i", entry, 0x1C, _check_seconds(end))
    return _list_from([*entries, bytes(entry)])


def with_timer_updated(
    raw: bytes,
    timer_id: int,
    days: list[str] | None = None,
    start: int | None = None,
    end: int | None = None,
) -> bytes:
    """The list with one timer changed. What isn't given is kept."""
    entries = timer_list_entries(raw)
    if entries is None:
        raise ValueError("The plug's timer list looks damaged")
    for i, entry in enumerate(entries):
        if entry[0] == timer_id:
            changed = bytearray(entry)
            if days is not None:
                changed[3] = _mask(days)
            if start is not None:
                struct.pack_into("<i", changed, 0x10, _check_seconds(start))
            if end is not None:
                struct.pack_into("<i", changed, 0x1C, _check_seconds(end))
            entries[i] = bytes(changed)
            return _list_from(entries)
    raise ValueError(f"There is no timer {timer_id}")


def with_timer_removed(raw: bytes, timer_id: int) -> bytes:
    """The list without one timer. The later ones move down."""
    entries = timer_list_entries(raw)
    if entries is None:
        raise ValueError("The plug's timer list looks damaged")
    kept = [e for e in entries if e[0] != timer_id]
    if len(kept) == len(entries):
        raise ValueError(f"There is no timer {timer_id}")
    return _list_from(kept)


# --- describing timers in words


_DASH = "\u2013"  # an en dash, for ranges of days


def _hhmm(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}" + (f":{secs:02d}" if secs else "")


def describe_days(days: tuple[str, ...] | list[str]) -> str:
    """Words for a set of days: every day, weekdays, weekends, or runs like Mon-Thu, Sat, Sun."""
    present = [i for i, day in enumerate(WEEKDAYS) if day in days]
    names = [day[:3].title() for day in WEEKDAYS]
    if not present:
        return "no days"
    if len(present) == 7:
        return "every day"
    if present == [0, 1, 2, 3, 4]:
        return "weekdays"
    if present == [5, 6]:
        return "weekends"
    runs: list[list[int]] = []
    for i in present:
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    parts = []
    for run in runs:
        if len(run) >= 3:
            parts.append(f"{names[run[0]]}{_DASH}{names[run[-1]]}")
        else:
            parts.extend(names[i] for i in run)
    return ", ".join(parts)


def _switches(state: int) -> str:
    return {1: "on", 0: "off"}.get(state, f"state {state}")


def describe_timer(timer: Timer) -> str:
    """For example: Mon-Thu, Sat, Sun: on 08:15, off 21:45."""
    return (
        f"{describe_days(timer.days)}: {_switches(timer.start_state)} "
        f"{_hhmm(timer.start_seconds)}, {_switches(timer.end_state)} "
        f"{_hhmm(timer.end_seconds)}"
    )


def describe_timers(timers: tuple[Timer, ...], limit: int = 250) -> str:
    """All the timers in one line. Falls back to a count if it won't fit a state."""
    if not timers:
        return "No timers"
    text = "; ".join(describe_timer(t) for t in timers)
    if len(text) <= limit:
        return text
    return f"{len(timers)} timers"

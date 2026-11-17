"""Build synthetic v1.x search replies and command replies (no real device data here).

Offsets are written out by hand from docs/PROTOCOL_FT.md, independently of
``protocol_ft.py``, so they pin the decoder.
"""

from __future__ import annotations

import struct

FAKE_FT = {
    "mac": "02:00:00:00:00:02",
    "device_id": "ECO-00000002",
    "alias": "Test FT Plug",
    "version": "1.6.0",
    "ip": "192.0.2.20",
    "port": 80,
    "clock": None,  # a naive datetime to include the clock record
    "dst": False,
    "tz_offset": -46800,
    "password": "test-password-do-not-leak",
}


def make_search_reply(**over) -> bytes:
    f = {**FAKE_FT, **over}
    d = bytearray(0x198)
    for off, text in (
        (4, f["version"]),
        (4 + 6, f["device_id"]),
        (4 + 0x26, f["alias"]),
        (4 + 0x46, f["password"]),
        (0x170, f["mac"]),
        (0x182, f["ip"]),
    ):
        d[off : off + len(text)] = text.encode()
    struct.pack_into("<i", d, 0x194, f["port"])
    if f["clock"] is not None:
        c = f["clock"]
        struct.pack_into(
            "<HBBii",
            d,
            4 + 0x68,
            c.year | (0x1000 if f["dst"] else 0),
            c.month,
            c.day,
            c.hour * 3600 + c.minute * 60 + c.second,
            f["tz_offset"],
        )
    return bytes(d)


def make_response(
    cmd: int, sequence: int, mark: int = 0, payload: bytes = b""
) -> bytes:
    head = bytearray(0x80)
    struct.pack_into("<I", head, 0, cmd)
    struct.pack_into("<HHH", head, 4, mark, sequence, len(payload))
    return bytes(head) + payload


def make_power_payload(
    *,
    voltage: int = 2000,
    current: int = 1000,
    power: int = 1000,
    total: int = 3_000_000,
    high: int = 0,
    energy_offset: int = 100_000_000,
    current_offset: int = 2_000_000,
    power_offset: int = 20_000_000,
    voltage_offset: int = 500_000,
) -> bytes:
    """Defaults give 100 V, 0.5 A, 50 W and 3 kWh with the app's formulas."""
    p = bytearray(56)
    p[0] = high
    struct.pack_into("<I", p, 0x14, total)
    struct.pack_into(
        "<7i",
        p,
        0x18,
        current,
        power,
        voltage,
        energy_offset,
        current_offset,
        power_offset,
        voltage_offset,
    )
    return bytes(p)


def make_timer_entry(
    *,
    timer_id: int = 0,
    kind: int = 0,
    mode: int = 2,
    mask: int = 0x7F,
    start: int = 28800,
    start_state: int = 1,
    end: int = 73800,
    end_state: int = 0,
) -> bytes:
    """Defaults: every day, on at 08:00:00 and off at 20:30:00."""
    e = bytearray(32)
    e[0], e[1], e[2], e[3] = timer_id, kind, mode, mask
    struct.pack_into("<hBB", e, 0x0C, 2020, 1, 1)
    struct.pack_into("<i", e, 0x10, start)
    e[0x14] = start_state
    struct.pack_into("<hBB", e, 0x16, 2020, 1, 1)
    e[0x1A] = end_state
    struct.pack_into("<i", e, 0x1C, end)
    return bytes(e)


def make_timers_payload(*entries: bytes) -> bytes:
    p = bytearray(388)
    struct.pack_into("<h", p, 0, len(entries))
    for i, e in enumerate(entries):
        p[4 + 32 * i : 36 + 32 * i] = e
    return bytes(p)


def make_random_payload(*, vacation: bool = False, random: bool = True) -> bytes:
    p = bytearray(44)
    p[0], p[1] = int(vacation), int(random)
    p[0x1D:0x29] = bytes(range(1, 13))
    return bytes(p)

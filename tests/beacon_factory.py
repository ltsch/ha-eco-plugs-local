"""Build synthetic beacons/replies for tests (no real device data in this repo)."""

from __future__ import annotations

from custom_components.eco_plugs_local import protocol as P

FAKE = {
    "mac": bytes.fromhex("020000000001"),
    "device_id": b"ECO-00000001",
    "name": b"Test Plug",
    "ip": b"192.0.2.10",
    "port": 1022,
    "token": b"abcdEFGh12",
    "eco_id": 123456,
    "short_id": b"00000001",
    "key": -12345678,
    "firmware": 230,
    "device_time": 1700000000,
}


def make_beacon(on: bool = True, key: bytes = b"\x5a\xc3\x19\x7e", **over) -> bytes:
    f = {**FAKE, **over}
    d = bytearray(P.BEACON_LENGTH)
    d[5:9] = b"\x55\xaa\x55\xaa"
    d[0x0A:0x13] = b"ECO Plugs"
    d[0x14:0x18] = (2002).to_bytes(4, "little")
    d[0x18:0x1C] = f["firmware"].to_bytes(4, "little")
    d[0x1C:0x20] = f["device_time"].to_bytes(4, "little")
    d[0x24:0x28] = f["eco_id"].to_bytes(4, "little")
    d[0x28:0x2C] = f["eco_id"].to_bytes(4, "little")  # duplicated on real units
    d[0x2C : 0x2C + len(f["device_id"])] = f["device_id"]
    d[0x3C : 0x3C + len(f["ip"])] = f["ip"]
    d[0x4C:0x50] = f["port"].to_bytes(4, "little")
    d[0x98 : 0x98 + len(f["token"])] = f["token"]
    d[0xA4 : 0xA4 + len(f["short_id"])] = f["short_id"]
    d[0xC4 : 0xC4 + len(f["name"])] = f["name"]
    d[0xE4:0xEA] = f["mac"]
    d[0xEC:0xF0] = key
    d[0xF0:0xF4] = (1 if on else 0).to_bytes(4, "little")
    d[0x108:0x10C] = f["key"].to_bytes(4, "little", signed=True)
    d[0x10C:0x110] = P.beacon_checksum(bytes(d)).to_bytes(4, "little")
    return P.beacon_xor(bytes(d))


def make_reply(on: bool) -> bytes:
    r = bytearray(P.PACKET_LENGTH)
    r[0x4C:0x50] = P.MSG_STATUS.to_bytes(4, "little")
    r[0x50:0x54] = (1 if on else 0).to_bytes(4, "little")
    return bytes(r)

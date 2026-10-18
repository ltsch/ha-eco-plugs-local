"""Eco Plugs LAN protocol: pure Python, no Home Assistant imports.

Field map, evidence and rationale: docs/PROTOCOL.md (section refs below).
"""

from __future__ import annotations

from dataclasses import dataclass
import time

BEACON_LISTEN_PORT = 10228
BEACON_LENGTH = 272
COMMAND_SOURCE_PORT = 9090
DEFAULT_COMMAND_PORT = 1022
PACKET_LENGTH = 0x98  # 152

MSG_STATUS = 0x69
MSG_SET_POWER = 0x6A

# Command routing sub-types (PROTOCOL.md §7). 0x16 + 0x17 together is confirmed.
SUBTYPE_LAN = 0x17
SUBTYPE_ALT = 0x16
SUBTYPE_CLOUD = 0x66
COMMAND_SUBTYPES = (SUBTYPE_ALT, SUBTYPE_LAN)

_BEACON_MAGIC = b"\x55\xaa\x55\xaa"
_BEACON_BRAND = b"ECO Plugs"
_BEACON_WINDOW_START = 0x14
_BEACON_WINDOW_END = 0x10C  # exclusive
_BEACON_KEY_OFFSET = 0xEC
_BEACON_CHECKSUM_OFFSET = 0x10C

_COMMAND_WINDOW_START = 0x10
_COMMAND_WINDOW_GROUPS = 14  # 0x10..0x47
_COMMAND_KEYSTREAM_MULT = (9, 13, 17, 19)
_COMMAND_SENTINEL = 0x12345678


def _u32(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 4], "little", signed=False)


def _s32(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 4], "little", signed=True)


def _cstr(buf: bytes, off: int, length: int) -> bytes:
    return buf[off : off + length].split(b"\x00", 1)[0]


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def beacon_xor(payload: bytes) -> bytes:
    """Beacon de/obfuscation (PROTOCOL.md §8.1); its own inverse."""
    if len(payload) < _BEACON_WINDOW_END:
        raise ValueError("beacon too short")
    key = payload[_BEACON_KEY_OFFSET : _BEACON_KEY_OFFSET + 4]
    out = bytearray(payload)
    for group, rel in enumerate(range(0, _BEACON_WINDOW_END - _BEACON_WINDOW_START, 4)):
        pos = _BEACON_WINDOW_START + rel
        if pos == _BEACON_KEY_OFFSET:
            continue
        out[pos + 0] ^= key[0] ^ ((group * 2) & 0xFF)
        out[pos + 1] ^= key[1] ^ (rel & 0xFF)
        out[pos + 2] ^= key[2] ^ ((group * 3) & 0xFF)
        out[pos + 3] ^= key[3] ^ (group & 0xFF)
    return bytes(out)


def beacon_fingerprint(payload: bytes) -> bytes:
    """Per-packet random key plus device clock: equal only for copies of one beacon."""
    return bytes(
        payload[_BEACON_KEY_OFFSET : _BEACON_KEY_OFFSET + 4] + payload[0x1C:0x20]
    )


def beacon_checksum(decoded: bytes) -> int:
    """Vendor CPPCheckSum (PROTOCOL.md §2.1)."""
    return sum(decoded[_BEACON_WINDOW_START:_BEACON_WINDOW_END])


@dataclass(frozen=True, slots=True)
class Beacon:
    """A validated, decoded beacon."""

    mac: str  # "aa:bb:cc:dd:ee:ff", stable unique id
    device_id: str  # e.g. "ECO-1A2B3C4D", as shown in the app
    name: str  # friendly name configured in the app
    ip: str  # device's self-reported IP
    command_port: int
    is_on: bool
    device_time: int
    firmware: int
    dimmer: int
    cloud_host: str
    cloud_port: int
    # Echoed into every command (PROTOCOL.md §3):
    eco_id: int
    token: bytes
    short_id: bytes
    key: int

    @property
    def unique_id(self) -> str:
        return self.mac.replace(":", "").lower()

    @property
    def firmware_version(self) -> str:
        """Formatted the way the app shows it (KabNetManager/a$g)."""
        f = self.firmware
        return f"V{f // 100}.{f % 100 // 10}.{f % 10}"


def parse_beacon(payload: bytes) -> Beacon | None:
    """Decode and validate a beacon. Returns None for anything that isn't one."""
    if len(payload) < _BEACON_CHECKSUM_OFFSET + 4:
        return None
    if payload[5:9] != _BEACON_MAGIC or payload[0x0A : 0x0A + 9] != _BEACON_BRAND:
        return None
    d = beacon_xor(payload)
    if beacon_checksum(d) != _u32(d, _BEACON_CHECKSUM_OFFSET):
        return None
    mac_raw = d[0xE4:0xEA]
    port = _u32(d, 0x4C)
    return Beacon(
        mac=":".join(f"{b:02x}" for b in mac_raw),
        device_id=_text(_cstr(d, 0x2C, 16)),
        name=_text(_cstr(d, 0xC4, 32)),
        ip=_text(_cstr(d, 0x3C, 16)),
        command_port=port if 0 < port < 65536 else DEFAULT_COMMAND_PORT,
        is_on=_u32(d, 0xF0) != 0,
        device_time=_u32(d, 0x1C),
        firmware=_u32(d, 0x18),
        dimmer=_u32(d, 0xF4),
        cloud_host=_text(_cstr(d, 0x50, 64)),
        cloud_port=_u32(d, 0x104),
        eco_id=_u32(d, 0x24),
        token=_cstr(d, 0x98, 12),
        short_id=_cstr(d, 0xA4, 32),
        key=_s32(d, 0x108),
    )


def _command_xor(buf: bytearray) -> None:
    """Command obfuscation, in place (PROTOCOL.md §8.2); its own inverse."""
    k = buf[0:4]
    for group in range(_COMMAND_WINDOW_GROUPS):
        pos = _COMMAND_WINDOW_START + group * 4
        for i, mult in enumerate(_COMMAND_KEYSTREAM_MULT):
            buf[pos + i] ^= k[i] ^ ((group * mult) & 0xFF)


def build_set_power(
    beacon: Beacon, on: bool, subtype: int = SUBTYPE_LAN, nonce: int | None = None
) -> bytes:
    """Build a set-power command from the plug's latest beacon (PROTOCOL.md §3)."""
    if nonce is None:  # "TimeMark"; unix seconds is what was tested live
        nonce = int(time.time())
    buf = bytearray(PACKET_LENGTH)
    buf[0x00:0x04] = (nonce & 0xFFFFFFFF).to_bytes(4, "little")
    buf[0x04:0x08] = subtype.to_bytes(4, "little")
    buf[0x0C:0x10] = beacon.eco_id.to_bytes(4, "little")
    buf[0x10:0x1C] = beacon.token[:12].ljust(12, b"\x00")
    buf[0x24:0x40] = beacon.short_id[:0x1C].ljust(0x1C, b"\x00")
    buf[0x40:0x44] = beacon.key.to_bytes(4, "little", signed=True)
    buf[0x44:0x48] = _COMMAND_SENTINEL.to_bytes(4, "little")
    _command_xor(buf)
    buf[0x4C:0x50] = MSG_SET_POWER.to_bytes(4, "little")
    buf[0x50:0x54] = (1 if on else 0).to_bytes(4, "little")
    return bytes(buf)


def parse_status_reply(payload: bytes) -> bool | None:
    """Relay state from a 0x69 status reply, or None (PROTOCOL.md §4)."""
    if len(payload) < 0x54 or _u32(payload, 0x4C) != MSG_STATUS:
        return None
    return _u32(payload, 0x50) != 0

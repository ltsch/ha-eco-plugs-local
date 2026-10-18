"""Protocol tests. GOLDEN_ON_16 came from the builder that switched a real plug
(see AGENTS.md rule 1); real_beacons.py pins the decoder to real firmware output."""

from __future__ import annotations

from custom_components.eco_plugs_local import protocol as P

from .beacon_factory import FAKE, make_beacon

GOLDEN_ON_16 = (
    "7bf15365160000000000000040e201001a93300137ba051e58d9714360d6605c5fc51729"
    "6680360a7d8f052644aa24e03399dbfd2a84cace2173f9db187ee8b4a5f3dc7e760eba80"
    "000000006a00000001000000" + "00" * 68
)


def test_parse_beacon_fields() -> None:
    b = P.parse_beacon(make_beacon(on=True))
    assert b is not None
    assert b.mac == "02:00:00:00:00:01"
    assert b.unique_id == "020000000001"
    assert b.device_id == "ECO-00000001"
    assert b.name == "Test Plug"
    assert b.ip == "192.0.2.10"
    assert b.command_port == 1022
    assert b.is_on is True
    assert b.token == FAKE["token"]
    assert b.eco_id == FAKE["eco_id"]
    assert b.firmware_version == "V2.3.0"
    assert b.short_id == FAKE["short_id"]
    assert b.key == FAKE["key"]
    assert P.parse_beacon(make_beacon(on=False)).is_on is False


def test_beacon_is_obfuscated_and_key_independent() -> None:
    a, b = make_beacon(key=b"\x01\x02\x03\x04"), make_beacon(key=b"\xaa\xbb\xcc\xdd")
    assert a != b
    assert b"Test Plug" not in a
    assert P.parse_beacon(a) == P.parse_beacon(b)


def test_beacon_xor_is_involution() -> None:
    raw = make_beacon()
    assert P.beacon_xor(P.beacon_xor(raw)) == raw


def test_rejects_corruption_and_junk() -> None:
    raw = bytearray(make_beacon(on=True))
    raw[0xF0] ^= 1  # flip the state byte only
    assert P.parse_beacon(bytes(raw)) is None
    assert P.parse_beacon(b"") is None
    assert P.parse_beacon(bytes(272)) is None
    wrong_brand = bytearray(make_beacon())
    wrong_brand[0x0A] = ord("X")
    assert P.parse_beacon(bytes(wrong_brand)) is None


def test_command_golden_vector() -> None:
    b = P.parse_beacon(make_beacon())
    pkt = P.build_set_power(b, True, P.SUBTYPE_ALT, nonce=1700000123)
    assert pkt.hex() == GOLDEN_ON_16
    off = P.build_set_power(b, False, 0x17, nonce=1700000123)
    assert len(off) == 152
    assert off[0x04] == 0x17
    assert off[0x4C] == P.MSG_SET_POWER
    assert off[0x50] == 0


def test_command_carries_beacon_values() -> None:
    b = P.parse_beacon(make_beacon())
    buf = bytearray(P.build_set_power(b, True, nonce=42))
    P._command_xor(buf)
    assert buf[0x10:0x1C].rstrip(b"\0") == FAKE["token"]
    assert int.from_bytes(buf[0x0C:0x10], "little") == FAKE["eco_id"]
    assert int.from_bytes(buf[0x44:0x48], "little") == 0x12345678


def test_parse_status_reply() -> None:
    from .beacon_factory import make_reply

    assert P.parse_status_reply(make_reply(True)) is True
    assert P.parse_status_reply(make_reply(False)) is False
    assert P.parse_status_reply(bytes(152)) is None
    assert P.parse_status_reply(b"short") is None


def test_real_beacons_decode() -> None:
    from .real_beacons import REAL_OFF, REAL_ON

    on, off = P.parse_beacon(REAL_ON), P.parse_beacon(REAL_OFF)
    assert on is not None and off is not None
    assert (on.is_on, off.is_on) == (True, False)
    for b in (on, off):
        # Untouched, device-produced fields:
        assert b.firmware_version == "V2.3.0"
        assert b.command_port == 1022
        assert b.cloud_host == "server1.eco-plugs.net"
        assert b.cloud_port == 22109
        # Substituted fields:
        assert b.eco_id == 123456  # 0x24, not the 654321 at 0x28
        assert b.token == FAKE["token"]
        assert b.mac == "02:00:00:00:00:01"
    assert on.device_time == 1786939293


def test_real_beacon_checksum_literal() -> None:
    from .real_beacons import REAL_ON

    decoded = P.beacon_xor(REAL_ON)
    assert P.beacon_checksum(decoded) == int.from_bytes(REAL_ON[0x10C:0x110], "little")
    assert decoded[0x14:0x20].hex() == "d2070000e60000009d87826a"


def test_rejects_bad_magic() -> None:
    raw = bytearray(make_beacon())
    raw[5] = 0x00
    assert P.parse_beacon(bytes(raw)) is None


def test_command_port_fallback() -> None:
    assert P.parse_beacon(make_beacon(port=0)).command_port == P.DEFAULT_COMMAND_PORT
    assert (
        P.parse_beacon(make_beacon(port=70000)).command_port == P.DEFAULT_COMMAND_PORT
    )
    assert P.parse_beacon(make_beacon(port=4321)).command_port == 4321


def test_firmware_fixed_ports() -> None:
    """Fixed by the firmware; see docs/PROTOCOL.md §1 before changing."""
    assert (P.BEACON_LISTEN_PORT, P.COMMAND_SOURCE_PORT) == (10228, 9090)
    assert P.COMMAND_SUBTYPES == (0x16, 0x17)

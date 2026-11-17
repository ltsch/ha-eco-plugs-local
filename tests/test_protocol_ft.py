"""v1.x protocol: parsing, validation and the golden wire vectors."""

from __future__ import annotations

from datetime import date, datetime
import struct

import pytest

from custom_components.eco_plugs_local import protocol_ft as P

from .ft_factory import (
    FAKE_FT,
    make_power_payload,
    make_random_payload,
    make_response,
    make_search_reply,
    make_timer_entry,
    make_timers_payload,
)


def _device(**over) -> P.FtDevice:
    device = P.parse_search_reply(make_search_reply(**over))
    assert device is not None
    return device


def test_parse_search_reply() -> None:
    d = _device()
    assert d.mac == "02:00:00:00:00:02"
    assert d.unique_id == "020000000002"
    assert d.device_id == "ECO-00000002"
    assert d.alias == "Test FT Plug"
    assert d.version == "1.6.0"
    assert d.ip == "192.0.2.20"
    assert d.port == 80
    assert d.auth == make_search_reply()[4 : 4 + 0x66]


def test_device_repr_hides_the_password() -> None:
    assert FAKE_FT["password"] not in repr(_device())


@pytest.mark.parametrize(
    "reply",
    [
        make_search_reply()[:-1],
        make_search_reply() + b"\x00",
        make_search_reply(mac="not-a-mac"),
        make_search_reply(mac="02:00:00:00:00"),
        make_search_reply(ip="999.1.1.1"),
        make_search_reply(ip=""),
        make_search_reply(version=""),
        make_search_reply(device_id=""),
        b"",
    ],
)
def test_parse_search_reply_rejects(reply: bytes) -> None:
    assert P.parse_search_reply(reply) is None


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_bad_port_falls_back(port: int) -> None:
    assert _device(port=port).port == P.DEFAULT_COMMAND_PORT


def test_mac_is_lowercased() -> None:
    assert _device(mac="AA:BB:CC:00:11:22").unique_id == "aabbcc001122"


def test_search_golden_vector() -> None:
    expected = bytearray(128)
    expected[0x18:0x1A] = (2026).to_bytes(2, "little")
    expected[0x1A] = 11
    expected[0x1B] = 15
    expected[0x1C:0x20] = (21 * 3600 + 6 * 60 + 32).to_bytes(4, "little")
    assert P.build_search(datetime(2026, 11, 15, 21, 6, 32)) == bytes(expected)


def _golden_head(cmd: int, extern: int) -> bytearray:
    head = bytearray(0x80)
    head[0x00:0x04] = cmd.to_bytes(4, "little")
    head[0x06:0x08] = (0x1234).to_bytes(2, "little")
    head[0x08:0x0A] = extern.to_bytes(2, "little")
    head[0x0A:0x70] = make_search_reply()[4 : 4 + 0x66]
    head[0x74:0x78] = (0x01020304).to_bytes(4, "little")
    head[0x7C:0x80] = (0x0A0B0C0D).to_bytes(4, "little")
    return head


def test_get_switch_golden_vector() -> None:
    packet = P.build_command(
        P.CMD_GET_SWITCH,
        _device(),
        sequence=0x1234,
        rand_id=0x0A0B0C0D,
        sendtime=0x01020304,
    )
    assert packet == bytes(_golden_head(0x50017, 0))


@pytest.mark.parametrize("on", [True, False])
def test_set_switch_golden_vector(on: bool) -> None:
    packet = P.build_command(
        P.CMD_SET_SWITCH,
        _device(),
        bytes([1, int(on)]),
        sequence=0x1234,
        rand_id=0x0A0B0C0D,
        sendtime=0x01020304,
    )
    assert packet == bytes(_golden_head(0x50016, 2)) + bytes([1, int(on)])


def test_helpers_build_the_same_commands() -> None:
    d = _device()
    assert P.build_get_switch(d, sequence=7)[:4] == (0x50017).to_bytes(4, "little")
    set_on = P.build_set_switch(d, True, sequence=7)
    assert set_on[:4] == (0x50016).to_bytes(4, "little")
    assert set_on[0x80:] == b"\x01\x01"
    assert P.build_set_switch(d, False, sequence=7)[0x80:] == b"\x01\x00"


def test_sequence_range_matches_the_app() -> None:
    assert all(1 <= P.new_sequence() <= 0x7FD7 for _ in range(200))


def test_parse_response() -> None:
    r = P.parse_response(make_response(0x50017, 9, 0, b"\x01\x01"))
    assert r == P.Response(0x50017, 0, 9, b"\x01\x01")
    assert P.parse_response(make_response(0x50016, 9, 3)) == P.Response(
        0x50016, 3, 9, b""
    )


def test_parse_response_rejects() -> None:
    assert P.parse_response(b"\x00" * 127) is None
    short = bytearray(make_response(0x50017, 9, 0, b"\x01\x01"))[:-1]
    assert P.parse_response(bytes(short)) is None  # claims 2 payload bytes, has 1


@pytest.mark.parametrize(
    ("response", "state"),
    [
        (P.Response(0x50017, 0, 1, b"\x01\x00"), False),
        (P.Response(0x50017, 0, 1, b"\x01\x01"), True),
        (P.Response(0x50017, 3, 1, b"\x01\x01"), None),  # refused
        (P.Response(0x50017, 0, 1, b"\x02\x01"), None),  # not switch 1
        (P.Response(0x50017, 0, 1, b"\x01\x02"), None),  # not 0 or 1
        (P.Response(0x50017, 0, 1, b"\x01"), None),
        (P.Response(0x50016, 0, 1, b"\x01\x01"), None),  # a set ack carries no state
    ],
)
def test_switch_state(response: P.Response, state: bool | None) -> None:
    assert P.switch_state(response) is state


def test_search_request_fields_are_little_endian() -> None:
    packet = P.build_search(datetime(2030, 1, 2, 3, 4, 5))
    assert struct.unpack_from("<H", packet, 0x18)[0] == 2030
    assert struct.unpack_from("<i", packet, 0x1C)[0] == 3 * 3600 + 4 * 60 + 5


def test_power_request_golden_vector() -> None:
    expected = bytearray(56)
    expected[0x04:0x08] = (2026).to_bytes(4, "little")
    expected[0x08] = 11
    expected[0x09] = 16
    expected[0x0C:0x10] = (2026).to_bytes(4, "little")
    expected[0x10] = 11
    expected[0x11] = 16
    assert P.power_request_payload(date(2026, 11, 16)) == bytes(expected)
    packet = P.build_get_power(_device(), date(2026, 11, 16), sequence=0x1234)
    assert packet[:4] == (0x50032).to_bytes(4, "little")
    assert packet[0x08:0x0A] == (56).to_bytes(2, "little")
    assert packet[0x80:] == bytes(expected)


def _power(**kw) -> P.PowerReading | None:
    return P.parse_power(P.Response(0x50032, 0, 1, make_power_payload(**kw)))


def test_parse_power_applies_the_apps_formulas() -> None:
    r = _power()
    assert (r.volts, r.amps, r.watts, r.kwh) == pytest.approx((100.0, 0.5, 50.0, 3.0))
    assert r.metered


def test_power_energy_uses_the_high_word() -> None:
    r = _power(high=1)
    assert r.kwh == pytest.approx(((1 << 32) + 3_000_000) * 100 / 100_000_000)


@pytest.mark.parametrize(
    "zeroed",
    ["voltage", "voltage_offset", "current", "current_offset", "power", "power_offset"],
)
def test_power_value_is_zero_without_raw_value_or_offset(zeroed: str) -> None:
    r = _power(**{zeroed: 0})
    field = {"voltage": "volts", "current": "amps", "power": "watts"}[
        zeroed.split("_")[0]
    ]
    assert getattr(r, field) == 0.0
    assert r.metered


def test_power_energy_needs_total_and_offset() -> None:
    assert _power(total=0).kwh == 0.0
    assert _power(energy_offset=0).kwh == 0.0


def test_unmetered_plug_reads_all_zero() -> None:
    zeros = make_power_payload(
        voltage=0,
        current=0,
        power=0,
        total=0,
        energy_offset=0,
        current_offset=0,
        power_offset=0,
        voltage_offset=0,
    )
    r = P.parse_power(P.Response(0x50032, 0, 1, zeros))
    assert not r.metered
    assert (r.volts, r.amps, r.watts, r.kwh) == (0.0, 0.0, 0.0, 0.0)


def test_parse_power_rejects() -> None:
    good = make_power_payload()
    assert P.parse_power(P.Response(0x50032, 0x33, 1, good)) is None  # bad date range
    assert P.parse_power(P.Response(0x50032, 3, 1, good)) is None
    assert P.parse_power(P.Response(0x50032, 0, 1, good[:55])) is None
    assert P.parse_power(P.Response(0x50017, 0, 1, good)) is None


def test_parse_clock() -> None:
    d = _device(clock=datetime(2026, 11, 16, 13, 44, 48), tz_offset=-46800)
    assert d.clock == P.PlugClock(datetime(2026, 11, 16, 13, 44, 48), False, -46800)
    assert _device(clock=datetime(2026, 7, 1, 0, 0, 0), dst=True).clock.dst is True


def test_clock_is_absent_when_the_record_is_empty() -> None:
    assert _device().clock is None


@pytest.mark.parametrize("seconds", [86400, -1])
def test_clock_rejects_impossible_seconds(seconds: int) -> None:
    raw = bytearray(make_search_reply(clock=datetime(2026, 1, 1)))
    raw[4 + 0x68 + 4 : 4 + 0x68 + 8] = seconds.to_bytes(4, "little", signed=True)
    assert P.parse_search_reply(bytes(raw)).clock is None


def test_clock_rejects_impossible_dates() -> None:
    raw = bytearray(make_search_reply(clock=datetime(2026, 1, 1)))
    raw[4 + 0x68 + 2] = 13  # month
    assert P.parse_search_reply(bytes(raw)).clock is None


def _timers(*entries: bytes) -> tuple[P.Timer, ...] | None:
    return P.parse_timers(P.Response(0x50103, 0, 1, make_timers_payload(*entries)))


def test_parse_timers() -> None:
    (t,) = _timers(make_timer_entry())
    assert t == P.Timer(
        timer_id=0,
        kind=0,
        mode=2,
        days=(
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ),
        start_seconds=28800,
        start_state=1,
        end_seconds=73800,
        end_state=0,
    )


@pytest.mark.parametrize(
    ("mask", "days"),
    [
        (0x20, ("monday",)),
        (0x10, ("tuesday",)),
        (0x08, ("wednesday",)),
        (0x04, ("thursday",)),
        (0x02, ("friday",)),
        (0x01, ("saturday",)),
        (0x40, ("sunday",)),
        (0x41, ("saturday", "sunday")),
        (0x00, ()),
    ],
)
def test_timer_weekday_bits_follow_the_apps_day_buttons(mask: int, days: tuple) -> None:
    (t,) = _timers(make_timer_entry(mask=mask))
    assert t.days == days


def test_empty_slots_are_skipped_and_order_is_kept() -> None:
    timers = _timers(
        make_timer_entry(timer_id=3), make_timer_entry(timer_id=5, start=60)
    )
    assert [t.timer_id for t in timers] == [3, 5]
    assert _timers() == ()


def test_timer_with_impossible_seconds_is_skipped() -> None:
    assert _timers(make_timer_entry(start=86400)) == ()
    assert _timers(make_timer_entry(end=-1)) == ()


def test_parse_timers_rejects() -> None:
    good = make_timers_payload(make_timer_entry())
    assert P.parse_timers(P.Response(0x50103, 3, 1, good)) is None
    assert P.parse_timers(P.Response(0x50103, 0, 1, good[:387])) is None
    assert P.parse_timers(P.Response(0x50017, 0, 1, good)) is None


def test_parse_random() -> None:
    def parse(payload, mark=0, cmd=0x50021):
        return P.parse_random(P.Response(cmd, mark, 1, payload))

    on = parse(make_random_payload())
    assert (on.random, on.vacation) == (True, False)
    assert on.raw == make_random_payload()
    off = parse(make_random_payload(vacation=True, random=False))
    assert (off.random, off.vacation) == (False, True)
    assert parse(make_random_payload()[:43]) is None
    assert parse(make_random_payload(), mark=3) is None
    assert parse(make_random_payload(), cmd=0x50103) is None


def test_dst_payload_is_what_the_app_sends() -> None:
    # year 0x17DF (2015 with the DST flag) or 0x07DF, April 18th, 57600 s, offset 0
    assert P.dst_payload(True) == bytes.fromhex("df17041200e1000000000000")
    assert P.dst_payload(False) == bytes.fromhex("df07041200e1000000000000")


def test_random_payload_changes_only_the_flag() -> None:
    current = P.parse_random(
        P.Response(0x50021, 0, 1, make_random_payload(vacation=True, random=True))
    )
    off = P.random_payload(current, False)
    assert off[1] == 0
    assert off[:1] + off[2:] == current.raw[:1] + current.raw[2:]  # nothing else moved
    assert P.random_payload(current, True) == current.raw


TODAY = date(2026, 11, 16)
ALL_DAYS = list(P.WEEKDAYS)


def _list(*entries: bytes) -> bytes:
    return make_timers_payload(*entries)


def test_a_healthy_list_is_recognised() -> None:
    assert P.timer_list_entries(_list()) == []
    assert P.timer_list_entries(_list(make_timer_entry())) == [make_timer_entry()]


def test_a_damaged_list_is_not() -> None:
    good = _list(make_timer_entry())
    assert P.timer_list_entries(good[:-1]) is None
    shifted = bytearray(388)  # what a single entry written over the list looks like
    shifted[:32] = make_timer_entry()
    assert P.timer_list_entries(bytes(shifted)) is None
    wrong_count = bytearray(good)
    wrong_count[0] = 2
    assert P.timer_list_entries(bytes(wrong_count)) is None
    gap = bytearray(388)
    struct.pack_into("<h", gap, 0, 1)
    gap[4 + 32 : 4 + 64] = make_timer_entry()  # the only entry is in the second slot
    assert P.timer_list_entries(bytes(gap)) is None


def test_adding_a_timer_uses_the_lowest_unused_id() -> None:
    raw = _list(make_timer_entry(timer_id=0), make_timer_entry(timer_id=2))
    new = P.with_timer_added(raw, ["sunday"], 3 * 3600, 3 * 3600 + 300, TODAY)
    entries = P.timer_list_entries(new)
    assert [e[0] for e in entries] == [0, 2, 1]
    added = entries[2]
    assert added[3] == 0x40 and added[2] == 2
    assert struct.unpack_from("<i", added, 0x10)[0] == 10800
    assert struct.unpack_from("<i", added, 0x1C)[0] == 11100
    assert (added[0x14], added[0x1A]) == (1, 0)  # on at the start, off at the end
    assert struct.unpack_from("<hBB", added, 0x0C) == (2026, 11, 16)
    assert struct.unpack_from("<hBB", added, 0x16) == (2026, 11, 16)
    assert entries[:2] == [make_timer_entry(timer_id=0), make_timer_entry(timer_id=2)]
    assert struct.unpack_from("<h", new, 0)[0] == 3


def test_the_first_timer_gets_id_zero() -> None:
    new = P.with_timer_added(_list(), ALL_DAYS, 0, 60, TODAY)
    assert P.timer_list_entries(new)[0][0] == 0
    assert P.timer_list_entries(new)[0][3] == 0x7F


def test_a_full_list_takes_no_more() -> None:
    full = _list(*(make_timer_entry(timer_id=i) for i in range(12)))
    with pytest.raises(ValueError, match="at most 12"):
        P.with_timer_added(full, ALL_DAYS, 0, 60, TODAY)


@pytest.mark.parametrize(
    ("days", "start", "end", "message"),
    [
        (["funday"], 0, 60, "Days must be"),
        ([], 0, 60, "Days must be"),
        (ALL_DAYS, 86400, 60, "within one day"),
        (ALL_DAYS, 0, -1, "within one day"),
    ],
)
def test_bad_timer_input_is_refused(days, start, end, message) -> None:
    with pytest.raises(ValueError, match=message):
        P.with_timer_added(_list(), days, start, end, TODAY)


def test_updating_changes_only_what_was_given() -> None:
    raw = _list(make_timer_entry(), make_timer_entry(timer_id=1, mask=0x40))
    new = P.with_timer_updated(raw, 1, days=["monday", "friday"], start=60)
    a, b = P.timer_list_entries(new)
    assert a == make_timer_entry()
    assert b[3] == 0x22
    assert struct.unpack_from("<i", b, 0x10)[0] == 60
    assert struct.unpack_from("<i", b, 0x1C)[0] == 73800  # end kept
    assert b[0] == 1


def test_updating_an_unknown_timer_is_refused() -> None:
    with pytest.raises(ValueError, match="no timer 5"):
        P.with_timer_updated(_list(make_timer_entry()), 5, start=0)


def test_removing_moves_the_later_ones_down() -> None:
    raw = _list(*(make_timer_entry(timer_id=i) for i in range(3)))
    new = P.with_timer_removed(raw, 1)
    assert [e[0] for e in P.timer_list_entries(new)] == [0, 2]
    assert struct.unpack_from("<h", new, 0)[0] == 2
    assert not any(new[4 + 64 : 4 + 96])
    assert P.timer_list_entries(P.with_timer_removed(new, 0))[0][0] == 2
    assert (
        P.timer_list_entries(P.with_timer_removed(P.with_timer_removed(new, 0), 2))
        == []
    )


def test_removing_an_unknown_timer_is_refused() -> None:
    with pytest.raises(ValueError, match="no timer 3"):
        P.with_timer_removed(_list(make_timer_entry()), 3)


def test_a_damaged_list_is_never_edited() -> None:
    bad = bytearray(388)
    bad[:32] = make_timer_entry()
    for edit in (
        lambda: P.with_timer_added(bytes(bad), ALL_DAYS, 0, 60, TODAY),
        lambda: P.with_timer_updated(bytes(bad), 0, start=0),
        lambda: P.with_timer_removed(bytes(bad), 0),
    ):
        with pytest.raises(ValueError, match="damaged"):
            edit()


def _timer(**kw):
    defaults = {
        "timer_id": 0,
        "kind": 0,
        "mode": 2,
        "days": P.WEEKDAYS,
        "start_seconds": 28800,
        "start_state": 1,
        "end_seconds": 73800,
        "end_state": 0,
    }
    return P.Timer(**{**defaults, **kw})


@pytest.mark.parametrize(
    ("days", "text"),
    [
        (list(P.WEEKDAYS), "every day"),
        (list(P.WEEKDAYS[:5]), "weekdays"),
        (list(P.WEEKDAYS[5:]), "weekends"),
        (
            ["monday", "tuesday", "wednesday", "thursday", "saturday", "sunday"],
            "Mon\u2013Thu, Sat, Sun",
        ),
        (["monday", "wednesday", "friday"], "Mon, Wed, Fri"),
        (["tuesday", "wednesday"], "Tue, Wed"),
        (["sunday"], "Sun"),
        ([], "no days"),
    ],
)
def test_describe_days(days: list[str], text: str) -> None:
    assert P.describe_days(days) == text


def test_describe_timer_shows_seconds_only_when_nonzero() -> None:
    assert P.describe_timer(_timer()) == "every day: on 08:00, off 20:30"
    odd = _timer(days=("saturday",), start_seconds=28805, end_seconds=0)
    assert P.describe_timer(odd) == "Sat: on 08:00:05, off 00:00"


def test_describe_timers() -> None:
    assert P.describe_timers(()) == "No timers"
    two = (_timer(), _timer(timer_id=1, days=("sunday",)))
    assert P.describe_timers(two) == (
        "every day: on 08:00, off 20:30; Sun: on 08:00, off 20:30"
    )


def test_describe_timers_falls_back_to_a_count_for_a_long_list() -> None:
    twelve = tuple(
        _timer(timer_id=i, days=("monday", "wednesday", "friday")) for i in range(12)
    )
    assert len(P.describe_timers(twelve, limit=255)) <= 255
    assert P.describe_timers(twelve) == "12 timers"

#!/usr/bin/env python3
"""Command-line tool for Eco Plugs, built on the integration's protocol.py.

    listen     print every beacon heard
    dump       decoded beacon hexdump with identifying bytes masked
    on/off     send a command and wait for the ACK (switches the real load!)

Plugs on v1.x firmware send no beacon, so they are asked instead:

    ft-search  search for v1.x plugs and print one line each
    ft-status  read a v1.x plug's relay state and clock
    ft-timers  read a v1.x plug's timers and its random and vacation flags
    ft-on/-off switch a v1.x plug and confirm by reading it back (switches the
               real load!)

Run from a clone of the repo on the plug's subnet, with UDP 10228 and 9090
free (v1.x plugs: 8900). Python 3.10+, no dependencies. Use --help for options.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import socket
import sys
import time

if sys.version_info < (3, 10):  # noqa: UP036 - this script runs outside the venv
    sys.exit(f"Python 3.10 or newer is required (this is {sys.version.split()[0]}).")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parent.parent
        / "custom_components/eco_plugs_local"
        / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


P = _load("ecoplugs_protocol", "protocol.py")
F = _load("ecoplugs_protocol_ft", "protocol_ft.py")

FT_BROADCAST = "255.255.255.255"
FT_HINT = (
    " (on a VPN or with several networks? Try --host <plug IP>,"
    " which does not depend on the default route)"
)
FT_UNICAST_PORT = F.SEARCH_PORTS[-1]  # the port a v1.x plug answers searches on
FT_FIND_SECONDS = 4.0
FT_REPLY_TIMEOUT = 1.5  # mirrors REPLY_TIMEOUT in const.py
FT_ATTEMPTS = 3


def _sock(port: int, timeout: float, share: bool = True) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if share:  # the command port must be exclusive, as in hub.py
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", port))
    s.settimeout(timeout)
    return s


def beacons(duration: float):
    s = _sock(P.BEACON_LISTEN_PORT, 1.0)
    end = time.monotonic() + duration
    try:
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(2048)
            except TimeoutError:
                continue
            b = P.parse_beacon(data)
            if b:
                yield b, addr[0]
    finally:
        s.close()


def find(mac: str | None, duration: float = 6.0):
    for b, host in beacons(duration):
        if mac is None or b.mac == mac.lower():
            return b, host
    sys.exit("no matching beacon heard")


# Byte ranges shown by "dump"; everything else is masked (allow-list, so new
# firmware fields can't leak). See docs/PROTOCOL.md §2.2.
_SAFE_RANGES = [(0x00, 0x20), (0x4C, 0x90), (0xF0, 0x108)]


def dump(mac: str | None, unmasked: bool) -> None:
    s = _sock(P.BEACON_LISTEN_PORT, 1.0)
    end = time.monotonic() + 10.0
    try:
        while True:
            if time.monotonic() > end:
                sys.exit("no matching beacon heard")
            try:
                data, _addr = s.recvfrom(2048)
            except TimeoutError:
                continue
            b = P.parse_beacon(data)
            if b and (mac is None or b.mac == mac.lower()):
                break
    finally:
        s.close()
    d = bytearray(P.beacon_xor(data))
    if not unmasked:
        safe = {i for lo, hi in _SAFE_RANGES for i in range(lo, hi)}
        for i in range(len(d)):
            if i not in safe:
                d[i] = 0xEE
    print(
        f"# firmware {b.firmware_version}, state {'on' if b.is_on else 'off'}, "
        f"command port {b.command_port}, length {len(data)}"
        + ("" if unmasked else ", identifying bytes masked as ee")
    )
    for i in range(0, len(d), 16):
        row = d[i : i + 16]
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in row)
        print(f"{i:04x}  {row.hex(' ')}  {text}")


def set_power(on: bool, mac: str | None, subtypes: tuple[int, ...]) -> None:
    b, host = find(mac)
    print(
        f"{b.name!r} {b.mac} at {host}:{b.command_port}, currently {'on' if b.is_on else 'off'}"
    )
    s = _sock(
        P.COMMAND_SOURCE_PORT, 1.5, share=False
    )  # timeout/attempts mirror const.py
    try:
        for attempt in range(3):
            for sub in subtypes:
                s.sendto(P.build_set_power(b, on, sub), (host, b.command_port))
            try:
                while True:
                    data, addr = s.recvfrom(2048)
                    state = P.parse_status_reply(data)
                    if addr[0] == host and state is not None:
                        print(f"ACK: plug reports {'on' if state else 'off'}")
                        if state == on:
                            return
            except TimeoutError:
                print(f"no confirmation (attempt {attempt + 1}/3)")
    finally:
        s.close()
    sys.exit("plug did not confirm the command")


def _ft_replies(
    s: socket.socket, host: str | None, seconds: float, broadcast: str = FT_BROADCAST
):
    """Send a search (a broadcast, or unicast to ``host``) and yield each answer."""
    packet = F.build_search()
    targets = (
        [(host, FT_UNICAST_PORT)]
        if host
        else [(broadcast, port) for port in F.SEARCH_PORTS]
    )
    for target in targets:
        s.sendto(packet, target)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            data, addr = s.recvfrom(2048)
        except TimeoutError:
            continue
        device = F.parse_search_reply(data)
        if device:
            yield device, addr[0]


def _ft_find(
    s: socket.socket, mac: str | None, host: str | None, broadcast: str = FT_BROADCAST
):
    for device, addr in _ft_replies(s, host, FT_FIND_SECONDS, broadcast):
        if mac is None or device.mac == mac.lower():
            return device, addr
    sys.exit("no matching v1.x plug answered" + FT_HINT)


def _ft_exchange(s: socket.socket, device, addr: str, cmd: int, payload: bytes = b""):
    for attempt in range(1, FT_ATTEMPTS + 1):
        sequence = F.new_sequence()
        s.sendto(
            F.build_command(cmd, device, payload, sequence=sequence),
            (addr, device.port),
        )
        end = time.monotonic() + FT_REPLY_TIMEOUT
        while time.monotonic() < end:
            try:
                data, source = s.recvfrom(2048)
            except TimeoutError:
                continue
            reply = F.parse_response(data)
            if (
                reply
                and source[0] == addr
                and reply.cmd == cmd
                and reply.sequence == sequence
            ):
                return reply
        print(f"no reply (attempt {attempt}/{FT_ATTEMPTS})")
    sys.exit("plug did not answer")


def _ft_state(s: socket.socket, device, addr: str) -> bool:
    state = F.switch_state(_ft_exchange(s, device, addr, F.CMD_GET_SWITCH))
    if state is None:
        sys.exit("unexpected status reply")
    return state


def ft_search(
    seconds: float, host: str | None, masked: bool, broadcast: str = FT_BROADCAST
) -> None:
    s = _sock(F.SOURCE_PORT, 0.5, share=False)
    seen = set()
    try:
        for device, addr in _ft_replies(s, host, seconds, broadcast):
            if device.mac in seen:  # every reply arrives twice
                continue
            seen.add(device.mac)
            if masked:
                print(
                    f"v1.x plug: firmware {device.version}, command port {device.port}"
                )
            else:
                print(
                    f"{addr:15} {device.mac} {device.device_id:14} "
                    f"fw {device.version} port {device.port} {device.alias!r}"
                )
    finally:
        s.close()
    if not seen:
        sys.exit("no v1.x plug answered" + FT_HINT)


def ft_status(mac: str | None, host: str | None, broadcast: str = FT_BROADCAST) -> None:
    s = _sock(F.SOURCE_PORT, 0.5, share=False)
    try:
        device, addr = _ft_find(s, mac, host, broadcast)
        print(
            f"{device.alias!r} {device.mac} at {addr}:{device.port}, firmware {device.version}"
        )
        print(f"state: {'on' if _ft_state(s, device, addr) else 'off'}")
        if device.clock:
            drift = (device.clock.local - datetime.now()).total_seconds()
            print(
                f"clock: {device.clock.local:%Y-%m-%d %H:%M:%S}, DST flag "
                f"{'on' if device.clock.dst else 'off'}, {drift:+.0f} s from this computer"
            )
    finally:
        s.close()


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _state(value: int) -> str:
    return {1: "on", 0: "off"}.get(value, str(value))


def ft_timers(mac: str | None, host: str | None, broadcast: str = FT_BROADCAST) -> None:
    s = _sock(F.SOURCE_PORT, 0.5, share=False)
    try:
        device, addr = _ft_find(s, mac, host, broadcast)
        timers = F.parse_timers(_ft_exchange(s, device, addr, F.CMD_GET_TIMERS))
        flags = F.parse_random(_ft_exchange(s, device, addr, F.CMD_GET_RANDOM))
    finally:
        s.close()
    if timers is None or flags is None:
        sys.exit("unexpected timer reply")
    print(f"{len(timers)} timer(s)")
    for t in timers:
        days = ",".join(d[:3] for d in t.days) or "no days"
        print(
            f"  #{t.timer_id}: {days}: {_state(t.start_state)} at {_clock(t.start_seconds)}, "
            f"{_state(t.end_state)} at {_clock(t.end_seconds)}"
        )
    print(
        f"random timers: {'on' if flags.random else 'off'}, vacation mode: {'on' if flags.vacation else 'off'}"
    )


def ft_set_power(
    on: bool, mac: str | None, host: str | None, broadcast: str = FT_BROADCAST
) -> None:
    s = _sock(F.SOURCE_PORT, 0.5, share=False)
    try:
        device, addr = _ft_find(s, mac, host, broadcast)
        now = "on" if _ft_state(s, device, addr) else "off"
        print(f"{device.alias!r} {device.mac} at {addr}:{device.port}, currently {now}")
        reply = _ft_exchange(s, device, addr, F.CMD_SET_SWITCH, bytes([1, int(on)]))
        if reply.mark == F.MARK_NO_PERMIT:
            sys.exit(
                f"the plug refused the command (it only accepts source port {F.SOURCE_PORT})"
            )
        if reply.mark != F.MARK_OK:
            sys.exit(f"the plug rejected the command (code {reply.mark})")
        for _ in range(FT_ATTEMPTS):
            state = _ft_state(s, device, addr)
            print(f"ACK: plug reports {'on' if state else 'off'}")
            if state == on:
                return
            time.sleep(0.3)
    finally:
        s.close()
    sys.exit("the plug did not reach the requested state")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "action",
        choices=[
            "listen",
            "dump",
            "on",
            "off",
            "ft-search",
            "ft-status",
            "ft-timers",
            "ft-on",
            "ft-off",
        ],
    )
    ap.add_argument("--mac", help="target plug MAC (default: first plug heard)")
    ap.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="listen/ft-search duration (default: 30 and 4)",
    )
    ap.add_argument("--unmasked", action="store_true", help="dump: do not mask secrets")
    ap.add_argument(
        "--host",
        help="ft-*: the plug's IP, to search it directly instead of broadcasting",
    )
    ap.add_argument(
        "--broadcast",
        default=FT_BROADCAST,
        help="ft-*: where to send the search broadcast (default: %(default)s). "
        "Untested against a real plug with anything but the default",
    )
    ap.add_argument(
        "--masked",
        action="store_true",
        help="ft-search: print only firmware and port, for pasting into a report",
    )
    ap.add_argument(
        "--subtypes",
        default=",".join(hex(x) for x in P.COMMAND_SUBTYPES),
        help="on/off: comma-separated sub-types to send (default: %(default)s)",
    )
    a = ap.parse_args()
    if a.action == "listen":
        for b, host in beacons(30.0 if a.seconds is None else a.seconds):
            print(
                f"{time.strftime('%H:%M:%S')} {host:15} {b.mac} {b.device_id:14} "
                f"{'ON ' if b.is_on else 'OFF'} {b.name!r}"
            )
    elif a.action == "dump":
        dump(a.mac, a.unmasked)
    elif a.action == "ft-search":
        ft_search(
            4.0 if a.seconds is None else a.seconds, a.host, a.masked, a.broadcast
        )
    elif a.action == "ft-status":
        ft_status(a.mac, a.host, a.broadcast)
    elif a.action == "ft-timers":
        ft_timers(a.mac, a.host, a.broadcast)
    elif a.action in ("ft-on", "ft-off"):
        ft_set_power(a.action == "ft-on", a.mac, a.host, a.broadcast)
    else:
        subtypes = tuple(int(x, 0) for x in a.subtypes.split(","))
        set_power(a.action == "on", a.mac, subtypes)


if __name__ == "__main__":
    main()

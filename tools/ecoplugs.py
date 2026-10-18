#!/usr/bin/env python3
"""Command-line tool for Eco Plugs, built on the integration's protocol.py.

    listen   print every beacon heard
    dump     decoded beacon hexdump with identifying bytes masked
    on/off   send a command and wait for the ACK (switches the real load!)

Run from a clone of the repo on the plug's subnet, with UDP 10228 and 9090
free. Python 3.10+, no dependencies. Use --help for options.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import socket
import sys
import time

_spec = importlib.util.spec_from_file_location(
    "ecoplugs_protocol",
    Path(__file__).resolve().parent.parent
    / "custom_components/eco_plugs_local/protocol.py",
)
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P
_spec.loader.exec_module(P)


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


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("action", choices=["listen", "dump", "on", "off"])
    ap.add_argument("--mac", help="target plug MAC (default: first plug heard)")
    ap.add_argument("--seconds", type=float, default=30.0, help="listen duration")
    ap.add_argument("--unmasked", action="store_true", help="dump: do not mask secrets")
    ap.add_argument(
        "--subtypes",
        default=",".join(hex(x) for x in P.COMMAND_SUBTYPES),
        help="on/off: comma-separated sub-types to send (default: %(default)s)",
    )
    a = ap.parse_args()
    if a.action == "listen":
        for b, host in beacons(a.seconds):
            print(
                f"{time.strftime('%H:%M:%S')} {host:15} {b.mac} {b.device_id:14} "
                f"{'ON ' if b.is_on else 'OFF'} {b.name!r}"
            )
    elif a.action == "dump":
        dump(a.mac, a.unmasked)
    else:
        subtypes = tuple(int(x, 0) for x in a.subtypes.split(","))
        set_power(a.action == "on", a.mac, subtypes)


if __name__ == "__main__":
    main()

"""The v1.x CLI commands, run against a loopback fake plug (never a real one)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from .ft_factory import (
    FAKE_FT,
    make_random_payload,
    make_timer_entry,
    make_timers_payload,
)

_spec = importlib.util.spec_from_file_location(
    "ecoplugs_cli", Path(__file__).parent.parent / "tools/ecoplugs.py"
)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


@pytest.fixture
def aim_at(fake_ft_plug):
    """Point the CLI at a fake plug on loopback, with an ephemeral source port."""

    async def _aim(**kw):
        plug = await fake_ft_plug(**kw)
        stack = [
            patch.object(cli.F, "SOURCE_PORT", 0),
            patch.object(cli, "FT_UNICAST_PORT", plug.port),
            patch.object(cli, "FT_FIND_SECONDS", 0.3),
            patch.object(cli, "FT_REPLY_TIMEOUT", 0.1),
        ]
        for p in stack:
            p.start()
        return plug, stack

    patches: list = []

    async def aim(**kw):
        plug, stack = await _aim(**kw)
        patches.extend(stack)
        return plug

    yield aim
    for p in patches:
        p.stop()


async def test_ft_search_lists_the_plug(aim_at, capsys) -> None:
    await aim_at()
    await asyncio.to_thread(cli.ft_search, 0.3, "127.0.0.1", False)
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # the plug answers twice, shown once
    assert "02:00:00:00:00:02" in out and "ECO-00000002" in out and "1.6.0" in out
    assert FAKE_FT["password"] not in out


async def test_ft_search_masked_hides_identifiers(aim_at, capsys) -> None:
    await aim_at()
    await asyncio.to_thread(cli.ft_search, 0.3, "127.0.0.1", True)
    out = capsys.readouterr().out
    assert "firmware 1.6.0" in out
    for hidden in ("02:00:00:00:00:02", "ECO-00000002", "Test FT Plug", "127.0.0.1"):
        assert hidden not in out


async def test_ft_search_finds_nothing(aim_at) -> None:
    await aim_at(silent=True)
    with pytest.raises(SystemExit, match=r"no v1\.x plug"):
        await asyncio.to_thread(cli.ft_search, 0.2, "127.0.0.1", False)


@pytest.mark.parametrize("on", [True, False])
async def test_ft_status(aim_at, capsys, on: bool) -> None:
    await aim_at(on=on)
    await asyncio.to_thread(cli.ft_status, None, "127.0.0.1")
    out = capsys.readouterr().out
    assert f"state: {'on' if on else 'off'}" in out
    assert "DST flag off" in out and "s from this computer" in out


async def test_ft_status_selects_by_mac(aim_at) -> None:
    await aim_at()
    with pytest.raises(SystemExit, match="no matching"):
        await asyncio.to_thread(cli.ft_status, "aa:bb:cc:dd:ee:ff", "127.0.0.1")


@pytest.mark.parametrize("on", [True, False])
async def test_ft_set_power_is_confirmed(aim_at, capsys, on: bool) -> None:
    plug = await aim_at(on=not on)
    await asyncio.to_thread(cli.ft_set_power, on, None, "127.0.0.1")
    assert plug.on is on
    assert f"ACK: plug reports {'on' if on else 'off'}" in capsys.readouterr().out


async def test_ft_set_power_refused(aim_at) -> None:
    plug = await aim_at(on=False, set_mark=3)
    with pytest.raises(SystemExit, match="refused"):
        await asyncio.to_thread(cli.ft_set_power, True, None, "127.0.0.1")
    assert plug.on is False


async def test_ft_set_power_unchanged(aim_at) -> None:
    await aim_at(on=False, stuck=True)
    with pytest.raises(SystemExit, match="did not reach"):
        await asyncio.to_thread(cli.ft_set_power, True, None, "127.0.0.1")


async def test_ft_silent_plug(aim_at) -> None:
    plug = await aim_at()
    plug.silent = False
    # Found by search, then goes quiet before the first command.
    original = cli._ft_find

    def find_then_silence(*args):
        found = original(*args)
        plug.silent = True
        return found

    with (
        patch.object(cli, "_ft_find", find_then_silence),
        pytest.raises(SystemExit, match="did not answer"),
    ):
        await asyncio.to_thread(cli.ft_status, None, "127.0.0.1")


async def test_ft_search_uses_the_given_broadcast_address(aim_at, capsys) -> None:
    plug = await aim_at()
    with patch.object(cli.F, "SEARCH_PORTS", (plug.port,)):
        await asyncio.to_thread(cli.ft_search, 0.3, None, False, "127.0.0.1")
    assert "ECO-00000002" in capsys.readouterr().out


async def test_ft_search_hints_at_vpns_when_nothing_answers(aim_at) -> None:
    await aim_at(silent=True)
    with pytest.raises(SystemExit, match="--host"):
        await asyncio.to_thread(cli.ft_search, 0.2, "127.0.0.1", False)


async def test_ft_timers(aim_at, capsys) -> None:
    await aim_at(
        timers=make_timers_payload(make_timer_entry(mask=0x60)),
        random=make_random_payload(random=True),
    )
    await asyncio.to_thread(cli.ft_timers, None, "127.0.0.1")
    out = capsys.readouterr().out
    assert "1 timer(s)" in out
    assert "mon,sun: on at 08:00:00, off at 20:30:00" in out
    assert "random timers: on, vacation mode: off" in out


async def test_ft_timers_unexpected_reply(aim_at) -> None:
    await aim_at(timers=b"\x00" * 5, random=make_random_payload())
    with pytest.raises(SystemExit, match="unexpected timer"):
        await asyncio.to_thread(cli.ft_timers, None, "127.0.0.1")

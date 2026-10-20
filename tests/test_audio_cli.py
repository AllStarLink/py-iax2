"""Tests for iax2.audio helpers and the iax2-tool CLI."""

from __future__ import annotations

import asyncio

import pytest

import iax2
from iax2 import audio, cli


def test_tone_rms_resample_wav(tmp_path):
    t = audio.tone(1000, 100, amplitude=0.5)
    assert len(t) == 1600
    assert 11000 < audio.rms(t) < 12500  # 0.5 * 32767 / sqrt(2)
    assert audio.rms(audio.silence(20)) == 0
    up = audio.resample(t, 8000, 16000)
    assert len(up) == 3200
    p = tmp_path / "x.wav"
    audio.write_wav(p, t)
    assert audio.read_wav(p) == t
    assert len(audio.read_wav(p, 16000)) == 3200


def test_cli_decode(capsys):
    assert cli.main(["decode", "8123", "0000", "01020304", "05", "06", "06", "02"]) == 0
    assert "IAX/PING 291->0" in capsys.readouterr().out


def test_cli_parser_hostport():
    assert cli._hostport("example.org") == ("example.org", 4569)
    assert cli._hostport("1.2.3.4:5000") == ("1.2.3.4", 5000)
    assert cli._hostport("[::1]:5000") == ("::1", 5000)


def test_cli_poke_and_call(capsys):
    async def run():
        server = iax2.Endpoint("127.0.0.1", 0)
        await server.start()
        server.on_call = lambda c: c.answer()
        try:
            loop = asyncio.get_running_loop()
            rc = await loop.run_in_executor(
                None, cli.main, ["-b", "127.0.0.1:0", "poke", f"127.0.0.1:{server.port}"])
            assert rc == 0
            rc = await loop.run_in_executor(
                None, cli.main, ["-b", "127.0.0.1:0", "call", f"127.0.0.1:{server.port}", "1",
                                 "--tone", "440", "-d", "0.3"])
            assert rc == 0
        finally:
            await server.close()

    asyncio.run(run())
    out = capsys.readouterr().out
    assert "PONG from 127.0.0.1" in out and "answered, format ['ulaw']" in out

"""Interoperability tests against Asterisk (see setup_asterisk.py for the dialplan)."""

from __future__ import annotations

import asyncio
import math
import struct

import pytest

import iax2
from iax2 import Cause, Format
from iax2.events import ControlEvent, DTMFEvent, Ringing, TextEvent

from .conftest import hangup_test_channels

pytestmark = pytest.mark.interop

MD5 = {"username": "pyiax-md5", "secret": "pyiaxsecret", "context": "pyiax-test"}


def tone(freq: float, n: int, rate: int = 8000, amp: int = 8000) -> bytes:
    return struct.pack(
        f"<{n}h", *(int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    )


def goertzel(pcm: bytes, freq: float, rate: int = 8000) -> float:
    """Relative power of ``freq`` in ``pcm`` (normalised by total energy)."""
    s = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    k = 2 * math.cos(2 * math.pi * freq / rate)
    q1 = q2 = 0.0
    for x in s:
        q1, q2 = x + k * q1 - q2, q1
    power = q1 * q1 + q2 * q2 - k * q1 * q2
    energy = sum(x * x for x in s) * len(s) / 2 or 1
    return power / energy


async def collect_audio(call, seconds):
    out = []
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        f = await call.recv_audio(timeout=max(0.01, end - loop.time()))
        if f is None:
            if call.is_ended:
                break
            continue
        out.append(f)
    return out


async def test_md5_echo(ep):
    call = await ep.dial("127.0.0.1", "100", **MD5, caller_number="4242", timeout=5)
    assert call.auth_method is iax2.AuthMethod.MD5
    assert call.format == Format.ULAW
    sent = tone(440, 160 * 50)
    player = asyncio.create_task(call.play_pcm(sent))
    frames = await collect_audio(call, 1.4)
    await player
    assert len(frames) >= 40
    pcm = b"".join(f.pcm for f in frames[5:35])
    assert goertzel(pcm, 440) > 0.5
    await call.hangup()


async def test_slin_codec_negotiation(ep):
    call = await ep.dial("127.0.0.1", "100", **MD5, formats=[Format.SLIN], timeout=5)
    assert call.format == Format.SLIN
    player = asyncio.create_task(call.play_pcm(tone(700, 160 * 40)))
    frames = await collect_audio(call, 1.0)
    await player
    pcm = b"".join(f.pcm for f in frames[5:30])
    assert frames and frames[0].format == Format.SLIN
    assert goertzel(pcm, 700) > 0.5  # proves big-endian wire order is right
    await call.hangup()


async def test_plaintext_alaw(ep):
    call = await ep.dial(
        "127.0.0.1",
        "100",
        username="pyiax-plain",
        secret="plainpw",
        context="pyiax-test",
        timeout=5,
    )
    assert call.auth_method is iax2.AuthMethod.PLAINTEXT
    assert call.format == Format.ALAW
    await call.hangup()


async def test_rsa(ep, rsa_key):
    call = await ep.dial(
        "127.0.0.1",
        "100",
        username="pyiax-rsa",
        private_key=rsa_key,
        context="pyiax-test",
        timeout=5,
    )
    assert call.auth_method is iax2.AuthMethod.RSA
    await call.hangup()


async def test_wrong_secret(ep):
    with pytest.raises(iax2.CallRejected) as ei:
        await ep.dial(
            "127.0.0.1",
            "100",
            username="pyiax-md5",
            secret="nope",
            context="pyiax-test",
            timeout=10,
        )
    assert ei.value.cause_code == Cause.FACILITY_NOT_SUBSCRIBED
    assert ei.value.cause == "No authority found"


async def test_ringing_and_tone(ep):
    call = await ep.dial("127.0.0.1", "101", **MD5, wait="accepted", timeout=5)
    ev = await call.next_event(timeout=3)
    seen = [type(ev)]
    while call.state is not iax2.CallState.ANSWERED and ev is not None:
        ev = await call.next_event(timeout=3)
        seen.append(type(ev))
    assert Ringing in seen
    frames = await collect_audio(call, 1.0)
    pcm = b"".join(f.pcm for f in frames[10:40])
    assert goertzel(pcm, 1000) > 0.5
    await asyncio.wait_for(call.wait_ended(), 5)
    assert call.cause_code == Cause.NORMAL_CLEARING


async def test_busy(ep):
    call = await ep.dial("127.0.0.1", "102", **MD5, wait="accepted", timeout=5)
    controls = [ev.control async for ev in call.events() if isinstance(ev, ControlEvent)]
    assert iax2.ControlType.BUSY in controls


async def test_congestion(ep):
    call = await ep.dial("127.0.0.1", "105", **MD5, wait="accepted", timeout=5)
    controls = [ev.control async for ev in call.events() if isinstance(ev, ControlEvent)]
    assert iax2.ControlType.CONGESTION in controls


async def test_text_and_dtmf_from_asterisk(ep):
    call = await ep.dial("127.0.0.1", "103", **MD5, timeout=5)
    texts, digits = [], []
    async for ev in call.events():
        if isinstance(ev, TextEvent):
            texts.append(ev.text)
        elif isinstance(ev, DTMFEvent) and not ev.begin:
            digits.append(ev.digit)
    assert texts == ["hello from asterisk"]
    assert "".join(digits) == "123#"
    assert call.cause_code == Cause.NORMAL_CLEARING


async def test_dtmf_variables_callerid_to_asterisk(ep):
    call = await ep.dial(
        "127.0.0.1", "104", **MD5, caller_number="5551212", variables={"PYVAR": "hello"}, timeout=5
    )
    # Asterisk's (forced) jitter buffer only releases DTMF while voice flows,
    # exactly as with any other IAX2 client, so stream silence meanwhile.
    player = asyncio.create_task(call.play_pcm(bytes(320 * 150)))
    await asyncio.sleep(0.3)
    await call.send_dtmf("7")
    ev = None
    async for ev in call.events():
        if isinstance(ev, TextEvent):
            break
    assert isinstance(ev, TextEvent)
    assert ev.text == "got 7 var=hello cid=5551212"
    player.cancel()
    await call.hangup()


async def test_reject_before_answer(ep):
    with pytest.raises(iax2.CallRejected) as ei:
        await ep.dial("127.0.0.1", "107", **MD5, timeout=5)
    assert ei.value.cause_code == Cause.CALL_REJECTED


async def test_hangup_by_asterisk_cause(ep):
    call = await ep.dial("127.0.0.1", "108", **MD5, timeout=5)
    await asyncio.wait_for(call.wait_ended(), 5)
    assert call.cause_code == Cause.USER_BUSY


async def test_poke(ep):
    rtt = await ep.poke("127.0.0.1", 4569)
    assert rtt < 0.5


async def test_register(ep, asterisk):
    reg = await ep.register("127.0.0.1", "pyiax-md5", "pyiaxsecret")
    assert reg.registered and reg.refresh > 0
    assert reg.apparent_addr == ("127.0.0.1", ep.port)
    out = asterisk("iax2 show peer pyiax-md5")
    assert (
        f"127.0.0.1:{ep.port}" in out.replace(" Port", ":").replace("  ", "") or str(ep.port) in out
    )
    await reg.stop(unregister=True)
    assert str(ep.port) not in asterisk("iax2 show peer pyiax-md5")


async def test_register_bad_secret(ep):
    with pytest.raises(iax2.RegistrationError):
        await ep.register("127.0.0.1", "pyiax-md5", "wrong", timeout=10)


async def test_long_call_keepalives(ep):
    """Asterisk pings every 21 s and sends LAGRQ every 10 s; we must answer both."""
    call = await ep.dial("127.0.0.1", "106", **MD5, timeout=5)
    await asyncio.sleep(23)
    assert not call.is_ended
    assert call.stats.rtt_ms is not None  # our own PING got a PONG
    assert call.stats.lag_ms is not None
    await call.hangup()


async def test_asterisk_calls_us(asterisk):
    """Asterisk dials IAX2/pyiax-out (us) and bridges to its echo test."""
    from .setup_asterisk import LIB_PORT

    ep = iax2.Endpoint(
        "127.0.0.1",
        LIB_PORT,
        users={"asterisk": iax2.Credentials(secret="outsecret")},
        allow_guest=False,
    )
    await ep.start()
    try:
        asterisk("channel originate IAX2/pyiax-out/500 extension 100@pyiax-test")
        call = await ep.accept_call(timeout=10)
        assert call is not None
        assert call.auth_method is iax2.AuthMethod.MD5
        assert call.info.called_number == "500"
        assert call.info.username == "asterisk"
        await call.answer()
        player = asyncio.create_task(call.play_pcm(tone(500, 160 * 50)))
        frames = await collect_audio(call, 1.4)
        await player
        assert goertzel(b"".join(f.pcm for f in frames[10:40]), 500) > 0.5
        await call.hangup()
    finally:
        await ep.close()


async def test_trunk_send_and_receive(asterisk):
    """Register as the trunk=yes friend, then exchange trunked audio with the echo test."""
    ep = iax2.Endpoint("127.0.0.1", 0, trunk_peers={("127.0.0.1", 4569)})
    await ep.start()
    try:
        await ep.register("127.0.0.1", "pyiax-trunk", "pyiaxsecret")
        call = await ep.dial(
            "127.0.0.1",
            "100",
            username="pyiax-trunk",
            secret="pyiaxsecret",
            context="pyiax-test",
            timeout=5,
        )
        assert call.session.trunk
        player = asyncio.create_task(call.play_pcm(tone(600, 160 * 50)))
        frames = await collect_audio(call, 1.4)
        await player
        assert len(frames) > 30
        assert goertzel(b"".join(f.pcm for f in frames[10:30]), 600) > 0.5
        await call.hangup()
    finally:
        await ep.close()


async def test_native_transfer_of_asterisk_calls(asterisk):
    """We bridge two calls from Asterisk and transfer them onto each other.

    Asterisk 22 never *starts* native transfers, but its endpoint side
    (try_transfer/complete_transfer) must accept ours.
    """
    from .setup_asterisk import LIB_PORT

    ep = iax2.Endpoint(
        "127.0.0.1",
        LIB_PORT,
        users={"asterisk": iax2.Credentials(secret="outsecret")},
        allow_guest=False,
    )
    await ep.start()
    try:
        calls = []
        for n in ("501", "502"):
            asterisk(f"channel originate IAX2/pyiax-out/{n} extension 106@pyiax-test")
            c = await ep.accept_call(timeout=10)
            assert c is not None
            await c.answer()
            calls.append(c)
        await asyncio.sleep(0.3)
        ids = {c.peer_callno for c in calls}
        ep.transfers.start_transfer(*calls)
        for c in calls:
            await asyncio.wait_for(c.wait_ended(), 5)
            assert c.cause == "transferred"
        await asyncio.sleep(0.5)
        out = asterisk("iax2 show channels")
        # each Asterisk leg now has the other leg as its remote call number
        a, b = sorted(ids)
        assert f"{a:05d}/{b:05d}" in out and f"{b:05d}/{a:05d}" in out
    finally:
        await ep.close()
        hangup_test_channels()


ENC = iax2.Encryption.AES128 | iax2.Encryption.KEYROTATE


async def test_encrypted_call_to_asterisk(asterisk):
    ep = iax2.Endpoint("127.0.0.1", 0, encryption=ENC)
    await ep.start()
    try:
        call = await ep.dial(
            "127.0.0.1",
            "100",
            username="pyiax-enc",
            secret="encsecret",
            context="pyiax-test",
            timeout=5,
        )
        assert call.session.crypto is not None
        player = asyncio.create_task(call.play_pcm(tone(440, 160 * 50)))
        frames = await collect_audio(call, 1.4)
        await player
        assert goertzel(b"".join(f.pcm for f in frames[5:35]), 440) > 0.5
        assert "pyiax-enc" in asterisk("iax2 show channels")
        # Rotate our transmit key (RTKEY) and check Asterisk keeps decrypting.
        from iax2 import crypto

        crypto._rotate(call.session)
        player = asyncio.create_task(call.play_pcm(tone(880, 160 * 50)))
        frames = await collect_audio(call, 1.4)
        await player
        assert goertzel(b"".join(f.pcm for f in frames[10:35]), 880) > 0.5
        await call.hangup()
    finally:
        await ep.close()


async def test_unencrypted_call_refused_by_asterisk(ep):
    with pytest.raises(iax2.CallRejected):
        await ep.dial(
            "127.0.0.1",
            "100",
            username="pyiax-enc",
            secret="encsecret",
            context="pyiax-test",
            timeout=10,
        )


async def test_encrypted_call_from_asterisk(asterisk):
    from .setup_asterisk import LIB_PORT

    ep = iax2.Endpoint(
        "127.0.0.1",
        LIB_PORT,
        users={"asterisk": iax2.Credentials(secret="outsecret")},
        allow_guest=False,
        encryption=ENC,
        force_encryption=True,
    )
    await ep.start()
    try:
        asterisk("channel originate IAX2/pyiax-out-enc/500 extension 100@pyiax-test")
        call = await ep.accept_call(timeout=10)
        assert call is not None and call.session.crypto is not None
        await call.answer()
        player = asyncio.create_task(call.play_pcm(tone(500, 160 * 50)))
        frames = await collect_audio(call, 1.4)
        await player
        assert goertzel(b"".join(f.pcm for f in frames[10:40]), 500) > 0.5
        await call.hangup()
    finally:
        await ep.close()
        hangup_test_channels()


async def test_allstarlink_app_rpt_client(asterisk, ep):
    """Connect to the local app_rpt node like iaxRpt does (needs an ASL3 node)."""
    nodes = asterisk("rpt localnodes").split()
    node = next((n for n in nodes if n.isdigit()), None)
    if node is None:
        pytest.skip("no local app_rpt node")
    call = await ep.dial(
        "127.0.0.1",
        node,
        username="pyiax-rpt",
        secret="rptsecret",
        caller_number="pyiax",
        timeout=10,
    )
    texts = []
    call.on_event = lambda ev: texts.append(ev.text) if isinstance(ev, TextEvent) else None
    await asyncio.sleep(1)
    call.radio_key()
    await call.play_pcm(tone(800, 160 * 25))
    call.radio_unkey()
    await asyncio.sleep(2)
    assert any(t.startswith("L") for t in texts)  # app_rpt link-list text
    assert not call.is_ended
    await call.hangup()

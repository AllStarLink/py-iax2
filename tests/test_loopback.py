"""End-to-end tests: two endpoints talking over the loopback interface."""

from __future__ import annotations

import asyncio
import struct

import pytest

import iax2
from iax2 import Cause, Credentials, Format
from iax2.events import Answered, DTMFEvent, HangupEvent, Ringing, TextEvent


@pytest.fixture
async def pair():
    server = iax2.Endpoint("127.0.0.1", 0, users={"alice": Credentials(secret="s3cret")},
                           allow_guest=False)
    client = iax2.Endpoint("127.0.0.1", 0)
    await server.start()
    await client.start()
    yield server, client
    await client.close()
    await server.close()


async def _answer_one(server, *, ring=False):
    call = await server.accept_call(timeout=5)
    assert call is not None
    if ring:
        call.ring()
    await call.answer()
    return call


async def test_call_md5_calltoken_answer_hangup(pair):
    server, client = pair
    srv_task = asyncio.create_task(_answer_one(server, ring=True))
    call = await client.dial("127.0.0.1", "1000", port=server.port, username="alice",
                             secret="s3cret", caller_number="555", caller_name="Test",
                             timeout=5)
    scall = await srv_task
    assert call.state is iax2.CallState.ANSWERED
    assert call.auth_method is iax2.AuthMethod.MD5
    assert scall.info.called_number == "1000"
    assert scall.info.calling_name == "Test"
    assert scall.info.username == "alice"
    assert call.format == scall.format == Format.ULAW

    events = []
    async def collect():
        async for ev in scall.events():
            events.append(ev)
    t = asyncio.create_task(collect())
    await call.hangup()
    await asyncio.wait_for(scall.wait_ended(), 5)
    await t
    assert isinstance(events[-1], HangupEvent)
    assert events[-1].cause_code == Cause.NORMAL_CLEARING
    assert scall.session.destroyed
    await asyncio.sleep(0.1)
    assert call.session.destroyed
    assert not client._sessions and not server._sessions


async def test_bad_password_rejected(pair):
    server, client = pair
    with pytest.raises(iax2.CallRejected) as ei:
        await client.dial("127.0.0.1", "1000", port=server.port, username="alice",
                          secret="wrong", timeout=5)
    assert ei.value.cause_code == Cause.FACILITY_NOT_SUBSCRIBED


async def test_unknown_user_rejected(pair):
    server, client = pair
    with pytest.raises(iax2.CallRejected):
        await client.dial("127.0.0.1", "1000", port=server.port, username="mallory", timeout=5)


async def test_audio_dtmf_text_both_ways(pair):
    server, client = pair
    srv_task = asyncio.create_task(_answer_one(server))
    call = await client.dial("127.0.0.1", "1", port=server.port, username="alice",
                             secret="s3cret", timeout=5)
    scall = await srv_task
    pcm = struct.pack("<160h", *([1000, -1000] * 80))
    for _ in range(10):
        call.send_pcm(pcm)
        await asyncio.sleep(0.005)
    frames = []
    for _ in range(10):
        f = await scall.recv_audio(timeout=2)
        assert f is not None
        frames.append(f)
    assert frames[0].full and not frames[1].full  # first full, then mini frames
    assert all(len(f.payload) == 160 for f in frames)
    assert abs(struct.unpack("<160h", frames[3].pcm)[0] - 1000) < 40
    ts = [f.timestamp for f in frames]
    assert ts == sorted(ts) and ts[1] - ts[0] == 20

    scall.send_pcm(pcm)
    back = await call.recv_audio(timeout=2)
    assert back is not None and back.format == Format.ULAW

    await call.send_dtmf("5#", duration=0.01, gap=0.01)
    call.send_text("hello")
    got = []
    while len(got) < 5:
        ev = await scall.next_event(timeout=2)
        assert ev is not None
        if isinstance(ev, (DTMFEvent, TextEvent)):
            got.append(ev)
    assert [(e.digit, e.begin) for e in got[:4]] == [("5", True), ("5", False), ("#", True),
                                                     ("#", False)]
    assert got[4].text == "hello"
    await scall.hangup()
    await asyncio.wait_for(call.wait_ended(), 5)
    assert call.cause_code == Cause.NORMAL_CLEARING


async def test_ringing_event_and_reject(pair):
    server, client = pair

    async def ring_then_reject():
        c = await server.accept_call(timeout=5)
        c.ring()
        await asyncio.sleep(0.05)
        c.reject("Busy here", Cause.USER_BUSY)

    t = asyncio.create_task(ring_then_reject())
    call = await client.dial("127.0.0.1", "1", port=server.port, username="alice",
                             secret="s3cret", wait="accepted", timeout=5)
    ev_types = []
    async for ev in call.events():
        ev_types.append(type(ev))
    await t
    assert Ringing in ev_types and HangupEvent in ev_types and Answered not in ev_types
    assert call.cause == "Busy here" and call.cause_code == Cause.USER_BUSY


async def test_poke(pair):
    server, client = pair
    rtt = await client.poke("127.0.0.1", server.port)
    assert 0 <= rtt < 1
    await asyncio.sleep(0.05)
    assert not client._sessions


async def test_registration(pair):
    server, client = pair
    registrar = server.enable_registrar()
    reg = await client.register("127.0.0.1", "alice", "s3cret", port=server.port, refresh=60)
    assert reg.registered
    assert registrar.lookup("alice") == ("127.0.0.1", client.port)
    assert reg.apparent_addr == ("127.0.0.1", client.port)
    await reg.stop(unregister=True)
    assert registrar.lookup("alice") is None


async def test_registration_rejected(pair):
    server, client = pair
    server.enable_registrar()
    with pytest.raises(iax2.RegistrationError):
        await client.register("127.0.0.1", "alice", "nope", port=server.port)
    with pytest.raises(iax2.RegistrationError):
        await client.register("127.0.0.1", "nobody", "x", port=server.port)


async def test_inval_for_unknown_call(pair):
    server, client = pair
    # A HANGUP to a call number that doesn't exist must provoke INVAL.
    s = client.new_session(("127.0.0.1", server.port))
    s.peer_callno = 999
    s.release_signaling()
    s.send_command(iax2.IAXCommand.HANGUP)
    await asyncio.sleep(0.2)
    assert s.destroyed and s.error == "invalid"


async def test_retransmit_then_timeout():
    ep = iax2.Endpoint("127.0.0.1", 0, timing=iax2.Timing(default_retry_ms=50, min_retry_ms=10,
                                                          max_retry_ms=100))
    await ep.start()
    # nobody listens on this port
    import socket
    dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    try:
        with pytest.raises(iax2.CallTimeout):
            await ep.dial("127.0.0.1", "1", port=port, timeout=5)
        data = []
        dead.setblocking(False)
        while True:
            try:
                data.append(dead.recv(2048))
            except BlockingIOError:
                break
        assert len(data) == 4  # one send + three retransmits, like Asterisk
        assert not data[0][2] & 0x80 and all(d[2] & 0x80 for d in data[1:])
    finally:
        dead.close()
        await ep.close()

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


async def test_native_transfer_three_endpoints():
    """A and B call server S; S transfers; A and B then talk directly."""
    srv = iax2.Endpoint("127.0.0.1", 0)
    a = iax2.Endpoint("127.0.0.1", 0)
    b = iax2.Endpoint("127.0.0.1", 0)
    for e in (srv, a, b):
        await e.start()
    try:
        async def answer():
            c = await srv.accept_call(timeout=5)
            await c.answer()
            return c
        t1 = asyncio.create_task(answer())
        ca = await a.dial("127.0.0.1", "1", port=srv.port, timeout=5)
        sa = await t1
        t2 = asyncio.create_task(answer())
        cb = await b.dial("127.0.0.1", "2", port=srv.port, timeout=5)
        sb = await t2
        srv.transfers.start_transfer(sa, sb)
        await asyncio.wait_for(sa.wait_ended(), 5)
        await asyncio.wait_for(sb.wait_ended(), 5)
        assert sa.cause == "transferred"
        await asyncio.sleep(0.2)
        assert ca.peer_addr == ("127.0.0.1", b.port)
        assert cb.peer_addr == ("127.0.0.1", a.port)
        assert ca.peer_callno == cb.callno and cb.peer_callno == ca.callno
        assert not ca.is_ended and not cb.is_ended
        # media now flows directly A <-> B
        pcm = struct.pack("<160h", *([500] * 160))
        for _ in range(3):
            ca.send_pcm(pcm)
            await asyncio.sleep(0.02)
        f = await cb.recv_audio(timeout=2)
        assert f is not None and f.full
        cb.send_text("direct")
        while True:
            ev = await ca.next_event(timeout=2)
            assert ev is not None
            if isinstance(ev, TextEvent):
                assert ev.text == "direct"
                break
        await ca.hangup()
        await asyncio.wait_for(cb.wait_ended(), 5)
        assert not srv._sessions
    finally:
        for e in (a, b, srv):
            await e.close()


async def test_transfer_refused_when_disabled():
    srv = iax2.Endpoint("127.0.0.1", 0)
    a = iax2.Endpoint("127.0.0.1", 0)
    b = iax2.Endpoint("127.0.0.1", 0)
    for e in (srv, a, b):
        await e.start()
    a.transfers.enabled = False
    try:
        async def answer():
            c = await srv.accept_call(timeout=5)
            await c.answer()
            return c
        t1 = asyncio.create_task(answer())
        ca = await a.dial("127.0.0.1", "1", port=srv.port, timeout=5)
        sa = await t1
        t2 = asyncio.create_task(answer())
        await b.dial("127.0.0.1", "2", port=srv.port, timeout=5)
        sb = await t2
        srv.transfers.start_transfer(sa, sb)
        await asyncio.sleep(0.5)
        assert not sa.is_ended and not sb.is_ended
        assert srv.transfers.state(sa) is srv.transfers.state(sb)
        assert ca.peer_addr == ("127.0.0.1", srv.port)
    finally:
        for e in (a, b, srv):
            await e.close()


async def test_video_full_then_mini(pair):
    server, client = pair
    srv_task = asyncio.create_task(_answer_one(server))
    call = await client.dial("127.0.0.1", "1", port=server.port, username="alice",
                             secret="s3cret", timeout=5)
    scall = await srv_task
    got = []
    scall.on_video = got.append
    for i in range(3):
        call.send_video(b"slice%d" % i, Format.H264, mark=(i == 2))
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)
    assert [v.payload for v in got] == [b"slice0", b"slice1", b"slice2"]
    assert all(v.format == Format.H264 for v in got)
    assert [v.mark for v in got] == [False, False, True]
    await call.hangup()


async def test_dpreq_dprep(pair):
    server, client = pair
    server.dialplan = lambda call, number: iax2.constants.DPStatus.EXISTS if number == "100" \
        else iax2.constants.DPStatus.NONEXISTENT
    srv_task = asyncio.create_task(_answer_one(server))
    call = await client.dial("127.0.0.1", "1", port=server.port, username="alice",
                             secret="s3cret", timeout=5)
    await srv_task
    assert await call.query_dialplan("100") & iax2.constants.DPStatus.EXISTS
    assert await call.query_dialplan("999") & iax2.constants.DPStatus.NONEXISTENT
    await call.hangup()


async def test_encrypted_call_and_key_rotation():
    enc = iax2.Encryption.AES128 | iax2.Encryption.KEYROTATE
    server = iax2.Endpoint("127.0.0.1", 0, users={"bob": Credentials(secret="a;pw")},
                           encryption=enc, force_encryption=True)
    client = iax2.Endpoint("127.0.0.1", 0, encryption=enc)
    await server.start()
    await client.start()
    try:
        t = asyncio.create_task(_answer_one(server))
        call = await client.dial("127.0.0.1", "1", port=server.port, username="bob",
                                 secret="pw", timeout=5)
        scall = await t
        assert call.session.crypto is not None and scall.session.crypto is not None
        assert call.session.crypto.tx_key == scall.session.crypto.rx_key
        pcm = struct.pack("<160h", *([1200] * 160))
        call.send_pcm(pcm)
        call.send_pcm(pcm)
        f1 = await scall.recv_audio(timeout=2)
        f2 = await scall.recv_audio(timeout=2)
        assert f1 is not None and f2 is not None and not f2.full
        from iax2 import crypto
        crypto._rotate(call.session)  # force an RTKEY
        await asyncio.sleep(0.1)
        assert call.session.crypto.tx_key == scall.session.crypto.rx_key
        call.send_pcm(pcm)
        assert await scall.recv_audio(timeout=2) is not None
        call.send_text("secret text")
        while True:
            ev = await scall.next_event(timeout=2)
            assert ev is not None
            if isinstance(ev, TextEvent):
                assert ev.text == "secret text"
                break
        await call.hangup()
        await asyncio.wait_for(scall.wait_ended(), 5)
    finally:
        await client.close()
        await server.close()


async def test_force_encryption_rejects_plain_caller():
    server = iax2.Endpoint("127.0.0.1", 0, users={"bob": Credentials(secret="pw")},
                           encryption=iax2.Encryption.AES128, force_encryption=True)
    client = iax2.Endpoint("127.0.0.1", 0)
    await server.start()
    await client.start()
    try:
        with pytest.raises(iax2.CallRejected):
            await client.dial("127.0.0.1", "1", port=server.port, username="bob", secret="pw",
                              timeout=5)
    finally:
        await client.close()
        await server.close()

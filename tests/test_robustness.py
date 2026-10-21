"""Hostile-input tests: malformed datagrams, fuzzed IEs and call/registration floods.

The parser must reject garbage with :class:`~iax2.FrameError` only, and an
endpoint must never let an exception escape its datagram handler (which would
otherwise be logged with a traceback per packet - a log-flooding vector).
"""

from __future__ import annotations

import asyncio
import random

import pytest

import iax2
from iax2 import Credentials, Format, IAXCommand
from iax2.constants import IE, FrameType
from iax2.exceptions import FrameError
from iax2.frames import FullFrame, MiniFrame, TrunkEntry, TrunkFrame, VideoFrame, parse_datagram
from iax2.ies import IEs

SEED = 0x1A2

# Every IE id Asterisk defines, plus some it doesn't.
IE_IDS = [int(i) for i in IE] + [0, 0x7F, 0xFE, 0xFF]
COMMANDS = [int(c) for c in IAXCommand] + [0, 0x7E]  # wire-encodable


def _random_ies(rng: random.Random) -> bytes:
    """Well-formed TLV framing with random ids/contents (reaches the value decoders)."""
    out = bytearray()
    for _ in range(rng.randint(0, 8)):
        n = rng.choice([0, 1, 2, 4, 6, 16, 28, rng.randint(0, 255)])
        out += bytes([rng.choice(IE_IDS), n]) + rng.randbytes(n)
    return bytes(out)


def _corpus() -> list[bytes]:
    ies = (
        IEs()
        .add(IE.VERSION, 2)
        .add(IE.CALLED_NUMBER, "1000")
        .add(IE.USERNAME, "alice")
        .add(IE.FORMAT, int(Format.ULAW))
        .add(IE.CAPABILITY, int(Format.ULAW | Format.ALAW))
        .add(IE.CALLTOKEN, "")
    )
    return [
        FullFrame.iax(IAXCommand.NEW, ies, source_call=5, timestamp=3).to_bytes(),
        FullFrame.iax(IAXCommand.REGREQ, IEs().add(IE.USERNAME, "u"), source_call=7).to_bytes(),
        FullFrame(
            FrameType.VOICE, int(Format.ULAW), source_call=5, dest_call=9, payload=bytes(160)
        ).to_bytes(),
        MiniFrame(5, 1234, bytes(160)).to_bytes(),
        VideoFrame(5, 1234, True, bytes(40)).to_bytes(),
        TrunkFrame(99, [TrunkEntry(3, b"aaaa", 10), TrunkEntry(4, b"bb", 20)], True).to_bytes(),
        TrunkFrame(99, [TrunkEntry(3, b"aaaa"), TrunkEntry(4, b"bb")], False).to_bytes(),
    ]


def _mutate(rng: random.Random, data: bytes) -> bytes:
    b = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(4)
        if op == 0 and b:  # flip a byte
            b[rng.randrange(len(b))] = rng.randrange(256)
        elif op == 1:  # truncate
            del b[rng.randint(0, len(b)) :]
        elif op == 2:  # append junk
            b += rng.randbytes(rng.randint(1, 40))
        elif b:  # corrupt a plausible TLV length byte
            b[rng.randrange(len(b))] = rng.choice([0, 1, 0x7F, 0xFF])
    return bytes(b)


def _fuzz_inputs(n: int) -> list[bytes]:
    rng = random.Random(SEED)
    corpus = _corpus()
    out = []
    for i in range(n):
        if i % 3 == 0:
            out.append(rng.randbytes(rng.randint(0, 64)))
        elif i % 3 == 1:
            out.append(_mutate(rng, rng.choice(corpus)))
        else:  # valid full-frame header, random command and IE body
            f = FullFrame(
                FrameType.IAX,
                rng.choice(COMMANDS),
                source_call=rng.randint(1, 32767),
                payload=_random_ies(rng),
            )
            out.append(f.to_bytes())
    return out


def test_parser_rejects_garbage_cleanly():
    unexpected = []
    for data in _fuzz_inputs(20000):
        try:
            frame = parse_datagram(data)
            if isinstance(frame, FullFrame):
                frame.describe()
                if frame.frame_type == FrameType.IAX:
                    ies = frame.ies
                    for ie in ies:
                        ies.get(ie)
        except FrameError:
            pass
        except Exception as exc:
            unexpected.append((data.hex(), repr(exc)))
    assert not unexpected, unexpected[:5]


@pytest.fixture
async def pair():
    server = iax2.Endpoint(
        "127.0.0.1", 0, users={"alice": Credentials(secret="s3cret")}, allow_guest=True
    )
    client = iax2.Endpoint("127.0.0.1", 0)
    await server.start()
    await client.start()
    yield server, client
    await client.close()
    await server.close()


def _feed(ep: iax2.Endpoint, packets, addr) -> list[str]:
    """Deliver packets straight to the endpoint, bypassing its catch-all handler."""
    errors = []
    for data in packets:
        try:
            ep._datagram_received(data, addr)
        except Exception as exc:
            errors.append(f"{data.hex()}: {exc!r}")
    return errors


async def test_endpoint_survives_garbage(pair):
    server, client = pair
    errors = _feed(server, _fuzz_inputs(6000), ("127.0.0.1", 40000))
    await asyncio.sleep(0.2)
    assert not errors, errors[:5]
    assert await client.poke("127.0.0.1", server.port) < 1


async def test_live_call_survives_fuzzed_signalling(pair):
    """In-sequence frames with random commands/IEs aimed at an established call."""
    server, client = pair

    async def connect():
        answered = asyncio.create_task(server.accept_call(timeout=5))
        call = await client.dial("127.0.0.1", "1000", port=server.port, timeout=5, wait="accepted")
        scall = await answered
        assert scall is not None
        return call, scall.session

    rng = random.Random(SEED)
    errors: list[str] = []
    caddr = ("127.0.0.1", client.port)
    delivered = calls = 0
    while delivered < 3000:
        call, s = await connect()
        calls += 1
        while delivered < 3000 and not s.destroyed:
            ftype = rng.choice(
                [FrameType.IAX] * 4
                + [
                    FrameType.CONTROL,
                    FrameType.DTMF_BEGIN,
                    FrameType.TEXT,
                    FrameType.HTML,
                    FrameType.VOICE,
                ]
            )
            sub = rng.choice(COMMANDS) if ftype == FrameType.IAX else rng.randrange(128)
            payload = (
                _random_ies(rng) if ftype == FrameType.IAX else rng.randbytes(rng.randint(0, 40))
            )
            f = FullFrame(
                ftype,
                sub,
                source_call=call.session.callno,
                dest_call=s.callno,
                timestamp=rng.randrange(1 << 32),
                oseqno=s.iseqno,
                iseqno=s.oseqno,
                payload=payload,
            )
            errors += _feed(server, [f.to_bytes()], caddr)
            delivered += 1
            if delivered % 200 == 0:
                await asyncio.sleep(0)  # let spawned tasks (auth, events) run too
        if not call.session.destroyed:
            await call.hangup()
    assert calls < 1500  # most frames must not simply kill the call
    await asyncio.sleep(0.2)
    assert not errors, errors[:5]
    assert await client.poke("127.0.0.1", server.port) < 1


def _new(source_call: int, token: str | None) -> bytes:
    ies = IEs().add(IE.VERSION, 2).add(IE.CALLED_NUMBER, "1000")
    if token is not None:
        ies.add(IE.CALLTOKEN, token)
    return FullFrame.iax(IAXCommand.NEW, ies, source_call=source_call).to_bytes()


async def test_new_flood_without_valid_token_creates_nothing(pair):
    server, _ = pair
    before = len(server._sessions)
    pkts = [_new(i, None) for i in range(1, 1500)]  # no token: REJECT
    pkts += [_new(i, "") for i in range(1, 1500)]  # token request: CALLTOKEN reply
    pkts += [_new(i, "123?deadbeef") for i in range(1, 1500)]  # forged token
    errors = _feed(server, pkts, ("127.0.0.1", 40001))
    assert not errors
    assert len(server._sessions) == before
    assert not server._ip_counts


async def test_per_ip_call_number_limit(pair):
    server, _ = pair
    server.config.max_callnumbers_per_ip = 5
    addr = ("127.0.0.1", 40002)
    for i in range(1, 20):
        _feed(server, [_new(i, server.calltokens.issue(*addr))], addr)
    assert server._ip_counts["127.0.0.1"] == 5
    assert len(server._sessions) == 5
    # Another IP is unaffected.
    other = ("127.0.0.2", 40003)
    assert not _feed(server, [_new(1, server.calltokens.issue(*other))], other)
    assert server._ip_counts["127.0.0.2"] == 1
    # Freed sessions return their slots.
    for s in list(server._sessions.values()):
        s.destroy()
    assert not server._ip_counts


async def test_registration_flood_limited(pair):
    server, _ = pair
    server.enable_registrar()
    server.config.max_callnumbers_per_ip = 3
    addr = ("127.0.0.1", 40004)
    for i in range(1, 50):
        ies = IEs().add(IE.USERNAME, "alice").add(IE.CALLTOKEN, server.calltokens.issue(*addr))
        _feed(server, [FullFrame.iax(IAXCommand.REGREQ, ies, source_call=i).to_bytes()], addr)
    assert server._ip_counts["127.0.0.1"] <= 3


def test_corpus_parses():
    # Guard against the fuzz corpus silently becoming garbage itself.
    for d in _corpus():
        parse_datagram(d)

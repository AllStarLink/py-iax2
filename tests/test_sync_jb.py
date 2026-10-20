"""Tests for the jitter buffer and the blocking wrapper."""

from __future__ import annotations

import struct
import threading
from dataclasses import dataclass

from iax2 import Credentials
from iax2.jitterbuffer import JitterBuffer
from iax2.sync import SyncEndpoint


@dataclass
class F:
    timestamp: int


def test_jb_in_order_constant_delay():
    jb = JitterBuffer(frame_ms=20, min_delay=40)
    out = []
    for i in range(10):
        jb.put(F(i * 20), 1000 + i * 20)
        out += jb.get(1000 + i * 20)
    out += jb.get(1000 + 9 * 20 + 40)  # due time of the last frame
    assert [f.timestamp for f in out] == [i * 20 for i in range(10)]
    assert jb.stats.lost == 0


def test_jb_reorders_and_reports_loss_and_late():
    jb = JitterBuffer(frame_ms=20, min_delay=60, max_delay=60)
    arrivals = [(0, 0), (40, 41), (20, 42), (80, 81), (200, 300)]  # 60 lost; 200 very late? no
    for ts, t in arrivals:
        jb.put(F(ts), t)
    out = jb.get(150)
    assert [None if f is None else f.timestamp for f in out[:5]] == [0, 20, 40, None, 80]
    assert jb.stats.lost >= 1
    jb.put(F(20), 1001)  # already played -> late
    assert jb.stats.late == 1


def test_jb_adapts_to_jitter():
    jb = JitterBuffer(frame_ms=20, min_delay=20, max_delay=400)
    import random

    rnd = random.Random(1)
    for i in range(200):
        jb.put(F(i * 20), i * 20 + rnd.uniform(0, 80))
    assert jb.delay > 60
    assert jb.stats.jitter > 10


def test_jb_duplicate():
    jb = JitterBuffer()
    jb.put(F(0), 0)
    jb.put(F(0), 1)
    assert jb.stats.duplicates == 1 and len(jb) == 1


def test_sync_wrapper_call():
    with SyncEndpoint("127.0.0.1", 0, users={"u": Credentials(secret="p")},
                      allow_guest=False) as server, SyncEndpoint("127.0.0.1", 0) as client:
        result = {}

        def serve():
            call = server.accept_call(timeout=5)
            call.answer()
            f = call.recv_audio(timeout=5)
            result["audio"] = f is not None and len(f.payload) == 160
            call.wait_ended(timeout=5)
            result["cause"] = call.cause_code

        t = threading.Thread(target=serve)
        t.start()
        call = client.dial("127.0.0.1", "9", port=server.port, username="u", secret="p",
                           timeout=5)
        assert call.state.value == "answered"
        call.send_pcm(struct.pack("<160h", *([100] * 160)))
        call.hangup()
        t.join(10)
        assert result == {"audio": True, "cause": 16}
        assert client.poke("127.0.0.1", server.port) < 1

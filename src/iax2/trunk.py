# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Jason D. McCormick
"""IAX2 trunking (meta trunk frames).

Trunking bundles the voice of many calls to the same peer into one datagram
every :attr:`EndpointConfig.trunk_interval` ms, saving per-packet overhead.
Receiving trunk frames is always supported; sending is enabled per peer
address with :attr:`EndpointConfig.trunk_peers` (Asterisk ``trunk=yes``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .formats import format_info
from .frames import TrunkEntry, TrunkFrame

if TYPE_CHECKING:  # pragma: no cover
    from .endpoint import Endpoint
    from .session import Session

__all__ = ["TrunkManager"]

log = logging.getLogger("iax2.trunk")

Addr = tuple[str, int]


def _duration_ms(fmt: int, payload: bytes) -> int:
    """Duration of ``payload`` in ms (20 if it can't be determined)."""
    try:
        info = format_info(fmt)
        n = info.samples_for_bytes(len(payload))
        if n and info.sample_rate:
            return max(1, n * 1000 // info.sample_rate)
    except (KeyError, ValueError):
        pass
    return 20


@dataclass
class _TrunkPeer:
    addr: Addr
    start: float
    entries: list[TrunkEntry] = field(default_factory=list)
    handle: asyncio.TimerHandle | None = None
    rx_start: float | None = None


class TrunkManager:
    """Sends and receives meta trunk frames for an :class:`~iax2.endpoint.Endpoint`."""

    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self._peers: dict[Addr, _TrunkPeer] = {}

    def _peer(self, addr: Addr) -> _TrunkPeer:
        p = self._peers.get(addr)
        if p is None:
            p = _TrunkPeer(addr, self.endpoint.clock())
            self._peers[addr] = p
        return p

    # -- sending ------------------------------------------------------------

    def queue(self, session: Session, ts: int, payload: bytes) -> None:
        """Queue one call's voice payload for the next trunk frame to its peer."""
        p = self._peer(session.addr)
        if not self.endpoint.config.trunk_timestamps and any(
            e.source_call == session.callno for e in p.entries
        ):
            # Without per-call timestamps every entry inherits the trunk frame's
            # timestamp, so two frames of one call must never share a trunk frame
            # (Asterisk's jitter buffer would drop the second as a duplicate).
            if p.handle is not None:
                p.handle.cancel()
            self._flush(p)
        p.entries.append(TrunkEntry(session.callno, payload, ts & 0xFFFF))
        if p.handle is None:
            delay = self.endpoint.config.trunk_interval / 1000
            p.handle = self.endpoint.loop.call_later(delay, self._flush, p)

    def _flush(self, p: _TrunkPeer) -> None:
        p.handle = None
        if not p.entries:
            return
        ts = int((self.endpoint.clock() - p.start) * 1000)
        frame = TrunkFrame(ts, p.entries, self.endpoint.config.trunk_timestamps)
        p.entries = []
        self.endpoint.sendto(frame.to_bytes(), p.addr)

    def close(self) -> None:
        """Cancel pending flushes."""
        for p in self._peers.values():
            if p.handle is not None:
                p.handle.cancel()
        self._peers.clear()

    # -- receiving ----------------------------------------------------------

    def receive(self, frame: TrunkFrame, addr: Addr) -> None:
        """Deliver each entry of a received trunk frame to its session."""
        for e in frame.entries:
            s = self.endpoint.session_for_peer(addr, e.source_call)
            if s is None:
                continue
            if e.timestamp is not None:
                ts = s.unwrap_timestamp(e.timestamp)
            else:
                # "Supermini" entries carry no per-call timestamp: advance the
                # call's receive clock by the trunk frame's position.
                p = self._peer(addr)
                if p.rx_start is None or not frame.timestamp:
                    p.rx_start = self.endpoint.clock() - frame.timestamp / 1000
                base = int((self.endpoint.clock() - p.rx_start) * 1000)
                ts = max(base, s.last + _duration_ms(s.voiceformat, e.payload)) if s.last else base
            s.handle_trunk_entry(e.payload, ts)

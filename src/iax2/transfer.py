# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Native IAX2 transfers (TXREQ / TXCNT / TXACC / TXREADY / TXREL / TXREJ / TXMEDIA).

When a server (usually Asterisk with ``transfer=yes``) bridges two IAX2
calls, it may ask both endpoints to talk to each other directly and drop
out of the media path::

    server -> A: TXREQ(APPARENT_ADDR=B, CALLNO=B's callno, TRANSFERID)
    server -> B: TXREQ(APPARENT_ADDR=A, CALLNO=A's callno, TRANSFERID)
    A -> B: TXCNT(TRANSFERID)          B -> A: TXCNT(TRANSFERID)
    B -> A: TXACC                      A -> B: TXACC
    A -> server: TXREADY(CALLNO)       B -> server: TXREADY(CALLNO)
    server -> A: TXREL(CALLNO=B's)     server -> B: TXREL(CALLNO=A's)

After TXREL each endpoint sends its media (and signalling) straight to the
other. With TXMEDIA only media is redirected. If anything fails an endpoint
sends TXREJ and the call continues through the server.

This module implements the *endpoint* side, matching Asterisk's
``try_transfer()`` / ``complete_transfer()``.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from .constants import IE, FrameType, IAXCommand
from .ies import IEs

if TYPE_CHECKING:  # pragma: no cover
    from .call import Call
    from .endpoint import Endpoint
    from .frames import FullFrame

__all__ = ["TransferManager", "TransferState"]

log = logging.getLogger("iax2.transfer")


class TransferState(enum.Enum):
    """Per-call native transfer state (Asterisk ``transferring``)."""

    NONE = 0
    BEGIN = 1
    READY = 2
    RELEASED = 3
    MBEGIN = 6
    MREADY = 7
    MEDIAPASS = 9


class TransferManager:
    """Handles transfer messages for an endpoint's calls.

    Attributes:
        enabled: Accept native transfer requests (default ``True``, like
            Asterisk's ``transfer=yes``). When ``False`` every TXREQ is
            answered with TXREJ.
    """

    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self.enabled = True
        self._state: dict[int, TransferState] = {}
        self._txid: dict[int, int] = {}

    def state(self, call: Call) -> TransferState:
        """Current transfer state of ``call``."""
        return self._state.get(call.callno, TransferState.NONE)

    def _set(self, call: Call, st: TransferState) -> None:
        self._state[call.callno] = st

    def on_txreq(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        s = call.session
        addr = ies.get(IE.APPARENT_ADDR)
        callno = ies.get(IE.CALLNO)
        if not self.enabled or addr is None or not callno or s.encryptor is not None:
            s.send_command(IAXCommand.TXREJ)
            return
        s.transfer_addr = (addr[0], addr[1])
        s.transfer_callno = callno
        txid = ies.get(IE.TRANSFERID, 0)
        self._txid[call.callno] = txid
        media_only = frame is not None and IE.CALLNO in ies and ies.get(IE.TRANSFERID) is not None \
            and False
        self._set(call, TransferState.MBEGIN if media_only else TransferState.BEGIN)
        # TXCNT goes to the new peer, sent "transfer" style (iseqno 0, retried <= 1 s).
        s.send(FrameType.IAX, IAXCommand.TXCNT, IEs().add(IE.TRANSFERID, txid).to_bytes(),
               transfer=True, seqno=0)

    def on_txcnt(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is not TransferState.NONE:
            call.session.send(FrameType.IAX, IAXCommand.TXACC, b"", transfer=True, seqno=0,
                              now=True)

    def on_txacc(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is TransferState.BEGIN:
            call.session.cancel_transfer_frames()
            call.session.send_command(IAXCommand.TXREADY, IEs().add(IE.CALLNO, call.callno))
            self._set(call, TransferState.READY)

    def on_txready(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        # Only meaningful for a server bridging two calls; endpoints ignore it.
        log.debug("%r: ignoring TXREADY (we are not bridging)", call)

    def on_txrel(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        """Complete the transfer: switch the call to the new peer."""
        s = call.session
        if s.transfer_addr is None:
            return
        peer_callno = ies.get(IE.CALLNO) or s.transfer_callno
        self.endpoint.rebind_address(s, s.transfer_addr)
        self.endpoint.rebind_peer_callno(s, peer_callno)
        s.transfer_addr = None
        s.transfer_callno = 0
        # complete_transfer(): reset sequencing and timing for the new leg
        s.oseqno = s.iseqno = s.rseqno = s.aseqno = 0
        s.svoiceformat = 0
        s.voiceformat = 0
        s.svideoformat = 0
        s.videoformat = 0
        s.last = 0
        s.lastsent = 0
        s._nextpred = 0
        s._offset = None
        for p in list(s._queue):
            s._drop(p)
        s.stop_keepalives()
        self._set(call, TransferState.NONE)
        log.info("%r: transferred to %s:%s/%s", call, *s.addr, peer_callno)

    def on_txrej(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        call.session.transfer_addr = None
        call.session.transfer_callno = 0
        call.session.cancel_transfer_frames()
        self._set(call, TransferState.NONE)

    def on_txmedia(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is TransferState.READY:
            call.session.cancel_transfer_frames()
            self._set(call, TransferState.MEDIAPASS)

    def on_timeout(self, call: Call) -> None:
        """The transfer path never answered: TXREJ was sent, fall back."""
        call.session.transfer_addr = None
        call.session.transfer_callno = 0
        self._set(call, TransferState.NONE)

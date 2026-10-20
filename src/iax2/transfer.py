# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Native IAX2 transfers ("re-INVITE"-style path optimisation).

When a server bridges two IAX2 calls it may ask both endpoints to talk to
each other directly and drop out of the media path::

    server -> A: TXREQ(APPARENT_ADDR=B, CALLNO=B's callno, TRANSFERID)
    server -> B: TXREQ(APPARENT_ADDR=A, CALLNO=A's callno, TRANSFERID)
    A -> B: TXCNT(TRANSFERID)          B -> A: TXCNT(TRANSFERID)
    B -> A: TXACC                      A -> B: TXACC
    A -> server: TXREADY(CALLNO)       B -> server: TXREADY(CALLNO)
    server -> A: TXREL(CALLNO=B's)     server -> B: TXREL(CALLNO=A's)

After TXREL each endpoint sends media and signalling straight to the other
and the server forgets both calls. With a *media-only* transfer the server
sends TXMEDIA instead and stays in the signalling path. If anything fails an
endpoint sends TXREJ and the call simply continues through the server.

Both roles are implemented:

* the **endpoint** role (answering TXREQ) matches Asterisk's
  ``try_transfer()`` / ``complete_transfer()`` and is always active
  (disable with :attr:`TransferManager.enabled`);
* the **server** role (:meth:`TransferManager.start_transfer`) matches
  Asterisk's ``iax2_start_transfer()`` and TXREADY handling. Note that
  Asterisk 22 itself is built without native bridging and never starts
  transfers, but answers them.
"""

from __future__ import annotations

import enum
import logging
import secrets
from typing import TYPE_CHECKING

from .constants import IE, FrameType, IAXCommand
from .exceptions import CallError
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
    MEDIA = 8
    MEDIAPASS = 9


class TransferManager:
    """Native transfer handling for an endpoint's calls.

    Attributes:
        enabled: Accept transfer requests (Asterisk ``transfer=yes``, the
            default). When ``False`` every TXREQ is answered with TXREJ.
    """

    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self.enabled = True
        self._state: dict[int, TransferState] = {}
        self._bridge: dict[int, Call] = {}

    def state(self, call: Call) -> TransferState:
        """Current transfer state of ``call``."""
        return self._state.get(call.callno, TransferState.NONE)

    def _set(self, call: Call, st: TransferState) -> None:
        if st is TransferState.NONE:
            self._state.pop(call.callno, None)
        else:
            self._state[call.callno] = st

    # ------------------------------------------------------------ server role

    def start_transfer(self, a: Call, b: Call, media_only: bool = False) -> None:
        """Ask the endpoints of calls ``a`` and ``b`` to connect directly.

        Both calls must be up and unencrypted. Completion is asynchronous:
        when both endpoints report TXREADY they are released (or, with
        ``media_only``, told to exchange media directly) and both calls end
        locally with reason ``"transferred"`` (no HANGUP is sent).

        Raises:
            CallError: if either call cannot be transferred.
        """
        for c in (a, b):
            if not c.is_active or not c.peer_callno:
                raise CallError(f"{c!r} is not up")
            if c.session.encryptor is not None:
                raise CallError("transfers are not supported for encrypted calls")
        txid = secrets.randbelow(2**32)
        for this, other in ((a, b), (b, a)):
            ies = IEs().add(IE.APPARENT_ADDR, other.peer_addr)
            ies.add(IE.CALLNO, other.peer_callno)
            ies.add(IE.TRANSFERID, txid)
            this.session.send_command(IAXCommand.TXREQ, ies)
            self._set(this, TransferState.MBEGIN if media_only else TransferState.BEGIN)
            self._bridge[this.callno] = other

    def _on_server_txready(self, call: Call) -> None:
        st = self.state(call)
        if st is TransferState.BEGIN:
            self._set(call, TransferState.READY)
        elif st is TransferState.MBEGIN:
            self._set(call, TransferState.MREADY)
        else:
            return
        other = self._bridge.get(call.callno)
        if other is None:
            return
        if self.state(other) not in (TransferState.READY, TransferState.MREADY):
            return
        media = self.state(call) is TransferState.MREADY
        for this, peer in ((call, other), (other, call)):
            ies = IEs().add(IE.CALLNO, peer.peer_callno)
            if media:
                self._set(this, TransferState.MEDIA)
                this.session.send_command(IAXCommand.TXMEDIA, ies)
            else:
                self._set(this, TransferState.RELEASED)
                this.session.stop_keepalives()
                this.session.send_command(IAXCommand.TXREL, ies, final=True)
        if not media:
            for c in (call, other):
                self._bridge.pop(c.callno, None)
                c._end("transferred", None, by_peer=False, reason="transferred")
        log.info("native transfer of %r and %r %s", call, other,
                 "(media only)" if media else "released")

    # ---------------------------------------------------------- endpoint role

    def on_txreq(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        s = call.session
        addr = ies.get(IE.APPARENT_ADDR)
        callno = ies.get(IE.CALLNO)
        if not self.enabled or addr is None or not callno or s.encryptor is not None:
            log.info("%r: refusing transfer request", call)
            s.send_command(IAXCommand.TXREJ)
            return
        s.transfer_addr = (addr[0], addr[1])
        s.transfer_callno = callno
        self.endpoint.index_transfer(s)
        self._set(call, TransferState.BEGIN)
        txid = ies.get(IE.TRANSFERID)
        out = IEs().add(IE.TRANSFERID, txid) if txid else None
        # send_command_transfer(): seqno 0, to the transfer address, retried <= 1 s
        s.send(FrameType.IAX, IAXCommand.TXCNT, out.to_bytes() if out else b"", transfer=True,
               seqno=0)

    def on_txcnt(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is not TransferState.NONE:
            call.session.send(FrameType.IAX, IAXCommand.TXACC, b"", transfer=True, seqno=0)

    def on_txacc(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is TransferState.BEGIN:
            call.session.cancel_transfer_frames()
            call.session.send_command(IAXCommand.TXREADY, IEs().add(IE.CALLNO, call.callno))
            self._set(call, TransferState.READY)

    def on_txready(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        self._on_server_txready(call)

    def on_txrel(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        """Complete the transfer: switch the call to the new peer (``complete_transfer``)."""
        s = call.session
        peer_callno = ies.get(IE.CALLNO) or 0
        if s.transfer_addr is None or peer_callno < 1:
            log.warning("%r: invalid transfer release", call)
            return
        self.endpoint.unindex_transfer(s)
        self.endpoint.rebind_address(s, s.transfer_addr)
        self.endpoint.rebind_peer_callno(s, peer_callno)
        s.transfer_addr = None
        s.transfer_callno = 0
        s.oseqno = s.iseqno = s.rseqno = s.aseqno = 0
        s.svoiceformat = s.svideoformat = -1
        s.voiceformat = s.videoformat = 0
        s.last = s.lastsent = s._nextpred = 0
        s._offset = None
        s.pingtime = s.timing.default_retry_ms
        for p in list(s._queue):
            s._drop(p)
        s.stop_keepalives()  # Asterisk stop_stuff(): "for attended transfer to work with libiax"
        self._set(call, TransferState.NONE)
        log.info("%r: transferred to %s:%s call %s", call, s.addr[0], s.addr[1], peer_callno)

    def on_txrej(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        s = call.session
        self.endpoint.unindex_transfer(s)
        s.transfer_addr = None
        s.transfer_callno = 0
        s.cancel_transfer_frames()
        self._set(call, TransferState.NONE)
        other = self._bridge.pop(call.callno, None)
        if other is not None and self.state(other) is not TransferState.NONE:
            self._set(other, TransferState.NONE)
            self._bridge.pop(other.callno, None)
            other.session.send_command(IAXCommand.TXREJ)

    def on_txmedia(self, call: Call, frame: FullFrame, ies: IEs) -> None:
        if self.state(call) is TransferState.READY:
            call.session.cancel_transfer_frames()
            self._set(call, TransferState.MEDIAPASS)
            call.session.media_via_transfer = True

    def on_timeout(self, call: Call) -> None:
        """The transfer path never answered (TXREJ already sent): fall back."""
        self.endpoint.unindex_transfer(call.session)
        call.session.transfer_addr = None
        call.session.transfer_callno = 0
        self._set(call, TransferState.NONE)

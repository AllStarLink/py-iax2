# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Per-call-number reliable transport ("session").

Every IAX2 dialog — a call, a registration, a POKE — is identified by a
local call number and a remote call number. :class:`Session` implements
everything that is common to them, following Asterisk 22's ``chan_iax2.c``:

* outbound/inbound sequence numbers, implicit and explicit ACKs, VNAK;
* retransmission of full frames (``2 × ping time`` clamped to
  100–10 000 ms, ×10 back-off, give up after 4 transmissions);
* outgoing timestamp generation (``calc_timestamp()``): clock-based for IAX
  frames, predicted for voice, slaved to voice for other frames;
* reconstruction of mini-frame timestamps (``unwrap_timestamp()``);
* choice of full vs. mini voice/video frames;
* holding non-IAX signalling until the peer's call number is known;
* periodic PING / LAGRQ while a call is up;
* "final" frames that destroy the session once acknowledged.

A session hands frames that need application handling to its *owner*
(:class:`~iax2.call.Call`, a registration, ...) through the
:class:`SessionOwner` protocol.

This is an internal building block; applications use
:class:`~iax2.endpoint.Endpoint` and :class:`~iax2.call.Call`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .constants import IE, FrameType, IAXCommand
from .formats import format_info
from .frames import FullFrame, MiniFrame, VideoFrame
from .ies import IEs

if TYPE_CHECKING:  # pragma: no cover
    from .endpoint import Endpoint

__all__ = ["Session", "SessionOwner", "SessionStats", "Timing"]

log = logging.getLogger("iax2.session")

Addr = tuple[str, int]

#: Commands that never consume a sequence number and are never ACKed.
_ACK_LIKE = frozenset(
    {IAXCommand.ACK, IAXCommand.INVAL, IAXCommand.TXCNT, IAXCommand.TXACC, IAXCommand.VNAK}
)
#: Extra commands tolerated out of order (attended-transfer related).
_OOO_OK = _ACK_LIKE | {IAXCommand.TXREADY, IAXCommand.TXREL, IAXCommand.UNQUELCH}
#: Commands whose timestamp originated with us (don't update ``last``).
_ECHO_TS = frozenset({IAXCommand.ACK, IAXCommand.PONG, IAXCommand.LAGRP})


@dataclass
class Timing:
    """Tunable protocol timers (defaults are Asterisk's).

    Attributes:
        min_retry_ms: Lower bound of the first retransmission delay.
        max_retry_ms: Upper bound of any retransmission delay.
        default_retry_ms: Assumed round-trip time before one is measured.
        max_retries: Transmissions before a frame is given up on.
        ping_interval: Seconds between PINGs on an active session (0 = off).
        lagrq_interval: Seconds between LAGRQs on an active session (0 = off).
        max_timestamp_skew: ms of tolerated skew before voice timestamps
            re-sync to the real clock.
    """

    min_retry_ms: int = 100
    max_retry_ms: int = 10_000
    default_retry_ms: int = 1000
    max_retries: int = 4
    ping_interval: float = 21.0
    lagrq_interval: float = 10.0
    max_timestamp_skew: int = 160


class SessionOwner(Protocol):
    """Interface a session uses to talk to the object that owns it."""

    def on_iax(self, session: Session, frame: FullFrame, ies: IEs) -> None:
        """Handle an IAX command not handled by the session itself."""

    def on_media(self, session: Session, frame_type: int, subclass: int, payload: bytes,
                 timestamp: int, full: bool, mark: bool) -> None:
        """Handle a non-IAX frame (voice, video, control, DTMF, text, ...)."""

    def on_session_error(self, session: Session, reason: str) -> None:
        """The peer stopped acknowledging frames; the session is being torn down."""

    def on_session_destroyed(self, session: Session) -> None:
        """The session has been destroyed and its call number released."""


@dataclass
class _Pending:
    frame: FullFrame
    final: bool
    transfer: bool
    retries: int = 0
    retry_ms: int = 0
    handle: asyncio.TimerHandle | None = None
    addr: Addr | None = None


@dataclass
class _Held:
    frame_type: int
    subclass: int
    payload: bytes
    final: bool


@dataclass
class SessionStats:
    """Counters and measurements for a session.

    Attributes:
        frames_sent: Full + mini frames transmitted (excluding retransmits).
        frames_received: Frames accepted from the peer.
        retransmits: Full frames retransmitted.
        rtt_ms: Last measured round-trip time (PING/PONG), or ``None``.
        lag_ms: Last measured LAGRQ/LAGRP lag, or ``None``.
        remote_rr: The peer's last receiver report (RR_* IEs from PONG).
    """

    frames_sent: int = 0
    frames_received: int = 0
    retransmits: int = 0
    rtt_ms: int | None = None
    lag_ms: int | None = None
    remote_rr: dict[str, int] = field(default_factory=dict)
    voice_received: int = 0
    out_of_order: int = 0
    jitter_ms: float = 0.0
    _last_transit: float | None = None
    _highest_voice_ts: int = -1

    def note_voice(self, ts: int, arrival_s: float) -> None:
        """Update receive statistics for a voice frame (RFC 3550 jitter)."""
        self.voice_received += 1
        transit = arrival_s * 1000 - ts
        if self._last_transit is not None:
            self.jitter_ms += (abs(transit - self._last_transit) - self.jitter_ms) / 16
        self._last_transit = transit
        if ts < self._highest_voice_ts:
            self.out_of_order += 1
        else:
            self._highest_voice_ts = ts


class Session:
    """Reliable delivery state for one local call number.

    Args:
        endpoint: The owning endpoint (provides sending, clock and loop).
        callno: Our call number.
        addr: Peer address ``(host, port)``.
        owner: Object receiving frames (see :class:`SessionOwner`).
        peer_callno: Peer's call number if already known (incoming dialogs).
        timing: Timer configuration.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        callno: int,
        addr: Addr,
        owner: SessionOwner | None = None,
        *,
        peer_callno: int = 0,
        timing: Timing | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.callno = callno
        self.addr = addr
        self.owner = owner
        self.peer_callno = peer_callno
        self.timing = timing or Timing()
        self.stats = SessionStats()

        # sequence numbers (all mod 256)
        self.oseqno = 0  # next outbound seqno
        self.iseqno = 0  # next inbound seqno expected
        self.rseqno = 0  # peer has acknowledged everything below this
        self.aseqno = 0  # last iseqno we told the peer about

        # timestamps
        self._offset: float | None = None  # monotonic time of ts 0
        self.lastsent = 0
        self._nextpred = 0
        self._notsilenttx = False
        self.last = 0  # highest timestamp received from peer
        self.lastvsent = 0
        self.pingtime = self.timing.default_retry_ms

        # media formats
        self.svoiceformat = 0  # last voice format sent in a full frame
        self.svideoformat = 0
        self.voiceformat = 0  # current inbound voice format
        self.videoformat = 0

        self.started = False  # call is up: media may flow (IAX_STATE_STARTED)
        self.trunk = False  # send voice via the endpoint's trunk for this peer

        self._queue: list[_Pending] = []
        self._held: list[_Held] | None = []  # None once signalling released
        self._timers: list[asyncio.TimerHandle] = []
        self._ping_handle: asyncio.TimerHandle | None = None
        self._lag_handle: asyncio.TimerHandle | None = None
        self._lagrq_sent: int | None = None

        # call-token resend support: the IEs of the last dialog-starting frame
        self._calltoken_frame: FullFrame | None = None

        # transfer state (used by Call)
        self.transfer_addr: Addr | None = None
        self.transfer_callno = 0
        self.media_via_transfer = False  # media-only transfer (TXMEDIA) in effect

        self.destroyed = False
        self.error: str | None = None
        # AES encryption state (iax2.crypto.CallCrypto), if negotiated
        self.crypto: Any = None

    # ------------------------------------------------------------------ util

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.endpoint.loop

    def _now(self) -> float:
        return self.endpoint.clock()

    def __repr__(self) -> str:
        return f"<Session {self.callno}/{self.peer_callno} {self.addr[0]}:{self.addr[1]}>"

    # ------------------------------------------------------------- timestamps

    def _ensure_offset(self) -> None:
        if self._offset is None:
            now = self._now()
            # Round down to 20 ms "for nice looking traces", like Asterisk.
            self._offset = now - (now % 0.020)

    def _ms_since_offset(self) -> int:
        self._ensure_offset()
        assert self._offset is not None
        return max(0, int((self._now() - self._offset) * 1000))

    def current_timestamp(self) -> int:
        """Milliseconds since this session's timestamp origin (no side effects)."""
        return self._ms_since_offset()

    def calc_timestamp(self, frame_type: int, *, ts: int = 0, samples: int = 0,
                       rate: int = 8000) -> int:
        """Compute the timestamp for an outgoing frame (Asterisk ``calc_timestamp``).

        Args:
            frame_type: Type of frame being sent.
            ts: Explicit timestamp to use (e.g. echoed PONG); 0 = compute.
            samples: For voice: number of samples in the frame.
            rate: For voice: sample rate in Hz.
        """
        voice = frame_type == FrameType.VOICE
        genuine = frame_type == FrameType.IAX
        if frame_type == FrameType.CNG:
            self._notsilenttx = False
        self._ensure_offset()
        if ts:
            return ts
        ms = self._ms_since_offset()
        skew = self.timing.max_timestamp_skew
        if voice:
            rate_ms = max(1, rate // 1000)
            adjust = ms - self._nextpred
            if self._notsilenttx and abs(adjust) <= skew:
                # nudge the clock origin 10% toward the predicted stream
                assert self._offset is not None
                self._offset += adjust / 10000.0
                if not self._nextpred:
                    self._nextpred = ms
                    if self._nextpred <= self.lastsent:
                        self._nextpred = self.lastsent + 3
                ms = self._nextpred
            else:
                frame_ms = samples // rate_ms if samples >= rate_ms else 0
                if frame_ms:
                    diff = ms % frame_ms
                    if diff:
                        ms += frame_ms - diff
                self._nextpred = ms
                self._notsilenttx = True
        elif frame_type == FrameType.VIDEO:
            ms = max(ms, self.lastsent)
        else:
            adjust = ms - self.lastsent
            if genuine:
                if ms <= self.lastsent:
                    ms = self.lastsent + 3
            elif abs(adjust) <= skew:
                ms = self.lastsent + 3
        self.lastsent = ms
        if voice:
            self._nextpred += samples // max(1, rate // 1000)
        return ms

    def unwrap_timestamp(self, ts: int, video: bool = False) -> int:
        """Reconstruct a full timestamp from a mini/video frame's low bits.

        Combines the low 16 (15 for video) bits with the upper bits of the last
        full timestamp, correcting for wrap-around like Asterisk's
        ``unwrap_timestamp()``.
        """
        shift = 15 if video else 16
        lower = (1 << shift) - 1
        upper = ~lower & 0xFFFFFFFF
        last_upper = self.last & upper
        full = last_upper | (ts & lower)
        x = full - self.last
        threshold = 25000 if video else 50000
        if x < -threshold:
            full = ((last_upper + (1 << shift)) & 0xFFFFFFFF) | (ts & lower)
        elif x > threshold:
            full = ((last_upper - (1 << shift)) & 0xFFFFFFFF) | (ts & lower)
        return full

    # ------------------------------------------------------------------ sending

    def _retry_ms(self) -> int:
        t = self.pingtime * 2
        return max(self.timing.min_retry_ms, min(self.timing.max_retry_ms, t))

    def send_command(self, command: int, ies: IEs | None = None, **kw: object) -> FullFrame | None:
        """Send an IAX command reliably. See :meth:`send` for keyword arguments."""
        return self.send(FrameType.IAX, int(command), ies.to_bytes() if ies else b"", **kw)  # type: ignore[arg-type]

    def send(
        self,
        frame_type: int,
        subclass: int,
        payload: bytes = b"",
        *,
        ts: int = 0,
        seqno: int | None = None,
        now: bool = False,
        final: bool = False,
        transfer: bool = False,
        samples: int = 0,
        mark: bool = False,
    ) -> FullFrame | None:
        """Send a frame on this session (Asterisk ``iax2_send``).

        Voice and video frames are sent as mini frames when possible.

        Args:
            frame_type: Frame type.
            subclass: Subclass (command, control, digit or format bit).
            payload: Frame body.
            ts: Explicit timestamp (0 = compute).
            seqno: Explicit outbound sequence number (ACK/VNAK use the
                peer's iseqno); ``None`` = allocate the next one.
            now: Send immediately without retransmission.
            final: Destroy the session once this frame is acknowledged.
            transfer: Send to the transfer address (attended transfer).
            samples: Voice: number of samples (for timestamp prediction).
            mark: Video: end-of-frame marker.

        Returns:
            The full frame sent, or ``None`` if a mini frame was sent, the
            frame was held, or the session is dead.
        """
        if self.destroyed:
            return None
        # Hold non-IAX signalling until we know the peer's call number.
        if self._held is not None and frame_type != FrameType.IAX:
            self._held.append(_Held(frame_type, subclass, payload, final))
            return None
        return self._send(frame_type, subclass, payload, ts=ts, seqno=seqno, now=now,
                          final=final, transfer=transfer, samples=samples, mark=mark)

    def _send(self, frame_type: int, subclass: int, payload: bytes, *, ts: int, seqno: int | None,
              now: bool, final: bool, transfer: bool, samples: int, mark: bool) -> FullFrame | None:
        lastsent = self.lastsent
        rate = 8000
        if frame_type == FrameType.VOICE:
            try:
                rate = format_info(subclass).sample_rate or 8000
            except (KeyError, ValueError):
                rate = 8000
        fts = self.calc_timestamp(frame_type, ts=ts, samples=samples, rate=rate)
        if frame_type == FrameType.VOICE and not payload:
            return None  # interpolation placeholder: only advances prediction

        sendmini = False
        if frame_type == FrameType.VOICE and subclass == self.svoiceformat:
            hi = fts & 0xFFFF0000
            if self.trunk or hi == (lastsent & 0xFFFF0000) or hi == ((lastsent + 0x10000) & 0xFFFF0000):
                sendmini = True
                now = True
        if frame_type == FrameType.VIDEO:
            if (fts & 0xFFFF8000) == (self.lastvsent & 0xFFFF8000) and subclass == self.svideoformat:
                sendmini = True
                now = True
            else:
                sendmini = False
                now = False
            self.lastvsent = fts

        if sendmini:
            if frame_type == FrameType.VIDEO:
                data = VideoFrame(self.callno, fts, mark, payload).to_bytes()
            elif self.trunk:
                self.endpoint.trunk_queue(self, fts, payload)
                self.stats.frames_sent += 1
                return None
            else:
                data = MiniFrame(self.callno, fts, payload).to_bytes()
            maddr = self.transfer_addr if self.media_via_transfer and self.transfer_addr else self.addr
            self._transmit_raw(data, maddr)
            self.stats.frames_sent += 1
            return None

        if self.media_via_transfer and frame_type in (FrameType.VOICE, FrameType.VIDEO):
            transfer, now, seqno = True, True, 0
        if seqno is None:
            seqno = self.oseqno
            self.oseqno = (self.oseqno + 1) & 0xFF
        frame = FullFrame(
            frame_type,
            subclass,
            source_call=self.callno,
            dest_call=self.transfer_callno if transfer else self.peer_callno,
            timestamp=fts,
            oseqno=seqno,
            iseqno=0 if transfer else self.iseqno,
            payload=payload,
            mark=mark,
        )
        if not transfer:
            self.aseqno = self.iseqno
        if frame_type == FrameType.VOICE:
            self.svoiceformat = subclass
        elif frame_type == FrameType.VIDEO:
            self.svideoformat = subclass
        is_ack = frame_type == FrameType.IAX and subclass == IAXCommand.ACK
        addr = self.transfer_addr if transfer and self.transfer_addr else self.addr
        self.stats.frames_sent += 1
        if now or is_ack:
            self._transmit_full(frame, addr)
            return frame
        pending = _Pending(frame, final, transfer, retry_ms=self._retry_ms(), addr=addr)
        self._transmit_full(frame, addr)
        self._queue.append(pending)
        pending.retries = 1
        pending.handle = self.loop.call_later(pending.retry_ms / 1000, self._attempt, pending)
        return frame

    def _transmit_full(self, frame: FullFrame, addr: Addr) -> None:
        self._transmit_raw(frame.to_bytes(), addr)

    def _transmit_raw(self, data: bytes, addr: Addr) -> None:
        if self.crypto is not None:
            data = self.crypto.encrypt(data)
        self.endpoint.sendto(data, addr)

    def _attempt(self, p: _Pending) -> None:
        """Retransmission timer (Asterisk ``__attempt_transmit``)."""
        p.handle = None
        if self.destroyed or p.retries < 0:
            self._drop(p)
            return
        if p.retries >= self.timing.max_retries:
            self._drop(p)
            if p.transfer:
                self.send_command(IAXCommand.TXREJ)
                if self.owner is not None and hasattr(self.owner, "on_transfer_timeout"):
                    self.owner.on_transfer_timeout(self)
            elif p.final:
                self.destroy()
            else:
                desc = p.frame.describe()
                log.warning("%r: max retries exceeded (%s)", self, desc)
                self.error = "timeout"
                if self.owner is not None:
                    self.owner.on_session_error(self, f"no response from peer ({desc})")
                self.destroy()
            return
        p.frame.retransmission = True
        p.frame.iseqno = 0 if p.transfer else self.iseqno
        self._transmit_full(p.frame, p.addr or self.addr)
        self.stats.retransmits += 1
        p.retries += 1
        p.retry_ms = min(p.retry_ms * 10, self.timing.max_retry_ms)
        if p.transfer:
            p.retry_ms = min(p.retry_ms, 1000)
        p.handle = self.loop.call_later(p.retry_ms / 1000, self._attempt, p)

    def _drop(self, p: _Pending) -> None:
        if p.handle is not None:
            p.handle.cancel()
            p.handle = None
        p.retries = -1
        try:
            self._queue.remove(p)
        except ValueError:
            pass

    def cancel_transfer_frames(self) -> None:
        """Stop retransmitting any outstanding transfer-path frames."""
        for p in list(self._queue):
            if p.transfer:
                self._drop(p)

    def release_signaling(self) -> None:
        """Send any held non-IAX frames (peer call number is now known)."""
        held, self._held = self._held, None
        for h in held or ():
            self._send(h.frame_type, h.subclass, h.payload, ts=0, seqno=None, now=False,
                       final=h.final, transfer=False, samples=0, mark=False)

    def send_ack(self, frame: FullFrame) -> None:
        """Send an explicit ACK for ``frame`` (echoing its timestamp)."""
        self._send(FrameType.IAX, IAXCommand.ACK, b"", ts=frame.timestamp, seqno=frame.iseqno,
                   now=True, final=False, transfer=False, samples=0, mark=False)

    def send_vnak(self) -> None:
        """Ask the peer to retransmit everything from our iseqno."""
        self._send(FrameType.IAX, IAXCommand.VNAK, b"", ts=0, seqno=self.iseqno, now=True,
                   final=False, transfer=False, samples=0, mark=False)

    def send_raw_full(self, frame: FullFrame, addr: Addr | None = None) -> None:
        """Send a pre-built full frame unreliably (no sequence bookkeeping)."""
        self._transmit_full(frame, addr or self.addr)

    def remember_for_calltoken(self, frame: FullFrame | None) -> None:
        """Record the frame to resend if the peer answers with CALLTOKEN."""
        self._calltoken_frame = frame

    # --------------------------------------------------------------- receiving

    def handle_full(self, frame: FullFrame, addr: Addr) -> None:
        """Process an incoming full frame for this session."""
        if self.destroyed:
            return
        cmd = frame.subclass if frame.frame_type == FrameType.IAX else None
        is_iax = frame.frame_type == FrameType.IAX
        self.stats.frames_received += 1
        from_peer = addr == self.addr

        if from_peer and cmd not in (IAXCommand.TXCNT, IAXCommand.TXACC):
            if frame.source_call and frame.source_call != self.peer_callno:
                self.endpoint.rebind_peer_callno(self, frame.source_call)

        # -- sequence number check
        if self.iseqno != frame.oseqno and (
            self.iseqno or not is_iax or cmd not in (_OOO_OK - {IAXCommand.ACK, IAXCommand.INVAL, IAXCommand.VNAK})
        ):
            if not is_iax or cmd not in _OOO_OK:
                if ((self.iseqno - frame.oseqno) & 0xFF) < 128:
                    # already seen: re-ACK it
                    if not is_iax or cmd not in (IAXCommand.ACK, IAXCommand.INVAL):
                        self.send_ack(frame)
                else:
                    self.send_vnak()
                return
        elif not is_iax or cmd not in _ACK_LIKE:
            self.iseqno = (self.iseqno + 1) & 0xFF

        # -- implicit acknowledgement of our frames
        if from_peer and not (is_iax and cmd == IAXCommand.INVAL):
            self._process_ack(frame.iseqno)
            if self.destroyed:
                return

        # Only TXACC/TXCNT are accepted from anywhere but the peer's address.
        if not from_peer and not (is_iax and cmd in (IAXCommand.TXACC, IAXCommand.TXCNT)):
            return

        if is_iax and cmd != IAXCommand.CALLTOKEN and self._held is not None:
            self.release_signaling()

        if frame.frame_type == FrameType.VOICE and frame.subclass != self.voiceformat:
            self.voiceformat = int(frame.subclass)
        if frame.frame_type == FrameType.VIDEO and frame.subclass != self.videoformat:
            self.videoformat = int(frame.subclass)

        if is_iax:
            if self.last < frame.timestamp and cmd not in _ECHO_TS:
                self.last = frame.timestamp
            try:
                ies = frame.ies
            except Exception:  # noqa: BLE001 - undecodable: Asterisk drops it
                log.warning("%r: undecodable IEs in %s", self, frame.describe())
                return
            self._handle_iax(frame, ies)
            if not self.destroyed and cmd not in _ACK_LIKE and self.aseqno != self.iseqno:
                self.send_ack(frame)
            return

        if self.aseqno != self.iseqno:
            self.send_ack(frame)
        if not self.started:
            return
        if frame.frame_type == FrameType.VOICE:
            self.stats.note_voice(frame.timestamp, self._now())
        if self.owner is not None:
            self.owner.on_media(self, int(frame.frame_type), int(frame.subclass), frame.payload,
                                frame.timestamp, True, frame.mark)
        if self.last < frame.timestamp:
            self.last = frame.timestamp

    def _process_ack(self, their_iseqno: int) -> None:
        r, o = self.rseqno, self.oseqno
        in_window = (r >= o) or (r <= their_iseqno < o)
        x = their_iseqno if in_window else o
        if x != o or o == their_iseqno:
            destroy = False
            seq = r
            while seq != their_iseqno:
                for p in list(self._queue):
                    if p.frame.oseqno == seq and not p.transfer:
                        self._drop(p)
                        if p.final:
                            destroy = True
                seq = (seq + 1) & 0xFF
            self.rseqno = their_iseqno
            if destroy:
                self.destroy()

    def handle_mini(self, frame: MiniFrame) -> None:
        """Process an incoming mini (voice) frame."""
        if self.destroyed:
            return
        self.stats.frames_received += 1
        if not self.voiceformat:
            self.send_vnak()
            return
        ts = self.unwrap_timestamp(frame.timestamp)
        self._deliver_mini(FrameType.VOICE, self.voiceformat, frame.payload, ts, False)

    def handle_trunk_entry(self, payload: bytes, ts: int) -> None:
        """Process voice received inside a trunk frame (``ts`` already resolved)."""
        if self.destroyed:
            return
        self.stats.frames_received += 1
        if not self.voiceformat:
            self.send_vnak()
            return
        self._deliver_mini(FrameType.VOICE, self.voiceformat, payload, ts, False)

    def handle_video(self, frame: VideoFrame) -> None:
        """Process an incoming meta video frame."""
        if self.destroyed:
            return
        self.stats.frames_received += 1
        if not self.videoformat:
            self.send_vnak()
            return
        ts = (self.last & 0xFFFF8000) | (frame.timestamp & 0x7FFF)
        ts = self.unwrap_timestamp(ts, video=True)
        self._deliver_mini(FrameType.VIDEO, self.videoformat, frame.payload, ts, frame.mark)

    def _deliver_mini(self, ftype: int, fmt: int, payload: bytes, ts: int, mark: bool) -> None:
        if not self.started:
            return
        if ftype == FrameType.VOICE:
            self.stats.note_voice(ts, self._now())
        if self.owner is not None:
            self.owner.on_media(self, ftype, fmt, payload, ts, False, mark)
        if self.last < ts:
            self.last = ts

    # ------------------------------------------------------- IAX commands

    def _handle_iax(self, frame: FullFrame, ies: IEs) -> None:
        cmd = frame.subclass
        if cmd == IAXCommand.ACK:
            return
        if cmd == IAXCommand.PING:
            self.send_command(IAXCommand.PONG, self.endpoint.build_rr(self), ts=frame.timestamp)
            return
        if cmd == IAXCommand.POKE:
            self.send_command(IAXCommand.PONG, ts=frame.timestamp, final=True)
            return
        if cmd == IAXCommand.PONG:
            rtt = self.calc_timestamp(FrameType.IAX) - frame.timestamp
            self.pingtime = max(1, rtt)
            self.stats.rtt_ms = rtt
            rr = {}
            for name in ("RR_JITTER", "RR_LOSS", "RR_PKTS", "RR_DELAY", "RR_DROPPED", "RR_OOO"):
                v = ies.get(IE[name])
                if v is not None:
                    rr[name[3:].lower()] = v
            if rr:
                self.stats.remote_rr = rr
            if self.owner is not None:
                self.owner.on_iax(self, frame, ies)
            return
        if cmd == IAXCommand.LAGRQ:
            self.send(FrameType.IAX, IAXCommand.LAGRP, frame.payload, ts=frame.timestamp)
            return
        if cmd == IAXCommand.LAGRP:
            self.stats.lag_ms = self.calc_timestamp(FrameType.IAX) - frame.timestamp
            return
        if cmd == IAXCommand.VNAK:
            for p in list(self._queue):
                if ((p.frame.oseqno - frame.iseqno) & 0xFF) < 128 and p.retries >= 0:
                    self._transmit_full(p.frame, p.addr or self.addr)
            return
        if cmd == IAXCommand.INVAL:
            self.error = "invalid"
            if self.owner is not None:
                self.owner.on_iax(self, frame, ies)
            self.destroy()
            return
        if cmd == IAXCommand.CALLTOKEN:
            token = ies.calltoken
            if token and self._calltoken_frame is not None:
                self._resend_with_token(token)
            return
        if self.owner is not None:
            self.owner.on_iax(self, frame, ies)
        else:
            # Nobody to handle it: tell the peer, like Asterisk does.
            self.send_command(IAXCommand.UNSUPPORT, IEs().add(IE.IAX_UNKNOWN, int(cmd) & 0xFF))

    def _resend_with_token(self, token: bytes) -> None:
        """Resend the dialog-starting frame with the server's call token."""
        old = self._calltoken_frame
        assert old is not None
        if old.dest_call:
            return
        ies = old.ies
        ies.set(IE.CALLTOKEN, token)
        for p in list(self._queue):
            self._drop(p)
        # Asterisk clears sequence numbers and the (bogus) peer call number
        # learned from the token reply, then starts the dialog anew.
        self.oseqno = 0
        self.iseqno = 0
        self.rseqno = 0
        self.aseqno = 0
        if self.peer_callno:
            self.endpoint.rebind_peer_callno(self, 0)
        frame = self.send(FrameType.IAX, int(old.subclass), ies.to_bytes())
        self._calltoken_frame = frame

    # ----------------------------------------------------------- keepalives

    def start_keepalives(self) -> None:
        """Begin periodic PING and LAGRQ (Asterisk does this for every call)."""
        t = self.timing
        if t.ping_interval and self._ping_handle is None:
            self._ping_handle = self.loop.call_later(t.ping_interval, self._send_ping)
        if t.lagrq_interval and self._lag_handle is None:
            self._lag_handle = self.loop.call_later(t.lagrq_interval, self._send_lagrq)

    def stop_keepalives(self) -> None:
        """Stop periodic PING and LAGRQ."""
        for h in (self._ping_handle, self._lag_handle):
            if h is not None:
                h.cancel()
        self._ping_handle = self._lag_handle = None

    def _send_ping(self) -> None:
        self._ping_handle = None
        if self.destroyed:
            return
        if self.peer_callno:
            self.send_command(IAXCommand.PING)
            self._ping_handle = self.loop.call_later(self.timing.ping_interval, self._send_ping)

    def _send_lagrq(self) -> None:
        self._lag_handle = None
        if self.destroyed:
            return
        if self.peer_callno:
            self.send_command(IAXCommand.LAGRQ)
            self._lag_handle = self.loop.call_later(self.timing.lagrq_interval, self._send_lagrq)

    # ------------------------------------------------------------- lifecycle

    def call_later(self, delay: float, callback: Callable[..., object], *args: object) -> asyncio.TimerHandle:
        """Schedule a callback that is cancelled automatically on destroy."""
        h = self.loop.call_later(delay, callback, *args)
        self._timers.append(h)
        return h

    @property
    def outstanding(self) -> int:
        """Number of full frames awaiting acknowledgement."""
        return len(self._queue)

    def destroy(self) -> None:
        """Tear down the session: cancel timers and release the call number."""
        if self.destroyed:
            return
        self.destroyed = True
        self.stop_keepalives()
        for p in list(self._queue):
            self._drop(p)
        for h in self._timers:
            h.cancel()
        self._timers.clear()
        self.endpoint.release_session(self)
        if self.owner is not None:
            self.owner.on_session_destroyed(self)

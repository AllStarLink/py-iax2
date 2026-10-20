# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""IAX2 calls.

A :class:`Call` represents one voice (and optionally video) call, incoming
or outgoing. It is created by :class:`~iax2.endpoint.Endpoint` —
:meth:`Endpoint.dial() <iax2.endpoint.Endpoint.dial>` for outgoing calls,
:meth:`Endpoint.incoming() <iax2.endpoint.Endpoint.incoming>` for incoming
ones — never directly.

Call setup follows Asterisk 22::

    caller                           callee
      NEW (VERSION, CALLED_NUMBER, FORMAT, CAPABILITY, ..., CALLTOKEN) ->
                    <- CALLTOKEN (if the callee requires tokens; NEW is resent)
                    <- AUTHREQ (AUTHMETHODS, CHALLENGE, USERNAME)  [optional]
      AUTHREP (MD5_RESULT | RSA_RESULT | PASSWORD) ->              [optional]
                    <- ACCEPT (FORMAT)
                    <- CONTROL RINGING                             [optional]
                    <- CONTROL ANSWER
      ... media ...
      HANGUP (CAUSECODE) ->

Example (outgoing)::

    call = await endpoint.dial("node.example.org", "1000", username="me", secret="pw")
    await call.play_pcm(pcm_bytes)
    await call.hangup()

Example (incoming)::

    async for call in endpoint.incoming():
        await call.answer()
        async for frame in call.audio():
            call.send_audio(frame.payload)      # echo
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .auth import Credentials, build_auth_reply, new_challenge, verify_auth_reply
from .codecs import Codec, get_codec
from .constants import (
    IAX_ALLOWED_CONTROLS,
    IAX_PROTO_VERSION,
    IE,
    AuthMethod,
    Cause,
    ControlType,
    FrameType,
    HTMLCommand,
    IAXCommand,
)
from .events import (
    Accepted,
    Answered,
    AudioFrame,
    CallEvent,
    ControlEvent,
    DTMFEvent,
    FormatChanged,
    HangupEvent,
    HTMLEvent,
    ImageEvent,
    Progress,
    QuelchEvent,
    Ringing,
    StateChanged,
    TextEvent,
    TransferRequest,
    UnknownFrame,
    VideoFrameData,
)
from .exceptions import AuthenticationError, CallError, CallRejected, CallTimeout
from .formats import AUDIO_MASK, choose_format, decode_codec_prefs, encode_codec_prefs, format_info
from .frames import FullFrame
from .ies import IEs
from .session import Session

if TYPE_CHECKING:  # pragma: no cover
    from .endpoint import Endpoint

__all__ = ["Call", "CallInfo", "CallState", "Direction"]

log = logging.getLogger("iax2.call")


class Direction(enum.Enum):
    """Which side started the call."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"


class CallState(enum.Enum):
    """Call lifecycle states."""

    DIALING = "dialing"
    """An outgoing call whose NEW has been sent; waiting for the callee."""
    AUTHENTICATING = "authenticating"
    """Authentication exchange in progress."""
    INCOMING = "incoming"
    """An incoming call that is authorised but not yet accepted."""
    ACCEPTED = "accepted"
    """ACCEPT has been exchanged; the codec is agreed and media may flow."""
    RINGING = "ringing"
    """The callee indicated ringing."""
    ANSWERED = "answered"
    """The call is answered (up)."""
    HUNGUP = "hungup"
    """The call is over."""


_ACTIVE = (CallState.ACCEPTED, CallState.RINGING, CallState.ANSWERED)


@dataclass
class CallInfo:
    """Parameters of a call as carried in the NEW message.

    For outgoing calls this is what we sent; for incoming calls it is what
    the caller sent. All fields are optional.

    Attributes:
        called_number: Number/extension being called.
        called_context: Context for the number.
        calling_number: Caller ID number.
        calling_name: Caller ID name.
        calling_ani: ANI for billing.
        calling_pres: Caller ID presentation (Asterisk ``AST_PRES_*``).
        calling_ton: Type of number.
        calling_tns: Transit network select.
        calling_ani2: ANI II digits.
        dnid: Originally dialled number.
        rdnis: Redirecting number.
        language: Requested language.
        username: Username the caller identifies as.
        version: Protocol version (2).
        capability: Caller's capability mask.
        format: Caller's requested format.
        codec_prefs: Caller's ordered codec preferences.
        adsicpe: ADSI CPE capability.
        encryption: Requested encryption methods.
        autoanswer: Caller requested auto-answer.
        variables: Channel variables (VARIABLE IEs).
        ies: The raw IEs of the NEW frame.
    """

    called_number: str | None = None
    called_context: str | None = None
    calling_number: str | None = None
    calling_name: str | None = None
    calling_ani: str | None = None
    calling_pres: int | None = None
    calling_ton: int | None = None
    calling_tns: int | None = None
    calling_ani2: int | None = None
    dnid: str | None = None
    rdnis: str | None = None
    language: str | None = None
    username: str | None = None
    version: int | None = None
    capability: int = 0
    format: int = 0
    codec_prefs: list[int] = field(default_factory=list)
    adsicpe: int | None = None
    encryption: int = 0
    autoanswer: bool = False
    variables: dict[str, str] = field(default_factory=dict)
    ies: IEs = field(default_factory=IEs, repr=False)

    @classmethod
    def from_ies(cls, ies: IEs) -> CallInfo:
        """Build from the IEs of a NEW frame."""
        prefs = ies.get(IE.CODEC_PREFS)
        return cls(
            called_number=ies.get(IE.CALLED_NUMBER),
            called_context=ies.get(IE.CALLED_CONTEXT),
            calling_number=ies.get(IE.CALLING_NUMBER),
            calling_name=ies.get(IE.CALLING_NAME),
            calling_ani=ies.get(IE.CALLING_ANI),
            calling_pres=ies.get(IE.CALLINGPRES),
            calling_ton=ies.get(IE.CALLINGTON),
            calling_tns=ies.get(IE.CALLINGTNS),
            calling_ani2=ies.get(IE.CALLINGANI2),
            dnid=ies.get(IE.DNID),
            rdnis=ies.get(IE.RDNIS),
            language=ies.get(IE.LANGUAGE),
            username=ies.get(IE.USERNAME),
            version=ies.get(IE.VERSION),
            capability=ies.capability,
            format=ies.format,
            codec_prefs=decode_codec_prefs(prefs) if prefs else [],
            adsicpe=ies.get(IE.ADSICPE),
            encryption=ies.get(IE.ENCRYPTION, 0),
            autoanswer=IE.AUTOANSWER in ies,
            variables=ies.variables,
            ies=ies,
        )

    def to_ies(self, capability: int, fmt: int, prefs: Iterable[int]) -> IEs:
        """Build NEW IEs in the order Asterisk's ``iax2_call()`` uses."""
        ies = IEs()
        ies.add(IE.VERSION, self.version or IAX_PROTO_VERSION)  # must be first
        ies.add(IE.CALLED_NUMBER, self.called_number or "")
        if self.autoanswer:
            ies.add(IE.AUTOANSWER)
        ies.add(IE.CODEC_PREFS, encode_codec_prefs(prefs))
        if self.calling_number:
            ies.add(IE.CALLING_NUMBER, self.calling_number)
        ies.add(IE.CALLINGPRES, self.calling_pres if self.calling_pres is not None
                else (0 if self.calling_number or self.calling_name else 0x43))
        ies.add(IE.CALLINGTON, self.calling_ton or 0)
        ies.add(IE.CALLINGTNS, self.calling_tns or 0)
        ies.add(IE.CALLINGANI2, self.calling_ani2 or 0)
        if self.calling_name:
            ies.add(IE.CALLING_NAME, self.calling_name)
        if self.calling_ani:
            ies.add(IE.CALLING_ANI, self.calling_ani)
        if self.language:
            ies.add(IE.LANGUAGE, self.language)
        if self.dnid:
            ies.add(IE.DNID, self.dnid)
        if self.rdnis:
            ies.add(IE.RDNIS, self.rdnis)
        if self.called_context:
            ies.add(IE.CALLED_CONTEXT, self.called_context)
        if self.username:
            ies.add(IE.USERNAME, self.username)
        if self.encryption:
            ies.add(IE.ENCRYPTION, self.encryption)
        ies.add(IE.FORMAT, fmt & 0xFFFFFFFF)
        ies.add(IE.FORMAT2, fmt)
        ies.add(IE.CAPABILITY, capability & 0xFFFFFFFF)
        ies.add(IE.CAPABILITY2, capability)
        ies.add(IE.ADSICPE, self.adsicpe or 0)
        ies.add(IE.DATETIME, None)
        for name, value in self.variables.items():
            # Split long values into chunks the way Asterisk does.
            chunk = max(1, 255 - (len(name.encode()) + 1))
            data = value.encode()
            for i in range(0, max(1, len(data)), chunk):
                ies.add(IE.VARIABLE, f"{name}={data[i:i + chunk].decode(errors='ignore')}")
        ies.add(IE.CALLTOKEN, b"")  # must be last (see Session._resend_with_token)
        return ies


#: Signature of a callback receiving call events.
EventCallback = Callable[[CallEvent], Any]
#: Signature of a callback receiving audio frames.
AudioCallback = Callable[[AudioFrame], Any]


class Call:
    """One IAX2 call.

    Attributes:
        endpoint: The endpoint that owns the call.
        direction: :class:`Direction` of the call.
        info: :class:`CallInfo` from the NEW message.
        state: Current :class:`CallState`.
        format: Negotiated audio format bit (0 until accepted).
        auth_method: Authentication method used, if any.
        cause: Hangup cause text, once ended.
        cause_code: Hangup cause code, once ended.
        on_event: Optional callback invoked with every :class:`CallEvent`.
        on_audio: Optional callback invoked with every :class:`AudioFrame`
            (instead of queuing it for :meth:`recv_audio`).
        user_data: Free slot for application data.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        session: Session,
        direction: Direction,
        info: CallInfo,
        *,
        capability: int,
        prefs: list[int],
        credentials: Credentials | None = None,
        caller_first: bool = False,
        audio_queue_size: int = 500,
    ) -> None:
        self.endpoint = endpoint
        self.session = session
        session.owner = self
        self.direction = direction
        self.info = info
        self.capability = capability
        self.prefs = list(prefs)
        self.credentials = credentials
        self.caller_first = caller_first
        self.state = CallState.DIALING if direction is Direction.OUTGOING else CallState.INCOMING
        self.format = 0
        self.auth_method: AuthMethod | None = None
        self.cause: str | None = None
        self.cause_code: int | None = None
        self.on_event: EventCallback | None = None
        self.on_audio: AudioCallback | None = None
        self.on_video: Callable[[VideoFrameData], Any] | None = None
        self.user_data: Any = None
        self.quelched = False  # peer asked us to stop sending media
        self._challenge: str | None = None
        self._events: asyncio.Queue[CallEvent | None] = asyncio.Queue()
        self._audio: asyncio.Queue[AudioFrame | None] = asyncio.Queue(audio_queue_size)
        self._accepted = asyncio.Event()
        self._answered = asyncio.Event()
        self._ended = asyncio.Event()
        self._tx_codec: Codec | None = None
        self._rx_codec: Codec | None = None
        self._rx_format = 0
        self._dp_waiters: dict[str, asyncio.Future[int]] = {}
        self._jitterbuffer: Any = None

    # ------------------------------------------------------------ properties

    @property
    def callno(self) -> int:
        """Our call number."""
        return self.session.callno

    @property
    def peer_callno(self) -> int:
        """The remote call number (0 until known)."""
        return self.session.peer_callno

    @property
    def peer_addr(self) -> tuple[str, int]:
        """The remote address ``(host, port)``."""
        return self.session.addr

    @property
    def stats(self) -> Any:
        """:class:`~iax2.session.SessionStats` for this call."""
        return self.session.stats

    @property
    def is_active(self) -> bool:
        """``True`` while media may flow (accepted and not hung up)."""
        return self.state in _ACTIVE

    @property
    def is_ended(self) -> bool:
        """``True`` once the call has ended."""
        return self.state is CallState.HUNGUP

    @property
    def codec(self) -> Codec | None:
        """The codec used for sending (``None`` if the format has no codec)."""
        return self._tx_codec

    def __repr__(self) -> str:
        return (f"<Call {self.direction.value} {self.callno}/{self.peer_callno} "
                f"{self.info.called_number!r} {self.state.value}>")

    # ---------------------------------------------------------------- events

    def _emit(self, event: CallEvent) -> None:
        if self.on_event is not None:
            try:
                result = self.on_event(event)
                if asyncio.iscoroutine(result):
                    self.endpoint.spawn(result)
            except Exception:  # noqa: BLE001
                log.exception("on_event callback failed")
        self._events.put_nowait(event)

    def _set_state(self, new: CallState) -> None:
        old = self.state
        if old is new or old is CallState.HUNGUP:
            return
        self.state = new
        self._emit(StateChanged(self, old, new))

    async def events(self) -> AsyncIterator[CallEvent]:
        """Iterate over call events until the call ends.

        The final event is always a :class:`~iax2.events.HangupEvent`.
        """
        while True:
            ev = await self._events.get()
            if ev is None:
                return
            yield ev
            if isinstance(ev, HangupEvent):
                return

    async def next_event(self, timeout: float | None = None) -> CallEvent | None:
        """Return the next event, or ``None`` after the call ended / on timeout."""
        try:
            return await asyncio.wait_for(self._events.get(), timeout)
        except TimeoutError:
            return None

    # ----------------------------------------------------------------- waits

    async def wait_accepted(self, timeout: float | None = None) -> None:
        """Wait until the call is accepted.

        Raises:
            CallRejected: if the call ends first.
            TimeoutError: on timeout.
        """
        await self._wait_for(self._accepted, timeout)

    async def wait_answered(self, timeout: float | None = None) -> None:
        """Wait until the call is answered.

        Raises:
            CallRejected: if the call ends first.
            TimeoutError: on timeout.
        """
        await self._wait_for(self._answered, timeout)

    async def wait_ended(self, timeout: float | None = None) -> None:
        """Wait until the call has ended."""
        await asyncio.wait_for(self._ended.wait(), timeout)

    async def _wait_for(self, ev: asyncio.Event, timeout: float | None) -> None:
        if ev.is_set():
            return
        if self._ended.is_set():
            self._raise_ended()
        waiters = [asyncio.ensure_future(ev.wait()), asyncio.ensure_future(self._ended.wait())]
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
        if not done:
            raise TimeoutError
        if not ev.is_set():
            self._raise_ended()

    def _raise_ended(self) -> None:
        if self.session.error == "timeout":
            raise CallTimeout("peer stopped responding")
        raise CallRejected(f"call ended: {self.cause or 'no cause given'}",
                           self.cause, self.cause_code)

    # --------------------------------------------------------- call control

    def _require_active(self) -> None:
        if self.state is CallState.HUNGUP:
            raise CallError("call has ended")

    def accept(self, format: int | None = None) -> int:
        """Accept an incoming call (send ACCEPT).

        Args:
            format: Format to use; default negotiates like Asterisk.

        Returns:
            The accepted format.

        Raises:
            CallError: if the call is not an unaccepted incoming call or no
                common codec exists (the call is rejected in that case).
        """
        if self.direction is not Direction.INCOMING:
            raise CallError("only incoming calls can be accepted")
        if self._accepted.is_set():
            return self.format
        if self.state not in (CallState.INCOMING, CallState.AUTHENTICATING):
            raise CallError(f"cannot accept a call in state {self.state.value}")
        fmt = format if format is not None else self.negotiate_format()
        if not fmt:
            self.reject("Unable to negotiate codec", Cause.BEARERCAPABILITY_NOTAVAIL)
            raise CallError("no common codec")
        self.session.send_command(IAXCommand.ACCEPT, IEs().add(IE.FORMAT, fmt & 0xFFFFFFFF)
                                  .add(IE.FORMAT2, fmt))
        self._on_accepted(fmt)
        return fmt

    def negotiate_format(self) -> int:
        """Pick the audio format for an incoming call (Asterisk's algorithm)."""
        return choose_format(
            self.info.capability,
            self.info.format,
            self.capability,
            our_prefs=self.prefs,
            peer_prefs=self.info.codec_prefs,
            caller_first=self.caller_first,
        )

    async def answer(self, format: int | None = None) -> None:
        """Answer the call (accepting it first if needed) by sending CONTROL ANSWER."""
        self._require_active()
        if self.direction is Direction.INCOMING and not self._accepted.is_set():
            self.accept(format)
        if self.state is CallState.ANSWERED:
            return
        self.session.send(FrameType.CONTROL, ControlType.ANSWER)
        self._on_answered()

    def ring(self) -> None:
        """Indicate ringing to the caller (CONTROL RINGING)."""
        self.indicate(ControlType.RINGING)

    def progress(self) -> None:
        """Indicate call progress (CONTROL PROGRESS)."""
        self.indicate(ControlType.PROGRESS)

    def proceeding(self) -> None:
        """Indicate call proceeding (CONTROL PROCEEDING)."""
        self.indicate(ControlType.PROCEEDING)

    def busy(self) -> None:
        """Indicate busy (CONTROL BUSY)."""
        self.indicate(ControlType.BUSY)

    def congestion(self) -> None:
        """Indicate congestion (CONTROL CONGESTION)."""
        self.indicate(ControlType.CONGESTION)

    def hold(self, music_class: str | None = None) -> None:
        """Put the far end on hold (CONTROL HOLD, optional MOH class as payload)."""
        self.indicate(ControlType.HOLD, (music_class.encode() + b"\0") if music_class else b"")

    def unhold(self) -> None:
        """Take the far end off hold (CONTROL UNHOLD)."""
        self.indicate(ControlType.UNHOLD)

    def radio_key(self) -> None:
        """Key the radio (CONTROL RADIO_KEY) — used by AllStarLink / app_rpt."""
        self.indicate(ControlType.RADIO_KEY)

    def radio_unkey(self) -> None:
        """Unkey the radio (CONTROL RADIO_UNKEY)."""
        self.indicate(ControlType.RADIO_UNKEY)

    def indicate(self, control: int, payload: bytes = b"") -> None:
        """Send a CONTROL frame.

        Control types Asterisk refuses to carry over IAX2 are silently
        dropped, exactly as Asterisk's ``send_command()`` does.
        """
        self._require_active()
        if control not in IAX_ALLOWED_CONTROLS:
            log.debug("%r: blocked sending control %s", self, control)
            return
        self.session.send(FrameType.CONTROL, int(control), payload)

    def quelch(self, music_on_hold: bool = False) -> None:
        """Ask the peer to stop sending media (IAX QUELCH)."""
        self._require_active()
        ies = IEs().add(IE.MUSICONHOLD) if music_on_hold else None
        self.session.send_command(IAXCommand.QUELCH, ies)

    def unquelch(self) -> None:
        """Ask the peer to resume sending media (IAX UNQUELCH)."""
        self._require_active()
        self.session.send_command(IAXCommand.UNQUELCH)

    def transfer(self, number: str, context: str | None = None) -> None:
        """Ask the peer to blind-transfer this call (IAX TRANSFER)."""
        self._require_active()
        self.session.send_command(IAXCommand.TRANSFER,
                                  IEs.build((IE.CALLED_NUMBER, number), (IE.CALLED_CONTEXT, context)))

    def reject(self, cause: str | None = None, cause_code: int = Cause.CALL_REJECTED) -> None:
        """Reject a call that has not been answered (IAX REJECT).

        For calls that are already up, use :meth:`hangup`.
        """
        if self.state is CallState.HUNGUP:
            return
        ies = IEs()
        if cause:
            ies.add(IE.CAUSE, cause)
        ies.add(IE.CAUSECODE, int(cause_code))
        self.session.send_command(IAXCommand.REJECT, ies, final=True)
        self._end(cause, int(cause_code), by_peer=False, reason="local")

    async def hangup(self, cause_code: int = Cause.NORMAL_CLEARING, cause: str | None = None) -> None:
        """Hang up the call (IAX HANGUP) and wait briefly for it to be acknowledged."""
        if self.state is CallState.HUNGUP:
            return
        ies = IEs()
        if cause:
            ies.add(IE.CAUSE, cause)
        ies.add(IE.CAUSECODE, int(cause_code))
        self.session.send_command(IAXCommand.HANGUP, ies, final=True)
        self._end(cause, int(cause_code), by_peer=False, reason="local")
        # Give the HANGUP a chance to be ACKed; the session cleans itself up anyway.
        for _ in range(20):
            if self.session.destroyed:
                break
            await asyncio.sleep(0.05)

    # -------------------------------------------------------------- media out

    def send_audio(self, payload: bytes, *, format: int | None = None,
                   samples: int | None = None) -> None:
        """Send one encoded voice frame.

        Args:
            payload: Encoded audio in ``format``.
            format: Format of ``payload``; defaults to the negotiated format.
            samples: Number of samples (computed from the format if omitted).

        Frames are silently dropped while the call is not yet accepted, or while
        the peer has quelched us — matching Asterisk's ``iax2_write()``.
        """
        fmt = format or self.format
        if not self.is_active or self.quelched or not fmt:
            return
        if samples is None:
            codec = self._tx_codec if fmt == self.format else get_codec(fmt)
            if codec is not None:
                samples = codec.samples(payload)
            else:
                samples = format_info(fmt).samples_for_bytes(len(payload)) or 160
        self.session.send(FrameType.VOICE, fmt, payload, samples=samples)

    def send_pcm(self, pcm: bytes) -> None:
        """Encode s16le PCM with the call's codec and send it as one voice frame.

        Raises:
            CodecError: if no codec is available for the negotiated format.
        """
        if self._tx_codec is None:
            from .exceptions import CodecError

            raise CodecError(f"no codec registered for format {self.format:#x}")
        self.send_audio(self._tx_codec.encode(pcm))

    async def play_pcm(self, pcm: bytes, frame_ms: int = 20) -> None:
        """Send PCM in real time, ``frame_ms`` per frame, paced by the event loop clock.

        Stops early if the call ends.
        """
        if self._tx_codec is None:
            await self.wait_accepted()
        codec = self._tx_codec
        if codec is None:
            from .exceptions import CodecError

            raise CodecError(f"no codec registered for format {self.format:#x}")
        step = 2 * codec.sample_rate * frame_ms // 1000
        loop = asyncio.get_running_loop()
        start = loop.time()
        for n, i in enumerate(range(0, len(pcm), step)):
            if self.is_ended:
                return
            chunk = pcm[i:i + step]
            if len(chunk) < step:
                chunk += bytes(step - len(chunk))
            self.send_pcm(chunk)
            delay = start + (n + 1) * frame_ms / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

    def send_video(self, payload: bytes, format: int, *, mark: bool = False) -> None:
        """Send a video frame/slice in ``format`` (``mark`` ends a picture)."""
        if not self.is_active or self.quelched:
            return
        self.session.send(FrameType.VIDEO, format, payload, mark=mark)

    def send_text(self, text: str) -> None:
        """Send a TEXT frame (NUL-terminated on the wire, like Asterisk)."""
        self._require_active()
        self.session.send(FrameType.TEXT, 0, text.encode("utf-8") + b"\0")

    def send_url(self, url: str) -> None:
        """Send an HTML URL frame."""
        self.send_html(HTMLCommand.URL, url.encode("utf-8") + b"\0")

    def send_html(self, command: int, data: bytes = b"") -> None:
        """Send an HTML frame with sub-command ``command``."""
        self._require_active()
        self.session.send(FrameType.HTML, int(command), data)

    def send_image(self, data: bytes, format: int) -> None:
        """Send an IMAGE frame in image ``format`` (e.g. ``Format.PNG``)."""
        self._require_active()
        self.session.send(FrameType.IMAGE, format, data)

    def send_dtmf_begin(self, digit: str) -> None:
        """Send DTMF key-down for one digit."""
        self._require_active()
        self.session.send(FrameType.DTMF_BEGIN, ord(digit[0]))

    def send_dtmf_end(self, digit: str) -> None:
        """Send DTMF key-up for one digit."""
        self._require_active()
        self.session.send(FrameType.DTMF_END, ord(digit[0]))

    async def send_dtmf(self, digits: str, duration: float = 0.1, gap: float = 0.05) -> None:
        """Send DTMF digits, each as a BEGIN/END pair ``duration`` seconds apart."""
        for d in digits:
            self.send_dtmf_begin(d)
            await asyncio.sleep(duration)
            self.send_dtmf_end(d)
            await asyncio.sleep(gap)

    # --------------------------------------------------------------- media in

    async def recv_audio(self, timeout: float | None = None) -> AudioFrame | None:
        """Return the next received audio frame.

        Returns ``None`` when the call has ended (and the queue is drained) or
        on timeout.
        """
        if self._ended.is_set() and self._audio.empty():
            return None
        try:
            return await asyncio.wait_for(self._audio.get(), timeout)
        except TimeoutError:
            return None

    async def playout(self, *, frame_ms: int = 20, min_delay: int = 40, max_delay: int = 500,
                      conceal: bool = True) -> AsyncIterator[AudioFrame]:
        """Iterate over received audio smoothed by a :class:`~iax2.jitterbuffer.JitterBuffer`.

        Frames are yielded at a steady ``frame_ms`` pace, in timestamp order,
        delayed just enough to absorb network jitter. Lost frames are
        replaced by encoded silence with :attr:`AudioFrame.lost` set (or
        skipped if ``conceal`` is false or the format has no codec).

        While iterating, received audio is routed into the jitter buffer
        rather than :meth:`recv_audio`.
        """
        from .jitterbuffer import JitterBuffer

        loop = asyncio.get_running_loop()
        jb: JitterBuffer[AudioFrame] = JitterBuffer(frame_ms, min_delay, max_delay)
        prev = self.on_audio
        self.on_audio = lambda f: jb.put(f, loop.time() * 1000)
        self._jitterbuffer = jb
        try:
            next_tick = loop.time()
            while not self.is_ended:
                for item in jb.get(loop.time() * 1000):
                    if item is not None:
                        yield item
                    elif conceal and self._rx_codec is not None:
                        yield AudioFrame(self._rx_codec.silence(frame_ms), self._rx_format, 0,
                                         codec=self._rx_codec, lost=True)
                next_tick += frame_ms / 1000
                delay = next_tick - loop.time()
                if delay < -1:
                    next_tick = loop.time()
                await asyncio.sleep(max(0.0, delay))
        finally:
            self.on_audio = prev
            self._jitterbuffer = None

    async def audio(self) -> AsyncIterator[AudioFrame]:
        """Iterate over received audio frames until the call ends."""
        while True:
            frame = await self.recv_audio()
            if frame is None:
                return
            yield frame

    # ------------------------------------------------- session owner protocol

    def on_iax(self, session: Session, frame: FullFrame, ies: IEs) -> None:
        cmd = frame.subclass
        handler = getattr(self, f"_iax_{IAXCommand(cmd).name.lower()}", None) \
            if cmd in IAXCommand._value2member_map_ else None
        if handler is not None:
            handler(frame, ies)
        else:
            log.debug("%r: unsupported IAX command %s", self, cmd)
            session.send_command(IAXCommand.UNSUPPORT, IEs().add(IE.IAX_UNKNOWN, int(cmd) & 0xFF))

    def on_media(self, session: Session, frame_type: int, subclass: int, payload: bytes,
                 timestamp: int, full: bool, mark: bool) -> None:
        if frame_type == FrameType.VOICE:
            if subclass != self._rx_format:
                changed = bool(self._rx_format)
                self._rx_format = subclass
                self._rx_codec = get_codec(subclass)
                if changed:
                    self._emit(FormatChanged(self, subclass))
            af = AudioFrame(payload, subclass, timestamp, full, self._rx_codec)
            if self.on_audio is not None:
                try:
                    r = self.on_audio(af)
                    if asyncio.iscoroutine(r):
                        self.endpoint.spawn(r)
                except Exception:  # noqa: BLE001
                    log.exception("on_audio callback failed")
                return
            if self._audio.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._audio.get_nowait()  # drop oldest
            self._audio.put_nowait(af)
        elif frame_type == FrameType.VIDEO:
            vf = VideoFrameData(payload, subclass, timestamp, mark)
            if self.on_video is not None:
                self.on_video(vf)
        elif frame_type == FrameType.CONTROL:
            self._on_control(subclass, payload)
        elif frame_type in (FrameType.DTMF_BEGIN, FrameType.DTMF_END):
            self._emit(DTMFEvent(self, chr(subclass & 0xFF), frame_type == FrameType.DTMF_BEGIN))
        elif frame_type == FrameType.TEXT:
            self._emit(TextEvent(self, payload.split(b"\0", 1)[0].decode("utf-8", "replace")))
        elif frame_type == FrameType.HTML:
            self._emit(HTMLEvent(self, subclass, payload))
        elif frame_type == FrameType.IMAGE:
            self._emit(ImageEvent(self, subclass, payload))
        elif frame_type in (FrameType.NULL, FrameType.CNG):
            pass
        else:
            self._emit(UnknownFrame(self, frame_type, subclass, payload))

    def on_session_error(self, session: Session, reason: str) -> None:
        self._end("peer not responding", int(Cause.DESTINATION_OUT_OF_ORDER), by_peer=True,
                  reason="timeout")

    def on_session_destroyed(self, session: Session) -> None:
        if self.state is not CallState.HUNGUP:
            self._end(self.cause, self.cause_code, by_peer=True, reason=session.error or "hangup")

    def on_transfer_timeout(self, session: Session) -> None:
        self.endpoint.transfers.on_timeout(self)

    # ----------------------------------------------------------- IAX handlers

    def _iax_hangup(self, frame: FullFrame, ies: IEs) -> None:
        self.session.send_ack(frame)
        self._end(ies.get(IE.CAUSE), ies.get(IE.CAUSECODE), by_peer=True, reason="hangup")
        self.session.destroy()

    def _iax_reject(self, frame: FullFrame, ies: IEs) -> None:
        self.session.send_ack(frame)
        self._end(ies.get(IE.CAUSE), ies.get(IE.CAUSECODE), by_peer=True, reason="reject")
        self.session.destroy()

    def _iax_accept(self, frame: FullFrame, ies: IEs) -> None:
        if self.direction is not Direction.OUTGOING or self._accepted.is_set():
            return
        fmt = ies.format or self.capability
        if not fmt & self.capability:
            self.session.send_command(
                IAXCommand.REJECT,
                IEs().add(IE.CAUSE, "Unable to negotiate codec")
                .add(IE.CAUSECODE, int(Cause.BEARERCAPABILITY_NOTAVAIL)),
                final=True,
            )
            self._end("Unable to negotiate codec", int(Cause.BEARERCAPABILITY_NOTAVAIL),
                      by_peer=False, reason="local")
            return
        if fmt.bit_count() > 1:
            from .formats import best_format

            fmt = best_format(fmt & self.capability) or fmt & -fmt
        self._on_accepted(fmt)

    def _iax_authreq(self, frame: FullFrame, ies: IEs) -> None:
        if self.direction is not Direction.OUTGOING or self._accepted.is_set():
            return
        creds = self.credentials or Credentials()
        if creds.username is None and ies.get(IE.USERNAME):
            creds = Credentials(ies.get(IE.USERNAME), creds.secret, creds.private_key)
        self._set_state(CallState.AUTHENTICATING)
        try:
            reply, method = build_auth_reply(creds, ies.get(IE.AUTHMETHODS, 0),
                                             ies.get(IE.CHALLENGE))
        except AuthenticationError as exc:
            log.warning("%r: %s", self, exc)
            self.session.send_command(IAXCommand.HANGUP,
                                      IEs().add(IE.CAUSECODE, int(Cause.NORMAL_CLEARING)),
                                      final=True)
            self._end(str(exc), int(Cause.FACILITY_NOT_SUBSCRIBED), by_peer=False, reason="auth")
            return
        self.auth_method = method
        challenge = ies.get(IE.CHALLENGE)
        # merge_encryption(): only with MD5, and only methods both sides offered
        enc = ies.get(IE.ENCRYPTION, 0) & self.info.encryption
        if enc and method is AuthMethod.MD5 and creds.secret and challenge:
            from . import crypto

            crypto.enable_caller(self.session, challenge, creds.secret, enc)
        elif self.endpoint.config.force_encryption:
            self.session.send_command(IAXCommand.HANGUP,
                                      IEs().add(IE.CAUSECODE, int(Cause.FACILITY_REJECTED)),
                                      final=True)
            self._end("encryption not negotiated", int(Cause.FACILITY_REJECTED), by_peer=False,
                      reason="local")
            return
        self.session.send_command(IAXCommand.AUTHREP, reply)

    def _iax_authrep(self, frame: FullFrame, ies: IEs) -> None:
        if self.direction is not Direction.INCOMING or self._challenge is None:
            return
        if self._accepted.is_set():
            return
        creds = self.credentials or Credentials()
        method = verify_auth_reply(creds, self._challenge, ies)
        if method is None:
            log.info("%r: authentication failed", self)
            self.endpoint.auth_failed(self, IAXCommand.REJECT)
            return
        self.auth_method = method
        self._challenge = None
        self.endpoint.call_authorized(self)

    def _iax_quelch(self, frame: FullFrame, ies: IEs) -> None:
        if self.is_active:
            self.quelched = True
            self._emit(QuelchEvent(self, True, IE.MUSICONHOLD in ies))

    def _iax_unquelch(self, frame: FullFrame, ies: IEs) -> None:
        if self.is_active:
            self.quelched = False
            self._emit(QuelchEvent(self, False))

    def _iax_transfer(self, frame: FullFrame, ies: IEs) -> None:
        self._emit(TransferRequest(self, ies.get(IE.CALLED_NUMBER, ""), ies.get(IE.CALLED_CONTEXT)))

    def _iax_new(self, frame: FullFrame, ies: IEs) -> None:
        pass  # retransmitted NEW for a call already in progress: ignore

    def _iax_pong(self, frame: FullFrame, ies: IEs) -> None:
        pass

    def _iax_unsupport(self, frame: FullFrame, ies: IEs) -> None:
        log.info("%r: peer did not understand our command %s", self, ies.get(IE.IAX_UNKNOWN))

    def _iax_txreq(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txreq(self, frame, ies)

    def _iax_txcnt(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txcnt(self, frame, ies)

    def _iax_txacc(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txacc(self, frame, ies)

    def _iax_txready(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txready(self, frame, ies)

    def _iax_txrel(self, frame: FullFrame, ies: IEs) -> None:
        self.session.send_ack(frame)
        self.endpoint.transfers.on_txrel(self, frame, ies)

    def _iax_txrej(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txrej(self, frame, ies)

    def _iax_txmedia(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.transfers.on_txmedia(self, frame, ies)

    def _iax_rtkey(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.rotate_key(self, ies)

    def _iax_dial(self, frame: FullFrame, ies: IEs) -> None:
        self._emit(TransferRequest(self, ies.get(IE.CALLED_NUMBER, ""), None))

    def _iax_dprep(self, frame: FullFrame, ies: IEs) -> None:
        number = ies.get(IE.CALLED_NUMBER, "")
        fut = self._dp_waiters.pop(number, None)
        if fut is not None and not fut.done():
            fut.set_result(int(ies.get(IE.DPSTATUS, 0)))

    async def query_dialplan(self, number: str, timeout: float = 5.0) -> int:
        """Ask the peer whether ``number`` exists in its dialplan (DPREQ/DPREP).

        Returns:
            The DPSTATUS bit mask (see :class:`~iax2.constants.DPStatus`).
        """
        self._require_active()
        fut = self._dp_waiters.get(number)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._dp_waiters[number] = fut
            self.session.send_command(IAXCommand.DPREQ, IEs().add(IE.CALLED_NUMBER, number))
        return await asyncio.wait_for(asyncio.shield(fut), timeout)

    def _iax_dpreq(self, frame: FullFrame, ies: IEs) -> None:
        self.endpoint.dialplan_request(self, ies)

    # ---------------------------------------------------------- transitions

    def _start_incoming_auth(self, creds: Credentials) -> None:
        """Send AUTHREQ for an incoming call (server side)."""
        self.credentials = creds
        self._challenge = new_challenge()
        methods = creds.offered_methods()
        ies = IEs().add(IE.AUTHMETHODS, int(methods))
        ies.add(IE.CHALLENGE, self._challenge)
        enc = 0
        if methods & AuthMethod.MD5 and creds.secret and self.endpoint.encryption_supported():
            enc = self.endpoint.negotiated_encryption(self.info.encryption)
            if enc:
                ies.add(IE.ENCRYPTION, enc)
        ies.add(IE.USERNAME, self.info.username or creds.username or "")
        self._set_state(CallState.AUTHENTICATING)
        self.session.send_command(IAXCommand.AUTHREQ, ies)
        if enc:
            # From now on the caller encrypts (starting with AUTHREP).
            from . import crypto

            crypto.enable_callee(self.session, self._challenge, creds.secret.split(";"), enc)

    def _on_accepted(self, fmt: int) -> None:
        self.format = fmt
        self._tx_codec = get_codec(fmt)
        self.session.started = True
        self._set_state(CallState.ACCEPTED)
        self._accepted.set()
        self._emit(Accepted(self, fmt))

    def _on_answered(self) -> None:
        self._set_state(CallState.ANSWERED)
        self._answered.set()
        self._emit(Answered(self))

    def _on_control(self, control: int, payload: bytes) -> None:
        if control not in IAX_ALLOWED_CONTROLS:
            return
        if control == ControlType.ANSWER:
            if self.state is not CallState.ANSWERED:
                self._on_answered()
        elif control == ControlType.RINGING:
            if self.state is not CallState.ANSWERED:
                self._set_state(CallState.RINGING)
            self._emit(Ringing(self))
        elif control in (ControlType.PROGRESS, ControlType.PROCEEDING):
            self._emit(Progress(self, control == ControlType.PROCEEDING))
        elif control == ControlType.HANGUP:
            self._end(None, None, by_peer=True, reason="hangup")
            self.endpoint.spawn(self.hangup())
        else:
            if control == ControlType.BUSY:
                self.cause_code = int(Cause.USER_BUSY)
            elif control == ControlType.CONGESTION:
                self.cause_code = int(Cause.NORMAL_CIRCUIT_CONGESTION)
            self._emit(ControlEvent(self, control, payload))

    def _end(self, cause: str | None, cause_code: int | None, *, by_peer: bool,
             reason: str) -> None:
        if self.state is CallState.HUNGUP:
            return
        self.cause = cause if cause is not None else self.cause
        self.cause_code = cause_code if cause_code is not None else self.cause_code
        self._set_state(CallState.HUNGUP)
        self.session.stop_keepalives()
        self._emit(HangupEvent(self, self.cause, self.cause_code, by_peer, reason))
        self._ended.set()
        self._events.put_nowait(None)
        with contextlib.suppress(asyncio.QueueFull):
            self._audio.put_nowait(None)
        self.endpoint.call_ended(self)

    def __del__(self) -> None:  # pragma: no cover - diagnostics only
        pass


def _audio_mask(mask: int) -> int:
    return mask & AUDIO_MASK

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""The :class:`Endpoint` — the main entry point of the library.

An endpoint owns one UDP socket and every IAX2 dialog running over it:
outgoing and incoming calls, registrations (as client and as registrar)
and POKE qualification. It can act as a client, a server, or both at once.

Example — a client::

    import asyncio, iax2

    async def main():
        async with iax2.Endpoint(port=0) as ep:            # any free port
            call = await ep.dial("192.0.2.10", "1000", username="me", secret="pw")
            await call.play_pcm(open("hello.raw", "rb").read())
            await call.hangup()

    asyncio.run(main())

Example — a server that answers everything with an echo::

    async def main():
        async with iax2.Endpoint(port=4569) as ep:
            async for call in ep.incoming():
                asyncio.create_task(echo(call))

    async def echo(call):
        await call.answer()
        async for frame in call.audio():
            call.send_audio(frame.payload)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import socket
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .auth import Credentials
from .call import Call, CallInfo, CallState, Direction
from .calltoken import CallTokenGenerator
from .codecs import available_formats
from .constants import DEFAULT_PORT, IE, Cause, DPStatus, FrameType, IAXCommand
from .exceptions import CallRejected, IAX2Error, TransportError
from .formats import AUDIO_MASK, Format, best_format, iter_formats
from .frames import (
    FrameError,
    FullFrame,
    MiniFrame,
    TrunkFrame,
    VideoFrame,
    parse_datagram,
    peek_call_numbers,
)
from .ies import IEs
from .session import Session, Timing

if TYPE_CHECKING:  # pragma: no cover
    from .registration import Registrar, Registration

__all__ = ["Endpoint", "EndpointConfig"]

log = logging.getLogger("iax2.endpoint")
wire_log = logging.getLogger("iax2.wire")

Addr = tuple[str, int]

#: Return type of an :attr:`EndpointConfig.authenticator`.
AuthDecision = Credentials | bool | None
Authenticator = Callable[[CallInfo, Addr], AuthDecision | Awaitable[AuthDecision]]

_TRUNK_CALL_START = 16384
_MIN_REUSE = 60.0


@dataclass
class EndpointConfig:
    """Endpoint settings. All have sensible, Asterisk-compatible defaults.

    Attributes:
        capability: Audio formats we can handle. Defaults to every format with
            a registered codec (ulaw, alaw, slin, slin16 out of the box).
        prefs: Our codec preference order (Asterisk ``allow=`` order).
        caller_first: Prefer the caller's codec order on incoming calls
            (Asterisk ``codecpriority=caller``); default is ours (``host``).
        require_calltoken: Require call tokens on incoming NEW/REGREQ
            (Asterisk ``requirecalltoken=yes``).
        calltoken_max_delay: Seconds a call token remains valid.
        users: ``{username: Credentials}`` used by the default authenticator.
        allow_guest: Default authenticator: let callers without a known
            username in without authentication.
        authenticator: Custom policy ``(info, addr) -> Credentials | True |
            None | False`` (may be async). ``Credentials`` require the caller to
            authenticate with them; ``True``/``None`` admit without
            authentication; ``False`` rejects.
        auto_accept: Send ACCEPT as soon as an incoming call is authorised
            (like Asterisk). If ``False`` the application must call
            :meth:`Call.accept` or :meth:`Call.answer`.
        delay_reject: Delay authentication-failure rejects by 1 s (Asterisk
            ``delayreject``).
        timing: Protocol timer settings.
        max_incoming_queue: Unclaimed incoming calls kept for :meth:`Endpoint.incoming`.
        encryption: Encryption methods we offer/accept (see :mod:`iax2.crypto`).
        force_encryption: Refuse calls that do not negotiate encryption.
        trunk_peers: Addresses to which outgoing voice is sent in trunk frames.
        trunk_interval: Trunk frame interval in ms.
        trunk_timestamps: Send trunk frames with per-call timestamps.
        language: Default LANGUAGE IE for outgoing calls.
    """

    capability: int | None = None
    prefs: list[int] | None = None
    caller_first: bool = False
    require_calltoken: bool = True
    calltoken_max_delay: int = 10
    users: dict[str, Credentials] = field(default_factory=dict)
    allow_guest: bool = True
    authenticator: Authenticator | None = None
    auto_accept: bool = True
    delay_reject: bool = False
    timing: Timing = field(default_factory=Timing)
    max_incoming_queue: int = 64
    encryption: int = 0
    force_encryption: bool = False
    trunk_peers: set[Addr] = field(default_factory=set)
    trunk_interval: int = 20
    trunk_timestamps: bool = False
    language: str | None = None


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint

    def datagram_received(self, data: bytes, addr: Any) -> None:
        try:
            self.endpoint._datagram_received(data, (addr[0], addr[1]))
        except Exception:  # noqa: BLE001 - never let a bad packet kill the transport
            log.exception("error processing datagram from %s", addr)

    def error_received(self, exc: Exception) -> None:
        log.debug("socket error: %s", exc)


class Endpoint:
    """An IAX2 endpoint bound to one UDP socket.

    Use as an async context manager, or call :meth:`start` and :meth:`close`.

    Args:
        host: Local address to bind (default all IPv4 interfaces).
        port: Local UDP port (default 4569; 0 picks a free port).
        config: :class:`EndpointConfig`; keyword arguments override fields.
        sock: An already-bound UDP socket to use instead of ``host``/``port``.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 config: EndpointConfig | None = None, *, sock: socket.socket | None = None,
                 **overrides: Any) -> None:
        cfg = config or EndpointConfig()
        for k, v in overrides.items():
            if not hasattr(cfg, k):
                raise TypeError(f"unknown EndpointConfig option {k!r}")
            setattr(cfg, k, v)
        self.config = cfg
        self.host = host
        self.port = port
        self._sock = sock
        if cfg.capability is None:
            cfg.capability = available_formats() & AUDIO_MASK
        if cfg.prefs is None:
            order = [Format.ULAW, Format.ALAW, Format.SLIN16, Format.SLIN, Format.G722, Format.GSM]
            cfg.prefs = [f for f in order if f & cfg.capability] + [
                f for f in iter_formats(cfg.capability) if f not in order
            ]
        self.calltokens = CallTokenGenerator(cfg.calltoken_max_delay)
        self.clock: Callable[[], float] = time.monotonic
        self._loop: asyncio.AbstractEventLoop | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._sessions: dict[int, Session] = {}
        self._by_peer: dict[tuple[Addr, int], Session] = {}
        self._by_transfer: dict[tuple[Addr, int], Session] = {}
        self._recent: dict[int, float] = {}
        self._calls: set[Call] = set()
        self._incoming: asyncio.Queue[Call] = asyncio.Queue(cfg.max_incoming_queue)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self.on_call: Callable[[Call], Any] | None = None
        """Callback for new incoming calls (instead of :meth:`incoming`)."""
        self.registrar: Registrar | None = None
        """Server-side registrar; set with :meth:`enable_registrar`."""
        self.registrations: list[Registration] = []
        self.on_mwi: Callable[[Addr, IEs], Any] | None = None
        """Callback for unsolicited MWI messages."""
        self.dialplan: Callable[[Call, str], int] | None = None
        """Optional ``(call, number) -> DPStatus`` hook for DPREQ queries."""
        from .transfer import TransferManager
        from .trunk import TrunkManager

        self.transfers = TransferManager(self)
        self.trunks = TrunkManager(self)

    # ---------------------------------------------------------------- setup

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The event loop the endpoint runs on."""
        if self._loop is None:
            raise TransportError("endpoint not started")
        return self._loop

    async def start(self) -> Endpoint:
        """Bind the socket and start processing packets. Returns ``self``."""
        if self._transport is not None:
            return self
        self._loop = asyncio.get_running_loop()
        try:
            if self._sock is not None:
                transport, _ = await self._loop.create_datagram_endpoint(
                    lambda: _Protocol(self), sock=self._sock)
            else:
                family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
                transport, _ = await self._loop.create_datagram_endpoint(
                    lambda: _Protocol(self), local_addr=(self.host, self.port), family=family)
        except OSError as exc:
            raise TransportError(f"cannot bind {self.host}:{self.port}: {exc}") from exc
        self._transport = transport
        self.host, self.port = transport.get_extra_info("sockname")[:2]
        log.info("IAX2 endpoint listening on %s:%s", self.host, self.port)
        return self

    async def close(self) -> None:
        """Hang up all calls, stop registrations and close the socket."""
        if self._closed:
            return
        self._closed = True
        for reg in list(self.registrations):
            await reg.stop(unregister=True)
        calls = [c for c in self._calls if not c.is_ended]
        await asyncio.gather(*(c.hangup() for c in calls), return_exceptions=True)
        self.trunks.close()
        for s in list(self._sessions.values()):
            s.destroy()
        for t in list(self._tasks):
            t.cancel()
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def __aenter__(self) -> Endpoint:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def local_address(self) -> Addr:
        """The bound ``(host, port)``."""
        return (self.host, self.port)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run a coroutine as a task tied to the endpoint's lifetime."""
        task = self.loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task failed", exc_info=task.exception())

    # --------------------------------------------------------- call numbers

    def _alloc_callno(self, trunk: bool = False) -> int:
        lo, hi = (_TRUNK_CALL_START, 32767) if trunk else (2, _TRUNK_CALL_START - 1)
        now = self.clock()
        for k in [k for k, t in self._recent.items() if now - t > _MIN_REUSE]:
            del self._recent[k]
        for _ in range(64):
            n = random.randint(lo, hi)
            if n not in self._sessions and n not in self._recent:
                return n
        for n in range(lo, hi + 1):
            if n not in self._sessions and n not in self._recent:
                return n
        raise IAX2Error("no free call numbers")

    def new_session(self, addr: Addr, owner: Any = None, *, peer_callno: int = 0) -> Session:
        """Allocate a call number and create a :class:`Session` for ``addr``."""
        s = Session(self, self._alloc_callno(), addr, owner, peer_callno=peer_callno,
                    timing=self.config.timing)
        self._sessions[s.callno] = s
        if peer_callno:
            self._by_peer[(addr, peer_callno)] = s
        if addr in self.config.trunk_peers:
            s.trunk = True
        return s

    def release_session(self, session: Session) -> None:
        """Forget a destroyed session (called by :meth:`Session.destroy`)."""
        if self._sessions.get(session.callno) is session:
            del self._sessions[session.callno]
            self._recent[session.callno] = self.clock()
        key = (session.addr, session.peer_callno)
        if self._by_peer.get(key) is session:
            del self._by_peer[key]
        self.unindex_transfer(session)

    def rebind_peer_callno(self, session: Session, peer_callno: int) -> None:
        """Update the peer call number index for ``session``."""
        old = (session.addr, session.peer_callno)
        if self._by_peer.get(old) is session:
            del self._by_peer[old]
        session.peer_callno = peer_callno
        if peer_callno:
            self._by_peer[(session.addr, peer_callno)] = session

    def index_transfer(self, session: Session) -> None:
        """Make ``session`` findable by its transfer peer's address and call number."""
        if session.transfer_addr is not None:
            self._by_transfer[(session.transfer_addr, session.transfer_callno)] = session

    def unindex_transfer(self, session: Session) -> None:
        """Remove ``session`` from the transfer-peer index."""
        if session.transfer_addr is not None:
            key = (session.transfer_addr, session.transfer_callno)
            if self._by_transfer.get(key) is session:
                del self._by_transfer[key]

    def rebind_address(self, session: Session, addr: Addr) -> None:
        """Move ``session`` to a new peer address (after a transfer)."""
        old = (session.addr, session.peer_callno)
        if self._by_peer.get(old) is session:
            del self._by_peer[old]
        session.addr = addr
        if session.peer_callno:
            self._by_peer[(addr, session.peer_callno)] = session

    # --------------------------------------------------------------- sending

    def sendto(self, data: bytes, addr: Addr) -> None:
        """Send a raw datagram."""
        if self._transport is None:
            return
        if wire_log.isEnabledFor(logging.DEBUG):
            try:
                f = parse_datagram(data)
                desc = f.describe() if isinstance(f, FullFrame) else repr(f)[:120]
            except FrameError:
                desc = f"<{len(data)} bytes>"
            wire_log.debug("TX %s:%s %s", addr[0], addr[1], desc)
        self._transport.sendto(data, addr)

    def send_apathetic(self, addr: Addr, dest_call: int, command: int, ts: int, iseqno: int,
                       ies: IEs | None = None) -> None:
        """Send a stateless reply from call number 1 (Asterisk ``send_apathetic_reply``)."""
        f = FullFrame.iax(command, ies, source_call=1, dest_call=dest_call, timestamp=ts,
                          oseqno=0, iseqno=iseqno & 0xFF)
        self.sendto(f.to_bytes(), addr)

    def send_inval(self, addr: Addr, src: int, dst: int) -> None:
        """Send INVAL for an unknown call (Asterisk ``raw_hangup``)."""
        f = FullFrame.iax(IAXCommand.INVAL, source_call=src & 0x7FFF, dest_call=dst & 0x7FFF)
        self.sendto(f.to_bytes(), addr)

    def build_rr(self, session: Session) -> IEs:
        """Receiver-report IEs for a PONG (Asterisk ``construct_rr``)."""
        st = session.stats
        ies = IEs()
        ies.add(IE.RR_JITTER, 0)
        ies.add(IE.RR_LOSS, 0)
        ies.add(IE.RR_PKTS, max(1, st.frames_received) & 0xFFFFFFFF)
        ies.add(IE.RR_DELAY, 0)
        ies.add(IE.RR_DROPPED, 0)
        ies.add(IE.RR_OOO, 0)
        return ies

    def trunk_queue(self, session: Session, ts: int, payload: bytes) -> None:
        """Queue voice for the trunk to ``session``'s peer."""
        self.trunks.queue(session, ts, payload)

    # ------------------------------------------------------------- receiving

    def _datagram_received(self, data: bytes, addr: Addr) -> None:
        kind, src, dst = peek_call_numbers(data)
        enc: Session | None = None
        if kind == "full" and dst not in (0, 1):
            enc = self._sessions.get(dst)
        elif kind == "mini":
            enc = self._by_peer.get((addr, src)) or self._by_transfer.get((addr, src))
        if enc is not None and enc.crypto is not None and enc.crypto.rx_active:
            plain = enc.crypto.decrypt(data)
            if plain is None:
                log.info("%r: packet decrypt failed", enc)
                return
            data = plain
        try:
            frame = parse_datagram(data)
        except FrameError as exc:
            log.debug("dropping bad datagram from %s: %s", addr, exc)
            return
        if isinstance(frame, FullFrame):
            if wire_log.isEnabledFor(logging.DEBUG):
                wire_log.debug("RX %s:%s %s", addr[0], addr[1], frame.describe())
            self._handle_full(frame, addr, data)
        elif isinstance(frame, MiniFrame):
            key = (addr, frame.source_call)
            s = self._by_peer.get(key) or self._by_transfer.get(key)
            if s is not None:
                s.handle_mini(frame)
        elif isinstance(frame, VideoFrame):
            s = self._by_peer.get((addr, frame.source_call))
            if s is not None:
                s.handle_video(frame)
        elif isinstance(frame, TrunkFrame):
            self.trunks.receive(frame, addr)

    def session_for_peer(self, addr: Addr, peer_callno: int) -> Session | None:
        """Find a session by peer address and peer call number."""
        return self._by_peer.get((addr, peer_callno))

    def _handle_full(self, frame: FullFrame, addr: Addr, data: bytes) -> None:
        s: Session | None = None
        if frame.dest_call:
            s = self._sessions.get(frame.dest_call)
            if s is not None and s.addr != addr and not (
                s.transfer_addr == addr
                or (frame.frame_type == FrameType.IAX
                    and frame.subclass in (IAXCommand.TXCNT, IAXCommand.TXACC))
            ):
                s = None
        else:
            s = self._by_peer.get((addr, frame.source_call))
        if s is not None:
            s.handle_full(frame, addr)
            return
        self._handle_unknown(frame, addr)

    def _handle_unknown(self, frame: FullFrame, addr: Addr) -> None:
        is_iax = frame.frame_type == FrameType.IAX
        cmd = frame.subclass if is_iax else None
        if is_iax and cmd == IAXCommand.POKE:
            self.send_apathetic(addr, frame.source_call, IAXCommand.PONG, frame.timestamp,
                                frame.iseqno + 1)
            return
        if is_iax and cmd == IAXCommand.ACK and frame.dest_call == 1:
            return
        if is_iax and cmd == IAXCommand.MWI and not frame.dest_call:
            self._handle_mwi(frame, addr)
            return
        if not frame.dest_call and is_iax and cmd in (
            IAXCommand.NEW, IAXCommand.REGREQ, IAXCommand.REGREL, IAXCommand.FWDOWNL
        ):
            try:
                ies = frame.ies
            except FrameError:
                log.warning("undecodable %s from %s", frame.describe(), addr)
                return
            if not self._check_calltoken(frame, ies, addr):
                return
            if cmd == IAXCommand.NEW:
                self._new_incoming_call(frame, ies, addr)
            elif cmd in (IAXCommand.REGREQ, IAXCommand.REGREL):
                if self.registrar is None:
                    self.send_apathetic(addr, frame.source_call, IAXCommand.REGREJ,
                                        frame.timestamp, frame.iseqno + 1,
                                        IEs().add(IE.CAUSE, "Registration Refused")
                                        .add(IE.CAUSECODE, int(Cause.FACILITY_REJECTED)))
                    return
                s = self.new_session(addr, None, peer_callno=frame.source_call)
                self.registrar.attach(s)
                s.handle_full(frame, addr)
            else:  # FWDOWNL: firmware download is not supported
                self.send_apathetic(addr, frame.source_call, IAXCommand.UNSUPPORT,
                                    frame.timestamp, frame.iseqno + 1)
            return
        if not (is_iax and cmd in (IAXCommand.INVAL, IAXCommand.TXCNT, IAXCommand.TXACC,
                                   IAXCommand.FWDOWNL)):
            log.debug("frame for unknown call from %s: %s", addr, frame.describe())
            self.send_inval(addr, frame.dest_call, frame.source_call)

    def _check_calltoken(self, frame: FullFrame, ies: IEs, addr: Addr) -> bool:
        """Asterisk ``handle_call_token``: returns ``True`` to proceed."""
        token = ies.calltoken
        if token == b"":
            tok = self.calltokens.issue(addr[0], addr[1])
            self.send_apathetic(addr, frame.source_call, IAXCommand.CALLTOKEN, frame.timestamp,
                                frame.iseqno + 1, IEs().add(IE.CALLTOKEN, tok))
            return False
        if token is not None:
            if self.calltokens.validate(token, addr[0], addr[1]):
                return True
            log.warning("%s:%s failed call token validation", *addr)
        elif not self.config.require_calltoken:
            return True
        else:
            log.warning("call from %s:%s rejected: call token required", *addr)
        cmd = IAXCommand.REGREJ if frame.subclass in (IAXCommand.REGREQ, IAXCommand.REGREL) \
            else IAXCommand.REJECT
        self.send_apathetic(addr, frame.source_call, cmd, frame.timestamp, frame.iseqno + 1)
        return False

    def _handle_mwi(self, frame: FullFrame, addr: Addr) -> None:
        if self.on_mwi is not None:
            try:
                self.on_mwi(addr, frame.ies)
            except Exception:  # noqa: BLE001
                log.exception("on_mwi callback failed")

    # -------------------------------------------------------- incoming calls

    def _new_incoming_call(self, frame: FullFrame, ies: IEs, addr: Addr) -> None:
        info = CallInfo.from_ies(ies)
        session = self.new_session(addr, None, peer_callno=frame.source_call)
        call = Call(self, session, Direction.INCOMING, info,
                    capability=self.config.capability or 0, prefs=self.config.prefs or [],
                    caller_first=self.config.caller_first)
        self._calls.add(call)
        session.start_keepalives()
        session.handle_full(frame, addr)  # sequences + ACKs the NEW
        self.spawn(self._authorize(call, addr))

    async def _authorize(self, call: Call, addr: Addr) -> None:
        info = call.info
        try:
            if self.config.authenticator is not None:
                decision = self.config.authenticator(info, addr)
                if inspect.isawaitable(decision):
                    decision = await decision
            elif info.username and info.username in self.config.users:
                decision = self.config.users[info.username]
            elif self.config.allow_guest:
                decision = None
            else:
                decision = False
        except Exception:  # noqa: BLE001
            log.exception("authenticator failed")
            decision = False
        if call.is_ended:
            return
        if decision is False:
            self.auth_failed(call, IAXCommand.REJECT)
            return
        if self.config.force_encryption and not (info.encryption & self.config.encryption):
            call.reject("Encryption required", Cause.FACILITY_REJECTED)
            return
        if isinstance(decision, Credentials) and (decision.secret or decision.public_keys):
            call._start_incoming_auth(decision)
            return
        self.call_authorized(call)

    def auth_failed(self, call: Call, command: int) -> None:
        """Reject a call/registration after failed authentication (Asterisk ``auth_fail``)."""
        def reject() -> None:
            if call.is_ended:
                return
            call.reject("No authority found", Cause.FACILITY_NOT_SUBSCRIBED)

        if self.config.delay_reject:
            call.session.call_later(1.0, reject)
        else:
            reject()

    def call_authorized(self, call: Call) -> None:
        """An incoming call passed authentication: accept it and hand it to the app."""
        if self.config.auto_accept:
            fmt = call.negotiate_format()
            if not fmt:
                call.reject("Unable to negotiate codec", Cause.BEARERCAPABILITY_NOTAVAIL)
                return
            call.accept(fmt)
        else:
            call._set_state(CallState.INCOMING)
        if self.on_call is not None:
            try:
                r = self.on_call(call)
                if inspect.isawaitable(r):
                    self.spawn(r)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                log.exception("on_call callback failed")
            return
        if self._incoming.full():
            log.warning("incoming call queue full; rejecting %r", call)
            call.reject("Too many unanswered calls", Cause.SWITCH_CONGESTION)
            return
        self._incoming.put_nowait(call)

    async def accept_call(self, timeout: float | None = None) -> Call | None:
        """Wait for the next incoming call (``None`` on timeout)."""
        try:
            return await asyncio.wait_for(self._incoming.get(), timeout)
        except TimeoutError:
            return None

    async def incoming(self) -> AsyncIterator[Call]:
        """Iterate over incoming calls as they arrive.

        Calls are delivered after authentication (and after ACCEPT when
        :attr:`EndpointConfig.auto_accept` is set).
        """
        while not self._closed:
            call = await self._incoming.get()
            if not call.is_ended:
                yield call

    def call_ended(self, call: Call) -> None:
        """Bookkeeping when a call ends (called by :class:`Call`)."""
        self._calls.discard(call)

    @property
    def calls(self) -> list[Call]:
        """Calls currently in progress."""
        return [c for c in self._calls if not c.is_ended]

    # -------------------------------------------------------- outgoing calls

    async def dial(
        self,
        host: str,
        number: str = "s",
        *,
        port: int = DEFAULT_PORT,
        context: str | None = None,
        username: str | None = None,
        secret: str | None = None,
        private_key: Any = None,
        caller_number: str | None = None,
        caller_name: str | None = None,
        formats: Iterable[int] | None = None,
        format: int | None = None,
        variables: dict[str, str] | None = None,
        autoanswer: bool = False,
        encryption: int | None = None,
        info: CallInfo | None = None,
        wait: str | None = "answered",
        timeout: float | None = 60.0,
    ) -> Call:
        """Place an outgoing call.

        Args:
            host: Remote host name or address.
            number: Extension to call (CALLED_NUMBER).
            port: Remote port.
            context: CALLED_CONTEXT.
            username: USERNAME sent with the call and used to authenticate.
            secret: Secret for MD5/plaintext authentication.
            private_key: RSA private key for RSA authentication.
            caller_number: Caller ID number.
            caller_name: Caller ID name.
            formats: Formats to offer, in preference order (default: the
                endpoint's preferences).
            format: Desired format (default: first of ``formats``).
            variables: Channel variables to send.
            autoanswer: Request auto-answer.
            encryption: Encryption methods to request (default: config).
            info: A fully specified :class:`CallInfo` (overrides the above).
            wait: ``"answered"`` (default), ``"accepted"`` or ``None`` (return
                immediately after sending NEW).
            timeout: Seconds to wait (``None`` = forever).

        Returns:
            The :class:`Call`.

        Raises:
            CallRejected: if the call is rejected or hung up while waiting.
            CallTimeout: if the peer stops responding.
            TimeoutError: if ``timeout`` expires (the call is hung up).
        """
        addr = await self.resolve(host, port)
        prefs = list(formats) if formats is not None else list(self.config.prefs or [])
        capability = 0
        for f in prefs:
            capability |= f
        if not capability:
            capability = self.config.capability or 0
        fmt = format or (prefs[0] if prefs else best_format(capability))
        if info is None:
            info = CallInfo(
                called_number=number, called_context=context, username=username,
                calling_number=caller_number, calling_name=caller_name,
                variables=dict(variables or {}), autoanswer=autoanswer,
                language=self.config.language,
                encryption=self.config.encryption if encryption is None else encryption,
            )
        if info.encryption and not self.encryption_supported():
            info.encryption = 0
        info.capability, info.format, info.codec_prefs = capability, fmt, prefs
        session = self.new_session(addr)
        creds = Credentials(username=username, secret=secret, private_key=private_key)
        call = Call(self, session, Direction.OUTGOING, info, capability=capability, prefs=prefs,
                    credentials=creds)
        self._calls.add(call)
        ies = info.to_ies(capability, fmt, prefs)
        info.ies = ies
        frame = session.send_command(IAXCommand.NEW, ies)
        session.remember_for_calltoken(frame)
        session.start_keepalives()
        if wait is None:
            return call
        try:
            if wait == "accepted":
                await call.wait_accepted(timeout)
            else:
                await call.wait_answered(timeout)
        except TimeoutError:
            await call.hangup(Cause.NO_ANSWER)
            raise
        except CallRejected:
            raise
        return call

    async def resolve(self, host: str, port: int = DEFAULT_PORT) -> Addr:
        """Resolve ``host`` to an address usable with this socket."""
        try:
            import ipaddress

            ipaddress.ip_address(host)
            return (host, port)
        except ValueError:
            pass
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        infos = await self.loop.getaddrinfo(host, port, family=family, type=socket.SOCK_DGRAM)
        if not infos:
            raise TransportError(f"cannot resolve {host}")
        sa = infos[0][4]
        return (sa[0], sa[1])

    # ----------------------------------------------------------------- poke

    async def poke(self, host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> float:
        """Qualify a peer with POKE/PONG and return the round-trip time in seconds.

        Raises:
            TimeoutError: if no PONG arrives within ``timeout``.
        """
        addr = await self.resolve(host, port)
        fut: asyncio.Future[float] = self.loop.create_future()
        start = self.clock()

        class _PokeOwner:
            def on_iax(self, session: Session, frame: FullFrame, ies: IEs) -> None:
                if frame.subclass == IAXCommand.PONG and not fut.done():
                    fut.set_result(self_ep.clock() - start)
                    session.send_ack(frame)
                    session.destroy()

            def on_media(self, *a: Any) -> None:
                pass

            def on_session_error(self, session: Session, reason: str) -> None:
                if not fut.done():
                    fut.set_exception(TimeoutError("no PONG"))

            def on_session_destroyed(self, session: Session) -> None:
                if not fut.done():
                    fut.set_exception(TimeoutError("no PONG"))

        self_ep = self
        session = self.new_session(addr, _PokeOwner())
        session._held = None
        session.send_command(IAXCommand.POKE, final=False)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            if not session.destroyed:
                session.destroy()

    # --------------------------------------------------------- registration

    def enable_registrar(self, registrar: Registrar | None = None) -> Registrar:
        """Act as a registrar for incoming REGREQ messages.

        Args:
            registrar: A configured :class:`~iax2.registration.Registrar`;
                a default one using :attr:`EndpointConfig.users` is created
                if omitted.
        """
        from .registration import Registrar

        self.registrar = registrar or Registrar(self, users=self.config.users)
        return self.registrar

    async def register(self, host: str, username: str, secret: str | None = None, *,
                       port: int = DEFAULT_PORT, refresh: int = 60, private_key: Any = None,
                       wait: bool = True, timeout: float | None = 10.0) -> Registration:
        """Register with a server and keep the registration refreshed.

        Args:
            host: Registrar host.
            username: Peer name to register as.
            secret: Secret for MD5/plaintext authentication.
            port: Registrar port.
            refresh: Requested refresh interval in seconds.
            private_key: RSA private key for RSA authentication.
            wait: Wait for the first registration result.
            timeout: Seconds to wait for the first result.

        Returns:
            The :class:`~iax2.registration.Registration`, already registered when
            ``wait`` is true.

        Raises:
            RegistrationError: if the first attempt is rejected (``wait=True``).
        """
        from .registration import Registration

        reg = Registration(self, host, username, secret, port=port, refresh=refresh,
                           private_key=private_key)
        self.registrations.append(reg)
        await reg.start()
        if wait:
            await reg.wait_registered(timeout)
        return reg

    # ----------------------------------------------- encryption/dialplan hooks

    def encryption_supported(self) -> bool:
        """``True`` if AES encryption is configured and ``cryptography`` is installed."""
        from . import crypto

        return crypto.available() and bool(self.config.encryption)

    def negotiated_encryption(self, offered: int) -> int:
        """Intersect peer-offered encryption methods with ours."""
        return offered & self.config.encryption

    def rotate_key(self, call: Call, ies: IEs) -> None:
        """Handle RTKEY (encryption key rotation)."""
        from . import crypto

        crypto.rotate_key(call.session, ies)

    def dialplan_request(self, call: Call, ies: IEs) -> None:
        """Answer a DPREQ using :attr:`dialplan` (or "nonexistent")."""
        number = ies.get(IE.CALLED_NUMBER, "")
        status = int(DPStatus.NONEXISTENT)
        if self.dialplan is not None:
            try:
                status = int(self.dialplan(call, number))
            except Exception:  # noqa: BLE001
                log.exception("dialplan hook failed")
        call.session.send_command(IAXCommand.DPREP, IEs().add(IE.CALLED_NUMBER, number)
                                  .add(IE.DPSTATUS, status).add(IE.REFRESH, 60))

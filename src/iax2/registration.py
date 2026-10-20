# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Registration: telling a server where to reach us (client) and tracking
where peers are (server/registrar).

Message flow (Asterisk 22)::

    client                                        registrar
      REGREQ (USERNAME, REFRESH, CALLTOKEN="") ->
                         <- CALLTOKEN (token)              [if tokens required]
      REGREQ (USERNAME, REFRESH, CALLTOKEN=token) ->
                         <- REGAUTH (AUTHMETHODS, CHALLENGE, USERNAME)
      REGREQ (USERNAME, REFRESH, MD5_RESULT|RSA_RESULT|PASSWORD) ->
                         <- REGACK (USERNAME, DATETIME, REFRESH, APPARENT_ADDR,
                                    [MSGCOUNT], [CALLING_NUMBER, CALLING_NAME])
                            or REGREJ (CAUSE, CAUSECODE)

Each attempt uses a fresh call number that is released after REGACK/REGREJ.
The client re-registers after 5/6 of the refresh interval granted by the
server; REGREL releases a registration.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .auth import Credentials, build_auth_reply, new_challenge, verify_auth_reply
from .constants import DEFAULT_PORT, IE, AuthMethod, Cause, IAXCommand
from .exceptions import AuthenticationError, RegistrationError
from .frames import FullFrame
from .ies import IEs
from .session import Session

if TYPE_CHECKING:  # pragma: no cover
    from .endpoint import Endpoint

__all__ = ["Registrar", "Registration", "RegistrationState", "RegistryEntry"]

log = logging.getLogger("iax2.registration")

Addr = tuple[str, int]


class RegistrationState(enum.Enum):
    """Client registration states (Asterisk ``REG_STATE_*``)."""

    UNREGISTERED = "Unregistered"
    REGSENT = "Request Sent"
    AUTHSENT = "Auth. Sent"
    REGISTERED = "Registered"
    REJECTED = "Rejected"
    TIMEOUT = "Timeout"
    NOAUTH = "No Authentication"


class Registration:
    """A client registration kept alive against one server.

    Created by :meth:`Endpoint.register() <iax2.endpoint.Endpoint.register>`.

    Attributes:
        state: Current :class:`RegistrationState`.
        refresh: Refresh interval granted by the server (seconds).
        apparent_addr: Our address as seen by the server (from REGACK).
        messages: ``(new, old)`` voicemail counts from MSGCOUNT, if sent.
        last_error: Cause text of the last rejection, if any.
        on_change: Optional callback ``(registration)`` on every state change.
    """

    def __init__(self, endpoint: Endpoint, host: str, username: str, secret: str | None,
                 *, port: int = DEFAULT_PORT, refresh: int = 60, private_key: Any = None) -> None:
        self.endpoint = endpoint
        self.host = host
        self.port = port
        self.username = username
        self.credentials = Credentials(username=username, secret=secret, private_key=private_key)
        self.requested_refresh = refresh
        self.refresh = refresh
        self.state = RegistrationState.UNREGISTERED
        self.apparent_addr: Addr | None = None
        self.messages: tuple[int, int] | None = None
        self.last_error: str | None = None
        self.on_change: Callable[[Registration], Any] | None = None
        self._addr: Addr | None = None
        self._session: Session | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._result: asyncio.Future[None] | None = None
        self._stopped = False
        self._releasing = False

    def __repr__(self) -> str:
        return f"<Registration {self.username}@{self.host}:{self.port} {self.state.value}>"

    @property
    def registered(self) -> bool:
        """``True`` while registered."""
        return self.state is RegistrationState.REGISTERED

    def _set_state(self, st: RegistrationState) -> None:
        if st is self.state:
            return
        self.state = st
        log.info("%r", self)
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001
                log.exception("registration on_change failed")

    async def start(self) -> None:
        """Send the first REGREQ."""
        self._addr = await self.endpoint.resolve(self.host, self.port)
        self._stopped = False
        self._send_regreq()

    async def wait_registered(self, timeout: float | None = 10.0) -> None:
        """Wait for the outcome of the current attempt.

        Raises:
            RegistrationError: if rejected or timed out.
        """
        if self.registered:
            return
        if self._result is None:
            self._result = self.endpoint.loop.create_future()
        try:
            await asyncio.wait_for(asyncio.shield(self._result), timeout)
        except TimeoutError:
            raise RegistrationError(f"no response from {self.host}") from None

    async def stop(self, unregister: bool = True) -> None:
        """Stop refreshing; optionally release the registration with REGREL."""
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if unregister and self.registered and self._addr is not None:
            self._releasing = True
            self._result = self.endpoint.loop.create_future()
            self._new_session()
            assert self._session is not None
            ies = IEs().add(IE.USERNAME, self.username).add(IE.CALLTOKEN, b"")
            f = self._session.send_command(IAXCommand.REGREL, ies)
            self._session.remember_for_calltoken(f)
            try:
                await asyncio.wait_for(asyncio.shield(self._result), 5)
            except (TimeoutError, RegistrationError):
                pass
        self._set_state(RegistrationState.UNREGISTERED)
        if self._session is not None and not self._session.destroyed:
            self._session.destroy()
        if self in self.endpoint.registrations:
            self.endpoint.registrations.remove(self)

    # -- protocol -----------------------------------------------------------

    def _new_session(self) -> None:
        if self._session is not None and not self._session.destroyed:
            self._session.destroy()
        assert self._addr is not None
        self._session = self.endpoint.new_session(self._addr, self)
        self._session.release_signaling()

    def _send_regreq(self) -> None:
        self._timer = None
        if self._stopped:
            return
        self._new_session()
        assert self._session is not None
        ies = IEs().add(IE.USERNAME, self.username).add(IE.REFRESH, self.requested_refresh)
        ies.add(IE.CALLTOKEN, b"")
        f = self._session.send_command(IAXCommand.REGREQ, ies)
        self._session.remember_for_calltoken(f)
        if self.state is not RegistrationState.REGISTERED:
            self._set_state(RegistrationState.REGSENT)
        # Asterisk schedules the next attempt now; REGACK reschedules it.
        self._schedule(max(1, 5 * self.refresh // 6))

    def _schedule(self, seconds: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if not self._stopped:
            self._timer = self.endpoint.loop.call_later(seconds, self._send_regreq)

    def _finish(self, exc: Exception | None = None) -> None:
        if self._result is not None and not self._result.done():
            if exc is None:
                self._result.set_result(None)
            else:
                self._result.set_exception(exc)
                self._result.exception()  # mark retrieved
        self._result = None

    # SessionOwner -----------------------------------------------------------

    def on_iax(self, session: Session, frame: FullFrame, ies: IEs) -> None:
        cmd = frame.subclass
        if cmd == IAXCommand.REGAUTH:
            try:
                reply, _ = build_auth_reply(self.credentials, ies.get(IE.AUTHMETHODS, 0),
                                            ies.get(IE.CHALLENGE))
            except AuthenticationError as exc:
                self.last_error = str(exc)
                self._set_state(RegistrationState.NOAUTH)
                session.send_command(IAXCommand.REJECT,
                                     IEs().add(IE.CAUSE, "No authority found")
                                     .add(IE.CAUSECODE, int(Cause.FACILITY_NOT_SUBSCRIBED)),
                                     final=True)
                self._finish(RegistrationError(str(exc)))
                return
            out = IEs().add(IE.USERNAME, self.username).add(IE.REFRESH, self.requested_refresh)
            for t, d in reply:
                out.add_raw(t, d)
            cmd_out = IAXCommand.REGREL if self._releasing else IAXCommand.REGREQ
            session.send_command(cmd_out, out)
            self._set_state(RegistrationState.AUTHSENT)
        elif cmd == IAXCommand.REGACK:
            session.send_ack(frame)
            if self._releasing:
                self._releasing = False
                self._finish()
                session.destroy()
                return
            self.refresh = ies.get(IE.REFRESH) or 60
            self.apparent_addr = ies.get(IE.APPARENT_ADDR)
            mc = ies.get(IE.MSGCOUNT)
            if mc is not None:
                self.messages = (mc & 0xFF, (mc >> 8) & 0xFF)
            self.last_error = None
            self._set_state(RegistrationState.REGISTERED)
            self._schedule(max(1, 5 * self.refresh // 6))
            self._finish()
            session.destroy()
        elif cmd in (IAXCommand.REGREJ, IAXCommand.REJECT):
            session.send_ack(frame)
            self.last_error = ies.get(IE.CAUSE) or "rejected"
            self._set_state(RegistrationState.REJECTED)
            self._finish(RegistrationError(f"registration rejected: {self.last_error}",
                                           ies.get(IE.CAUSE), ies.get(IE.CAUSECODE)))
            session.destroy()

    def on_media(self, *args: Any) -> None:
        pass

    def on_session_error(self, session: Session, reason: str) -> None:
        if session is self._session:
            self._set_state(RegistrationState.TIMEOUT)
            self.refresh = 60
            self._finish(RegistrationError(f"registration timed out: {reason}"))
            self._schedule(10)

    def on_session_destroyed(self, session: Session) -> None:
        pass


@dataclass
class RegistryEntry:
    """A peer registered with our :class:`Registrar`.

    Attributes:
        username: Peer name.
        addr: Where the peer can be reached.
        expires: ``time.time()`` when the registration lapses.
        refresh: Granted refresh interval.
    """

    username: str
    addr: Addr
    expires: float
    refresh: int

    @property
    def expired(self) -> bool:
        """``True`` once the registration has lapsed."""
        return time.time() > self.expires


class Registrar:
    """Accepts REGREQ from peers (server side).

    Args:
        endpoint: The endpoint.
        users: ``{username: Credentials}`` of peers allowed to register.
            Peers with neither secret nor public keys register without
            authentication.
        min_refresh: Minimum refresh interval granted (Asterisk ``minregexpire``).
        max_refresh: Maximum refresh interval granted (Asterisk ``maxregexpire``).

    Attributes:
        registry: ``{username: RegistryEntry}`` of current registrations.
        on_register: Optional callback ``(entry)`` after each registration;
            ``entry.addr`` is ``None``-like ``("", 0)`` on release.
        message_counts: Optional ``username -> (new, old)`` for MSGCOUNT.
        caller_ids: Optional ``{username: (number, name)}`` returned in REGACK.
    """

    def __init__(self, endpoint: Endpoint, users: dict[str, Credentials] | None = None,
                 *, min_refresh: int = 60, max_refresh: int = 60) -> None:
        self.endpoint = endpoint
        self.users = users if users is not None else {}
        self.min_refresh = min_refresh
        self.max_refresh = max(max_refresh, min_refresh)
        self.registry: dict[str, RegistryEntry] = {}
        self.on_register: Callable[[RegistryEntry], Any] | None = None
        self.message_counts: Callable[[str], tuple[int, int] | None] | None = None
        self.caller_ids: dict[str, tuple[str, str]] = {}
        self._challenges: dict[int, str] = {}

    def lookup(self, username: str) -> Addr | None:
        """Return the current address of ``username`` if registered and not expired."""
        e = self.registry.get(username)
        if e is None or e.expired:
            return None
        return e.addr

    def attach(self, session: Session) -> None:
        """Take ownership of a session created for an incoming REGREQ/REGREL."""
        session.owner = self
        session.release_signaling()

    # SessionOwner -----------------------------------------------------------

    def on_iax(self, session: Session, frame: FullFrame, ies: IEs) -> None:
        if frame.subclass not in (IAXCommand.REGREQ, IAXCommand.REGREL):
            return
        username = ies.get(IE.USERNAME) or ""
        creds = self.users.get(username)
        challenge = self._challenges.get(session.callno)
        has_auth = any(t in ies for t in (IE.MD5_RESULT, IE.RSA_RESULT, IE.PASSWORD))
        if creds is None:
            if challenge is None:
                # Don't reveal whether the peer exists: challenge anyway.
                self._regauth(session, username, AuthMethod.MD5)
            else:
                self._reject(session)
            return
        needs_auth = bool(creds.secret or creds.public_keys)
        if needs_auth:
            if challenge is None or not has_auth:
                self._regauth(session, username, creds.offered_methods())
                return
            if verify_auth_reply(creds, challenge, ies) is None:
                self._reject(session)
                return
        self._challenges.pop(session.callno, None)
        release = frame.subclass == IAXCommand.REGREL
        self._update(session, username, ies.get(IE.REFRESH, 0), release)

    def _regauth(self, session: Session, username: str, methods: int) -> None:
        challenge = new_challenge()
        self._challenges[session.callno] = challenge
        ies = IEs().add(IE.AUTHMETHODS, int(methods))
        if methods & (AuthMethod.MD5 | AuthMethod.RSA):
            ies.add(IE.CHALLENGE, challenge)
        ies.add(IE.USERNAME, username)
        session.send_command(IAXCommand.REGAUTH, ies)

    def _reject(self, session: Session) -> None:
        self._challenges.pop(session.callno, None)

        def send() -> None:
            if not session.destroyed:
                session.send_command(IAXCommand.REGREJ,
                                     IEs().add(IE.CAUSE, "Registration Refused")
                                     .add(IE.CAUSECODE, int(Cause.FACILITY_REJECTED)),
                                     final=True)

        if self.endpoint.config.delay_reject:
            session.call_later(1.0, send)
        else:
            send()

    def _update(self, session: Session, username: str, refresh: int, release: bool) -> None:
        if not refresh:
            refresh = self.min_refresh
        refresh = max(self.min_refresh, min(self.max_refresh, refresh))
        ies = IEs().add(IE.USERNAME, username).add(IE.DATETIME, None)
        if release:
            self.registry.pop(username, None)
            entry = RegistryEntry(username, ("", 0), 0.0, 0)
        else:
            entry = RegistryEntry(username, session.addr, time.time() + refresh + 10, refresh)
            self.registry[username] = entry
            ies.add(IE.REFRESH, refresh)
            ies.add(IE.APPARENT_ADDR, session.addr)
            if self.message_counts is not None:
                counts = self.message_counts(username)
                if counts is not None:
                    new, old = (min(255, c) for c in counts)
                    ies.add(IE.MSGCOUNT, (old << 8) | new)
            cid = self.caller_ids.get(username)
            if cid:
                ies.add(IE.CALLING_NUMBER, cid[0]).add(IE.CALLING_NAME, cid[1])
        session.send_command(IAXCommand.REGACK, ies, final=True)
        log.info("peer %s %s %s:%s", username, "unregistered" if release else "registered",
                 *session.addr)
        if self.on_register is not None:
            try:
                self.on_register(entry)
            except Exception:  # noqa: BLE001
                log.exception("on_register callback failed")

    def on_media(self, *args: Any) -> None:
        pass

    def on_session_error(self, session: Session, reason: str) -> None:
        self._challenges.pop(session.callno, None)

    def on_session_destroyed(self, session: Session) -> None:
        self._challenges.pop(session.callno, None)

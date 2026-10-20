# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Blocking (thread-based) wrapper around the asyncio API.

For scripts and applications that don't use asyncio. A private event loop
runs in a background daemon thread; every method here blocks until the
corresponding coroutine finishes.

Example::

    from iax2.sync import SyncEndpoint

    with SyncEndpoint(port=0) as ep:
        call = ep.dial("192.0.2.10", "100", username="me", secret="pw")
        call.play_pcm(open("hello.raw", "rb").read())
        call.hangup()

Callbacks (``on_event``/``on_audio``) set on a :class:`SyncCall` run in the
event-loop thread; keep them short and thread-safe.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Iterator
from typing import Any, TypeVar

from .call import Call, CallInfo, CallState, Direction
from .constants import DEFAULT_PORT, Cause
from .endpoint import Endpoint, EndpointConfig
from .events import AudioFrame, CallEvent
from .registration import Registration

__all__ = ["SyncCall", "SyncEndpoint"]

T = TypeVar("T")


class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._main, name="iax2-loop", daemon=True)
        self.thread.start()
        self._ready.wait()

    def _main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        if threading.current_thread() is self.thread:
            raise RuntimeError("blocking iax2.sync call made from the event-loop thread")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout)

    def call(self, fn: Callable[..., T], *args: Any, **kw: Any) -> T:
        async def wrapper() -> T:
            return fn(*args, **kw)

        return self.run(wrapper())

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if not self.loop.is_running():
            self.loop.close()


class SyncCall:
    """Blocking view of a :class:`~iax2.call.Call`.

    Read-only attributes (``state``, ``format``, ``info``, ``cause`` ...) are
    forwarded to the underlying call; the wrapped call is ``call``.
    """

    def __init__(self, call: Call, lt: _LoopThread):
        self.call = call
        self._lt = lt

    # -- forwarded attributes ----------------------------------------------

    @property
    def state(self) -> CallState:
        return self.call.state

    @property
    def direction(self) -> Direction:
        return self.call.direction

    @property
    def info(self) -> CallInfo:
        return self.call.info

    @property
    def format(self) -> int:
        return self.call.format

    @property
    def cause(self) -> str | None:
        return self.call.cause

    @property
    def cause_code(self) -> int | None:
        return self.call.cause_code

    @property
    def is_ended(self) -> bool:
        return self.call.is_ended

    @property
    def on_event(self) -> Callable[[CallEvent], Any] | None:
        return self.call.on_event

    @on_event.setter
    def on_event(self, cb: Callable[[CallEvent], Any] | None) -> None:
        self.call.on_event = cb

    @property
    def on_audio(self) -> Callable[[AudioFrame], Any] | None:
        return self.call.on_audio

    @on_audio.setter
    def on_audio(self, cb: Callable[[AudioFrame], Any] | None) -> None:
        self.call.on_audio = cb

    def __repr__(self) -> str:
        return f"<Sync{self.call!r}>"

    # -- control -------------------------------------------------------------

    def accept(self, format: int | None = None) -> int:
        """See :meth:`Call.accept`."""
        return self._lt.call(self.call.accept, format)

    def answer(self, format: int | None = None) -> None:
        """See :meth:`Call.answer`."""
        self._lt.run(self.call.answer(format))

    def reject(self, cause: str | None = None, cause_code: int = Cause.CALL_REJECTED) -> None:
        """See :meth:`Call.reject`."""
        self._lt.call(self.call.reject, cause, cause_code)

    def hangup(self, cause_code: int = Cause.NORMAL_CLEARING, cause: str | None = None) -> None:
        """See :meth:`Call.hangup`."""
        self._lt.run(self.call.hangup(cause_code, cause))

    def indicate(self, control: int, payload: bytes = b"") -> None:
        """See :meth:`Call.indicate`."""
        self._lt.call(self.call.indicate, control, payload)

    def ring(self) -> None:
        """See :meth:`Call.ring`."""
        self._lt.call(self.call.ring)

    def radio_key(self) -> None:
        """See :meth:`Call.radio_key`."""
        self._lt.call(self.call.radio_key)

    def radio_unkey(self) -> None:
        """See :meth:`Call.radio_unkey`."""
        self._lt.call(self.call.radio_unkey)

    # -- media -------------------------------------------------------------

    def send_audio(self, payload: bytes, **kw: Any) -> None:
        """See :meth:`Call.send_audio`."""
        self._lt.call(self.call.send_audio, payload, **kw)

    def send_pcm(self, pcm: bytes) -> None:
        """See :meth:`Call.send_pcm`."""
        self._lt.call(self.call.send_pcm, pcm)

    def play_pcm(self, pcm: bytes, frame_ms: int = 20) -> None:
        """Send PCM in real time; blocks until done. See :meth:`Call.play_pcm`."""
        self._lt.run(self.call.play_pcm(pcm, frame_ms))

    def send_text(self, text: str) -> None:
        """See :meth:`Call.send_text`."""
        self._lt.call(self.call.send_text, text)

    def send_dtmf(self, digits: str, duration: float = 0.1, gap: float = 0.05) -> None:
        """See :meth:`Call.send_dtmf`."""
        self._lt.run(self.call.send_dtmf(digits, duration, gap))

    def recv_audio(self, timeout: float | None = None) -> AudioFrame | None:
        """See :meth:`Call.recv_audio`."""
        return self._lt.run(self.call.recv_audio(timeout))

    def audio(self) -> Iterator[AudioFrame]:
        """Iterate over received audio until the call ends."""
        while True:
            f = self.recv_audio()
            if f is None:
                return
            yield f

    def next_event(self, timeout: float | None = None) -> CallEvent | None:
        """See :meth:`Call.next_event`."""
        return self._lt.run(self.call.next_event(timeout))

    def events(self) -> Iterator[CallEvent]:
        """Iterate over call events until the call ends."""
        from .events import HangupEvent

        while True:
            ev = self.next_event()
            if ev is None:
                return
            yield ev
            if isinstance(ev, HangupEvent):
                return

    def wait_answered(self, timeout: float | None = None) -> None:
        """See :meth:`Call.wait_answered`."""
        self._lt.run(self.call.wait_answered(timeout))

    def wait_ended(self, timeout: float | None = None) -> None:
        """See :meth:`Call.wait_ended`."""
        self._lt.run(self.call.wait_ended(timeout))


class SyncEndpoint:
    """Blocking counterpart of :class:`~iax2.endpoint.Endpoint`.

    Accepts the same arguments. The socket is bound on construction.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 config: EndpointConfig | None = None, **overrides: Any) -> None:
        self._lt = _LoopThread()
        try:
            self.endpoint: Endpoint = self._lt.run(self._create(host, port, config, overrides))
        except BaseException:
            self._lt.stop()
            raise

    @staticmethod
    async def _create(host: str, port: int, config: EndpointConfig | None,
                      overrides: dict[str, Any]) -> Endpoint:
        return await Endpoint(host, port, config, **overrides).start()

    @property
    def local_address(self) -> tuple[str, int]:
        """The bound ``(host, port)``."""
        return self.endpoint.local_address

    @property
    def port(self) -> int:
        """The bound UDP port."""
        return self.endpoint.port

    def dial(self, host: str, number: str = "s", **kw: Any) -> SyncCall:
        """Place a call; see :meth:`Endpoint.dial`."""
        return SyncCall(self._lt.run(self.endpoint.dial(host, number, **kw)), self._lt)

    def accept_call(self, timeout: float | None = None) -> SyncCall | None:
        """Wait for an incoming call; see :meth:`Endpoint.accept_call`."""
        call = self._lt.run(self.endpoint.accept_call(timeout))
        return SyncCall(call, self._lt) if call else None

    def register(self, host: str, username: str, secret: str | None = None,
                 **kw: Any) -> Registration:
        """Register with a server; see :meth:`Endpoint.register`."""
        return self._lt.run(self.endpoint.register(host, username, secret, **kw))

    def poke(self, host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> float:
        """Qualify a peer; see :meth:`Endpoint.poke`."""
        return self._lt.run(self.endpoint.poke(host, port, timeout))

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Run any coroutine on the endpoint's loop and return its result."""
        return self._lt.run(coro, timeout)

    def close(self) -> None:
        """Hang up everything, close the socket and stop the loop thread."""
        if self._lt.loop.is_closed():
            return
        try:
            self._lt.run(self.endpoint.close(), timeout=10)
        finally:
            self._lt.stop()

    def __enter__(self) -> SyncEndpoint:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""``iax2-tool`` — command-line utility for testing IAX2 peers.

Sub-commands::

    iax2-tool poke HOST[:PORT]                     qualify a peer (RTT)
    iax2-tool call HOST[:PORT] EXTEN [options]     place a call
    iax2-tool serve [options]                      answer calls (echo/tone/play)
    iax2-tool register HOST[:PORT] USER [SECRET]   register and stay registered
    iax2-tool decode HEX                           decode a datagram

Run ``iax2-tool COMMAND --help`` for options. ``-v``/``-vv`` enable protocol
logging (``-vv`` shows every frame on the wire).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from . import __version__, audio
from .auth import Credentials
from .call import Call
from .constants import DEFAULT_PORT
from .endpoint import Endpoint
from .events import DTMFEvent, HangupEvent, TextEvent
from .exceptions import IAX2Error
from .formats import format_by_name, format_names

__all__ = ["main"]


def _hostport(text: str) -> tuple[str, int]:
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else DEFAULT_PORT
    if text.count(":") == 1:
        host, port = text.split(":")
        return host, int(port)
    return text, DEFAULT_PORT


def _formats(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(format_by_name(n)) for n in text.split(",")]


def _endpoint(args: argparse.Namespace, **kw: object) -> Endpoint:
    host, port = _hostport(args.bind)
    return Endpoint(host, port, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------- poke

async def cmd_poke(args: argparse.Namespace) -> int:
    host, port = _hostport(args.peer)
    async with _endpoint(args) as ep:
        for i in range(args.count):
            try:
                rtt = await ep.poke(host, port, timeout=args.timeout)
                print(f"PONG from {host}:{port}: time={rtt * 1000:.1f} ms")
            except TimeoutError:
                print(f"no reply from {host}:{port}")
                if args.count == 1:
                    return 1
            if i + 1 < args.count:
                await asyncio.sleep(1)
    return 0


# --------------------------------------------------------------------- call

async def _print_events(call: Call) -> None:
    async for ev in call.events():
        if isinstance(ev, DTMFEvent):
            if not ev.begin:
                print(f"DTMF {ev.digit}")
        elif isinstance(ev, TextEvent):
            print(f"TEXT {ev.text}")
        elif isinstance(ev, HangupEvent):
            print(f"HANGUP cause={ev.cause_code} {ev.cause or ''} ({ev.reason})")
        else:
            logging.getLogger("iax2.cli").info("%s", type(ev).__name__)


async def cmd_call(args: argparse.Namespace) -> int:
    host, port = _hostport(args.peer)
    async with _endpoint(args) as ep:
        try:
            call = await ep.dial(host, args.exten, port=port, context=args.context,
                                 username=args.user, secret=args.secret,
                                 caller_number=args.callerid, caller_name=args.callername,
                                 formats=_formats(args.codecs), timeout=args.timeout)
        except IAX2Error as exc:
            print(f"call failed: {exc}", file=sys.stderr)
            return 1
        rate = call.codec.sample_rate if call.codec else 8000
        print(f"answered, format {format_names(call.format)}")
        printer = asyncio.create_task(_print_events(call))
        recorded: list[bytes] = []
        if args.record:
            call.on_audio = lambda f: recorded.append(f.pcm) if f.codec else None
        if args.dtmf:
            await asyncio.sleep(0.5)
            await call.send_dtmf(args.dtmf)
        if args.text:
            call.send_text(args.text)
        if args.play:
            await call.play_pcm(audio.read_wav(args.play, rate))
        elif args.tone:
            await call.play_pcm(audio.tone(args.tone, int(args.duration * 1000), rate))
        try:
            await asyncio.wait_for(call.wait_ended(), args.duration if not args.play else 0.5)
        except TimeoutError:
            await call.hangup()
        await printer
        if args.record:
            audio.write_wav(args.record, b"".join(recorded), rate)
            print(f"recorded {sum(map(len, recorded)) / 2 / rate:.1f} s to {args.record}")
    return 0


# -------------------------------------------------------------------- serve

async def _serve_call(call: Call, args: argparse.Namespace) -> None:
    log = logging.getLogger("iax2.cli")
    log.info("incoming call %s from %s <%s> to %s", call.callno, call.info.calling_name,
             call.info.calling_number, call.info.called_number)
    print(f"call from {call.info.calling_name or ''} <{call.info.calling_number or ''}> "
          f"to {call.info.called_number} ({format_names(call.format)})")
    if args.ring:
        call.ring()
        await asyncio.sleep(args.ring)
    await call.answer()
    rate = call.codec.sample_rate if call.codec else 8000
    printer = asyncio.create_task(_print_events(call))
    if args.mode == "echo":
        async for frame in call.audio():
            call.send_audio(frame.payload, format=frame.format)
    else:
        pcm = audio.read_wav(args.play, rate) if args.mode == "play" else \
            audio.tone(args.tone, 1000, rate)
        while not call.is_ended:
            await call.play_pcm(pcm)
            if args.mode == "play":
                await call.hangup()
    await printer


async def cmd_serve(args: argparse.Namespace) -> int:
    users = {}
    for spec in args.user or []:
        name, _, secret = spec.partition(":")
        users[name] = Credentials(username=name, secret=secret or None)
    if args.play:
        args.mode = "play"
    elif args.tone:
        args.mode = "tone"
    else:
        args.mode = "echo"
    ep = _endpoint(args, users=users, allow_guest=not users or args.guest,
                   require_calltoken=not args.no_calltoken)
    async with ep:
        if args.registrar:
            ep.enable_registrar()
        print(f"listening on {ep.host}:{ep.port} ({args.mode}); Ctrl-C to stop")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                pass
        ep.on_call = lambda c: _serve_call(c, args)
        await stop.wait()
    return 0


# ----------------------------------------------------------------- register

async def cmd_register(args: argparse.Namespace) -> int:
    host, port = _hostport(args.server)
    async with _endpoint(args) as ep:
        try:
            reg = await ep.register(host, args.username, args.secret, port=port,
                                    refresh=args.refresh)
        except IAX2Error as exc:
            print(f"registration failed: {exc}", file=sys.stderr)
            return 1
        print(f"registered; apparent address {reg.apparent_addr}, refresh {reg.refresh} s")
        if args.once:
            await reg.stop()
            return 0
        reg.on_change = lambda r: print(f"registration {r.state.value}")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover
                pass
        await stop.wait()
    return 0


# ------------------------------------------------------------------- decode

def cmd_decode(args: argparse.Namespace) -> int:
    from .frames import FullFrame, parse_datagram

    data = bytes.fromhex("".join(args.hex).replace(":", ""))
    frame = parse_datagram(data)
    print(frame.describe() if isinstance(frame, FullFrame) else frame)
    return 0


# --------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iax2-tool", description="IAX2 test utility")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="-v: protocol log, -vv: also every frame")
    p.add_argument("-b", "--bind", default="0.0.0.0:0",
                   help="local address[:port] (default 0.0.0.0:0 = any free port)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("poke", help="qualify a peer with POKE")
    s.add_argument("peer", help="HOST[:PORT]")
    s.add_argument("-c", "--count", type=int, default=1)
    s.add_argument("-t", "--timeout", type=float, default=2.0)

    s = sub.add_parser("call", help="place a call")
    s.add_argument("peer", help="HOST[:PORT]")
    s.add_argument("exten", help="number to call")
    s.add_argument("-u", "--user")
    s.add_argument("-s", "--secret")
    s.add_argument("-C", "--context")
    s.add_argument("--callerid", help="caller ID number")
    s.add_argument("--callername", help="caller ID name")
    s.add_argument("--codecs", help="comma-separated formats in order, e.g. ulaw,alaw")
    s.add_argument("--play", metavar="WAV", help="play a WAV file")
    s.add_argument("--tone", type=float, metavar="HZ", help="play a tone")
    s.add_argument("--record", metavar="WAV", help="record received audio")
    s.add_argument("--dtmf", help="digits to send after answer")
    s.add_argument("--text", help="text message to send after answer")
    s.add_argument("-d", "--duration", type=float, default=10.0,
                   help="seconds to stay connected (default 10)")
    s.add_argument("-t", "--timeout", type=float, default=30.0, help="answer timeout")

    s = sub.add_parser("serve", help="answer incoming calls")
    s.add_argument("-u", "--user", action="append", metavar="NAME[:SECRET]",
                   help="accepted user (repeatable); default: anyone")
    s.add_argument("--guest", action="store_true", help="also accept unknown callers")
    s.add_argument("--play", metavar="WAV", help="play a WAV file to callers")
    s.add_argument("--tone", type=float, metavar="HZ", help="play a tone to callers")
    s.add_argument("--ring", type=float, default=0, metavar="SEC", help="ring before answer")
    s.add_argument("--registrar", action="store_true", help="accept registrations from users")
    s.add_argument("--no-calltoken", action="store_true",
                   help="don't require call tokens (old clients)")

    s = sub.add_parser("register", help="register with a server")
    s.add_argument("server", help="HOST[:PORT]")
    s.add_argument("username")
    s.add_argument("secret", nargs="?")
    s.add_argument("--refresh", type=int, default=60)
    s.add_argument("--once", action="store_true", help="register, then unregister and exit")

    s = sub.add_parser("decode", help="decode a datagram given in hex")
    s.add_argument("hex", nargs="+")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``iax2-tool``."""
    args = build_parser().parse_args(argv)
    level = logging.WARNING if args.verbose == 0 else logging.INFO if args.verbose == 1 \
        else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(name)s: %(message)s")
    if args.verbose < 2:
        logging.getLogger("iax2.wire").setLevel(logging.WARNING)
    if args.command == "decode":
        return cmd_decode(args)
    handler = {"poke": cmd_poke, "call": cmd_call, "serve": cmd_serve,
               "register": cmd_register}[args.command]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

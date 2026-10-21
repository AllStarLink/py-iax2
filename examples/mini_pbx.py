#!/usr/bin/env python3
"""A tiny PBX: a registrar that connects registered users to each other.

Users register with this server, then call each other by username. The
server bridges the two calls by relaying audio (and DTMF/text), and with
``--transfer`` asks the two endpoints to connect directly via IAX2 native
transfer once the call is up.

Usage:
    python3 mini_pbx.py --user alice:pw1 --user bob:pw2 [--port 4569] [--transfer]

Then, e.g. with two terminals:
    iax2-tool -b :5001 register 127.0.0.1 alice pw1      # keeps alice registered
    iax2-tool serve ...                                  # (or a softphone as bob)
    iax2-tool call 127.0.0.1 bob -u alice -s pw1 --tone 440
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import iax2
from iax2.events import DTMFEvent, TextEvent

log = logging.getLogger("mini_pbx")


async def relay(a: iax2.Call, b: iax2.Call) -> None:
    """Copy audio, DTMF and text from ``a`` to ``b`` until either ends."""

    def on_event(ev: iax2.CallEvent) -> None:
        if b.is_ended:
            return
        if isinstance(ev, DTMFEvent):
            (b.send_dtmf_begin if ev.begin else b.send_dtmf_end)(ev.digit)
        elif isinstance(ev, TextEvent):
            b.send_text(ev.text)

    a.on_event = on_event
    a.on_audio = lambda f: b.send_audio(f.payload, format=f.format)
    await a.wait_ended()
    if not b.is_ended:
        await b.hangup(a.cause_code or iax2.Cause.NORMAL_CLEARING)


async def handle(
    ep: iax2.Endpoint,
    registrar: iax2.Registrar,
    creds: dict[str, str],
    caller: iax2.Call,
    transfer: bool,
) -> None:
    target = caller.info.called_number or ""
    addr = registrar.lookup(target)
    if addr is None:
        log.info("%s is not registered", target)
        caller.reject("Not registered", iax2.Cause.SUBSCRIBER_ABSENT)
        return
    caller.ring()
    try:
        # Call the target using its own credentials (it authenticates us as itself).
        callee = await ep.dial(
            addr[0],
            target,
            port=addr[1],
            username=target,
            secret=creds.get(target),
            caller_number=caller.info.calling_number or caller.info.username,
            caller_name=caller.info.calling_name,
            formats=[caller.format],
            timeout=30,
        )
    except iax2.IAX2Error as exc:
        caller.reject(str(exc), getattr(exc, "cause_code", None) or iax2.Cause.NO_ANSWER)
        return
    await caller.answer()
    log.info("bridged %s -> %s", caller.info.username, target)
    if transfer:
        await asyncio.sleep(1)
        ep.transfers.start_transfer(caller, callee)
        return
    await asyncio.gather(relay(caller, callee), relay(callee, caller))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=iax2.DEFAULT_PORT)
    ap.add_argument("--user", action="append", required=True, metavar="NAME:SECRET")
    ap.add_argument("--transfer", action="store_true", help="use native transfer")
    args = ap.parse_args()
    creds = dict(spec.split(":", 1) for spec in args.user)
    users = {n: iax2.Credentials(username=n, secret=s) for n, s in creds.items()}

    async with iax2.Endpoint(args.host, args.port, users=users, allow_guest=False) as ep:
        registrar = ep.enable_registrar()
        registrar.on_register = lambda e: log.info("%s at %s", e.username, e.addr)
        log.info("mini PBX on %s:%s", ep.host, ep.port)
        tasks: set[asyncio.Task[None]] = set()  # keep strong refs until each call ends
        async for call in ep.incoming():
            task = asyncio.create_task(handle(ep, registrar, creds, call, args.transfer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main())

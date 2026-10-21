#!/usr/bin/env python3
"""Echo server: answers every IAX2 call and sends the caller's audio back.

Usage:
    python3 echo_server.py [--port 4569] [--user alice:secret ...]

Try it with:
    iax2-tool call 127.0.0.1 echo --tone 440 -d 3 --record echoed.wav
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import iax2


async def handle(call: iax2.Call) -> None:
    print(f"call {call.callno} from {call.info.calling_number!r} to {call.info.called_number!r}")
    await call.answer()
    # Every received frame goes straight back out in the same format.
    async for frame in call.audio():
        call.send_audio(frame.payload, format=frame.format)
    print(f"call {call.callno} ended: cause {call.cause_code}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=iax2.DEFAULT_PORT)
    ap.add_argument("--user", action="append", default=[], metavar="NAME:SECRET")
    args = ap.parse_args()
    users = {}
    for spec in args.user:
        name, _, secret = spec.partition(":")
        users[name] = iax2.Credentials(username=name, secret=secret)

    async with iax2.Endpoint(args.host, args.port, users=users, allow_guest=not users) as ep:
        print(f"echo server on {ep.host}:{ep.port}")
        tasks: set[asyncio.Task[None]] = set()  # keep strong refs until each call ends
        async for call in ep.incoming():
            task = asyncio.create_task(handle(call))
            tasks.add(task)
            task.add_done_callback(tasks.discard)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

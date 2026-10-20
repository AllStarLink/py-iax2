#!/usr/bin/env python3
"""Dial a number, play a WAV file (or a tone), record what comes back.

Usage:
    python3 dial_play_record.py HOST EXTEN --user U --secret S [--context C]
                                [--play in.wav] [--record out.wav]

Example against an Asterisk echo test:
    python3 dial_play_record.py pbx.example.org 600 --user me --secret pw \\
        --record echo.wav
"""

from __future__ import annotations

import argparse
import asyncio

import iax2
from iax2 import audio
from iax2.events import DTMFEvent, Ringing, TextEvent


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("exten")
    ap.add_argument("--port", type=int, default=iax2.DEFAULT_PORT)
    ap.add_argument("--user")
    ap.add_argument("--secret")
    ap.add_argument("--context")
    ap.add_argument("--play", help="WAV file to play (default: 2 s of 440 Hz)")
    ap.add_argument("--record", help="WAV file to write received audio to")
    args = ap.parse_args()

    async with iax2.Endpoint(port=0) as ep:
        # dial() returns once the call is answered (raises CallRejected otherwise)
        call = await ep.dial(args.host, args.exten, port=args.port, context=args.context,
                             username=args.user, secret=args.secret,
                             caller_number="1234", caller_name="py-iax2 example")
        print("answered with", iax2.formats.format_names(call.format))

        def on_event(ev: iax2.CallEvent) -> None:
            if isinstance(ev, Ringing):
                print("ringing")
            elif isinstance(ev, DTMFEvent) and not ev.begin:
                print("DTMF", ev.digit)
            elif isinstance(ev, TextEvent):
                print("text:", ev.text)

        call.on_event = on_event
        received: list[bytes] = []
        call.on_audio = lambda frame: received.append(frame.pcm)

        rate = call.codec.sample_rate if call.codec else 8000
        pcm = audio.read_wav(args.play, rate) if args.play else audio.tone(440, 2000, rate)
        await call.play_pcm(pcm)          # paced in real time
        await asyncio.sleep(0.5)          # let the last echoes arrive
        await call.hangup()

        if args.record:
            audio.write_wav(args.record, b"".join(received), rate)
            print(f"wrote {len(received)} frames to {args.record}")


if __name__ == "__main__":
    asyncio.run(main())

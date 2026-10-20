#!/usr/bin/env python3
"""Connect to an AllStarLink (app_rpt) node like the iaxRpt PC client.

The node must have an IAX2 user built from ASL3's ``[iaxrpt]`` template, e.g.
in ``/etc/asterisk/custom/iax/iaxrpt-users.conf``::

    [myclient](iaxrpt)
    secret = s3cret

The client keys the node's transmitter with CONTROL RADIO_KEY, sends audio,
unkeys with RADIO_UNKEY, and records what the node sends back. app_rpt
also sends TEXT frames (e.g. ``"L ..."`` link lists), which are printed.

Usage:
    python3 allstar_client.py HOST NODE --user myclient --secret s3cret
                              [--play announce.wav] [--listen 10] [--record rx.wav]
"""

from __future__ import annotations

import argparse
import asyncio

import iax2
from iax2 import audio
from iax2.constants import ControlType
from iax2.events import ControlEvent, TextEvent


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("node")
    ap.add_argument("--port", type=int, default=iax2.DEFAULT_PORT)
    ap.add_argument("--user", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--callsign", default="PYIAX", help="sent as caller ID")
    ap.add_argument("--play", help="WAV to transmit (default: a short 800 Hz tone)")
    ap.add_argument("--listen", type=float, default=10.0, help="seconds to listen")
    ap.add_argument("--record", help="WAV file for received audio")
    args = ap.parse_args()

    async with iax2.Endpoint(port=0) as ep:
        call = await ep.dial(args.host, args.node, port=args.port, username=args.user,
                             secret=args.secret, caller_number=args.callsign, timeout=20)
        print(f"connected to node {args.node}")

        def on_event(ev: iax2.CallEvent) -> None:
            if isinstance(ev, TextEvent):
                print("node says:", ev.text)
            elif isinstance(ev, ControlEvent) and ev.control == ControlType.RADIO_KEY:
                print("node transmitting")
            elif isinstance(ev, ControlEvent) and ev.control == ControlType.RADIO_UNKEY:
                print("node idle")

        call.on_event = on_event
        received: list[bytes] = []
        call.on_audio = lambda f: received.append(f.pcm)

        pcm = audio.read_wav(args.play) if args.play else audio.tone(800, 1000)
        call.radio_key()                  # PTT down
        await call.play_pcm(pcm)
        call.radio_unkey()                # PTT up

        await asyncio.sleep(args.listen)
        await call.hangup()
        if args.record:
            audio.write_wav(args.record, b"".join(received))
            print(f"recorded {len(received) * 0.02:.1f} s to {args.record}")


if __name__ == "__main__":
    asyncio.run(main())

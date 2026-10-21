#!/usr/bin/env python3
"""The blocking API: no asyncio required.

Usage:
    python3 sync_client.py HOST EXTEN [--user U --secret S --context C]
"""

from __future__ import annotations

import argparse

from iax2 import audio
from iax2.sync import SyncEndpoint


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("exten")
    ap.add_argument("--user")
    ap.add_argument("--secret")
    ap.add_argument("--context")
    args = ap.parse_args()

    with SyncEndpoint(port=0) as ep:
        print(f"RTT to {args.host}: {ep.poke(args.host) * 1000:.1f} ms")
        call = ep.dial(
            args.host, args.exten, context=args.context, username=args.user, secret=args.secret
        )
        call.send_dtmf("123")
        call.play_pcm(audio.tone(1000, 1000))
        frame = call.recv_audio(timeout=1)
        if frame is not None:
            print(f"received {len(frame.payload)} bytes, level {audio.rms(frame.pcm):.0f}")
        call.hangup()


if __name__ == "__main__":
    main()

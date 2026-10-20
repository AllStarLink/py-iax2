# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""A simple adaptive jitter buffer for received audio.

IAX2 carries no sequence numbers on voice, only timestamps, and the network
reorders, delays and drops frames. :class:`JitterBuffer` turns the received
stream back into an evenly paced one:

* frames are ordered by timestamp and released ``delay`` ms after the time
  they *would* have arrived on a perfect network;
* ``delay`` adapts to measured jitter (RFC 3550 estimator), between
  ``min_delay`` and ``max_delay``; it only grows immediately and shrinks
  slowly, one frame at a time;
* a frame that is missing when its turn comes is reported as lost
  (``None``) so the application can conceal it; one that arrives after its
  turn is discarded as late.

It is clock-agnostic: pass times in milliseconds. :meth:`Call.playout()
<iax2.call.Call.playout>` drives one from the event loop.

Example::

    jb = JitterBuffer(frame_ms=20)
    jb.put(frame, now_ms)
    for item in jb.get(now_ms):   # AudioFrame, or None for a lost frame
        ...
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

__all__ = ["JitterBuffer", "JitterStats"]


class _HasTimestamp(Protocol):
    timestamp: int


F = TypeVar("F", bound=_HasTimestamp)


@dataclass
class JitterStats:
    """Counters kept by a :class:`JitterBuffer` (also used for receiver reports).

    Attributes:
        received: Frames put into the buffer.
        played: Frames released in order.
        lost: Gaps reported (no frame at its turn).
        late: Frames discarded because they arrived after their turn.
        duplicates: Frames discarded as duplicates.
        out_of_order: Frames that arrived with a timestamp lower than a
            previously received one.
        jitter: Current jitter estimate (ms).
        delay: Current playout delay (ms).
    """

    received: int = 0
    played: int = 0
    lost: int = 0
    late: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    jitter: float = 0.0
    delay: float = 0.0


class JitterBuffer(Generic[F]):
    """Adaptive playout buffer.

    Args:
        frame_ms: Duration of one frame in ms.
        min_delay: Lower bound of the playout delay (ms).
        max_delay: Upper bound of the playout delay (ms).
        jitter_factor: Delay target = ``frame_ms + jitter_factor * jitter``.
        max_frames: Hard cap on buffered frames (oldest dropped beyond it).
    """

    def __init__(self, frame_ms: int = 20, min_delay: int = 40, max_delay: int = 500,
                 jitter_factor: float = 3.0, max_frames: int = 200) -> None:
        self.frame_ms = frame_ms
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.jitter_factor = jitter_factor
        self.max_frames = max_frames
        self.stats = JitterStats(delay=float(min_delay))
        self._frames: list[F] = []
        self._keys: list[int] = []
        self._base: float | None = None  # arrival_ms - ts of the reference frame
        self._next_ts: int | None = None  # timestamp of the next frame to play
        self._last_transit: float | None = None
        self._highest_ts: int | None = None

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def delay(self) -> float:
        """Current playout delay (ms)."""
        return self.stats.delay

    def reset(self) -> None:
        """Forget all state (e.g. after a transfer or long silence)."""
        self.__init__(self.frame_ms, self.min_delay, self.max_delay,  # type: ignore[misc]
                      self.jitter_factor, self.max_frames)

    def put(self, frame: F, arrival_ms: float) -> None:
        """Add a received frame."""
        st = self.stats
        ts = frame.timestamp
        st.received += 1
        transit = arrival_ms - ts
        if self._last_transit is not None:
            d = abs(transit - self._last_transit)
            st.jitter += (d - st.jitter) / 16
        self._last_transit = transit
        if self._base is None:
            self._base = transit
        elif transit < self._base:
            self._base = transit  # fastest path seen so far
        if self._highest_ts is not None and ts < self._highest_ts:
            st.out_of_order += 1
        self._highest_ts = ts if self._highest_ts is None else max(ts, self._highest_ts)
        if self._next_ts is not None and ts < self._next_ts:
            st.late += 1
            return
        i = bisect.bisect_left(self._keys, ts)
        if i < len(self._keys) and self._keys[i] == ts:
            st.duplicates += 1
            return
        self._keys.insert(i, ts)
        self._frames.insert(i, frame)
        if len(self._frames) > self.max_frames:
            self._keys.pop(0)
            self._frames.pop(0)
        target = min(self.max_delay, max(self.min_delay, self.frame_ms + self.jitter_factor * st.jitter))
        if target > st.delay:
            st.delay = target  # grow immediately

    def get(self, now_ms: float) -> list[F | None]:
        """Return everything due for playout at ``now_ms``, in order.

        Lost frames are represented by ``None``. Returns an empty list if
        nothing is due.
        """
        out: list[F | None] = []
        st = self.stats
        if self._base is None:
            return out
        if self._next_ts is None:
            if not self._frames:
                return out
            self._next_ts = self._keys[0]
        while True:
            due = self._next_ts + self._base + st.delay
            if now_ms < due:
                break
            if self._keys and self._keys[0] == self._next_ts:
                self._keys.pop(0)
                out.append(self._frames.pop(0))
                st.played += 1
            elif self._keys and self._keys[0] < self._next_ts:
                self._keys.pop(0)
                self._frames.pop(0)
                st.late += 1
                continue
            elif not self._frames and now_ms - due > self.max_delay:
                # Long silence (e.g. sender stopped): re-anchor on the next frame.
                self._next_ts = None
                break
            else:
                out.append(None)
                st.lost += 1
            self._next_ts += self.frame_ms
            # Shrink slowly when jitter has fallen: skip one frame's worth of delay.
            target = max(self.min_delay, self.frame_ms + self.jitter_factor * st.jitter)
            if st.delay - target >= self.frame_ms and len(self._frames) > 1:
                st.delay -= self.frame_ms
        return out

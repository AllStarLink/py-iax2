# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Small PCM helpers: WAV files, tones, resampling and level measurement.

All PCM here is mono signed 16-bit little-endian ("s16le"), the format used
throughout :mod:`iax2`. Only the standard library is used.
"""

from __future__ import annotations

import array
import math
import os
import sys
import wave

__all__ = ["read_wav", "resample", "rms", "silence", "tone", "write_wav"]


def _samples(pcm: bytes) -> array.array[int]:
    a = array.array("h", pcm[: len(pcm) & ~1])
    if sys.byteorder == "big":  # pragma: no cover
        a.byteswap()
    return a


def _bytes(a: array.array[int]) -> bytes:
    if sys.byteorder == "big":  # pragma: no cover
        a = array.array("h", a)
        a.byteswap()
    return a.tobytes()


def tone(freq: float, ms: int, rate: int = 8000, amplitude: float = 0.3) -> bytes:
    """Generate a sine tone.

    Args:
        freq: Frequency in Hz.
        ms: Duration in milliseconds.
        rate: Sample rate.
        amplitude: Peak level, 0.0–1.0 of full scale.
    """
    n = rate * ms // 1000
    peak = int(32767 * max(0.0, min(1.0, amplitude)))
    return _bytes(
        array.array("h", (int(peak * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    )


def silence(ms: int, rate: int = 8000) -> bytes:
    """Return ``ms`` milliseconds of silence."""
    return bytes(2 * (rate * ms // 1000))


def rms(pcm: bytes) -> float:
    """Root-mean-square level of ``pcm`` (0–32768)."""
    a = _samples(pcm)
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a))


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample by linear interpolation (adequate for voice; not hi-fi)."""
    if src_rate == dst_rate:
        return pcm
    a = _samples(pcm)
    if not a:
        return b""
    n = int(len(a) * dst_rate / src_rate)
    out = array.array("h", bytes(2 * n))
    step = src_rate / dst_rate
    last = len(a) - 1
    for i in range(n):
        pos = i * step
        j = int(pos)
        frac = pos - j
        x = a[j] if j >= last else a[j] + (a[j + 1] - a[j]) * frac
        out[i] = int(x)
    return _bytes(out)


def read_wav(path: str | os.PathLike[str], rate: int = 8000) -> bytes:
    """Read a WAV file as mono s16le PCM at ``rate`` Hz.

    Stereo files are mixed down, 8-bit files are expanded and the sample
    rate is converted if needed.

    Raises:
        ValueError: for unsupported sample widths.
    """
    with wave.open(os.fspath(path), "rb") as w:
        channels, width, src_rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        raw = bytes(b"".join(((b - 128) << 8 & 0xFFFF).to_bytes(2, "little") for b in raw))
    elif width != 2:
        raise ValueError(f"unsupported WAV sample width {width * 8} bits")
    if channels > 1:
        a = _samples(raw)
        mono = array.array(
            "h", (sum(a[i : i + channels]) // channels for i in range(0, len(a), channels))
        )
        raw = _bytes(mono)
    return resample(raw, src_rate, rate)


def write_wav(path: str | os.PathLike[str], pcm: bytes, rate: int = 8000) -> None:
    """Write mono s16le PCM to a WAV file."""
    with wave.open(os.fspath(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)

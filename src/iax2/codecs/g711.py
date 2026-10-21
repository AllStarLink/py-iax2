# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""G.711 μ-law and A-law, implemented with lookup tables (no ``audioop``)."""

from __future__ import annotations

import struct
import sys

from ..formats import Format
from .base import Codec

__all__ = [
    "ALawCodec",
    "ULawCodec",
    "alaw_to_linear",
    "linear_to_alaw",
    "linear_to_ulaw",
    "ulaw_to_linear",
]

_BIAS = 0x84
_CLIP = 32635


def linear_to_ulaw(sample: int) -> int:
    """Encode one signed 16-bit sample as μ-law (ITU-T G.711, Sun reference)."""
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not sample & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def ulaw_to_linear(u: int) -> int:
    """Decode one μ-law byte into a signed 16-bit sample."""
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + _BIAS
    t <<= (u & 0x70) >> 4
    return _BIAS - t if u & 0x80 else t - _BIAS


def linear_to_alaw(sample: int) -> int:
    """Encode one signed 16-bit sample as A-law (ITU-T G.711)."""
    if sample >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        sample = -sample - 1
    sample = min(sample, 0x7FFF) >> 3
    seg = 0
    for end in (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF):
        if sample <= end:
            break
        seg += 1
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << 4
    aval |= (sample >> 1) & 0x0F if seg < 2 else (sample >> seg) & 0x0F
    return aval ^ mask


def alaw_to_linear(a: int) -> int:
    """Decode one A-law byte into a signed 16-bit sample."""
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


def _decode_table(fn: object) -> list[bytes]:
    return [struct.pack("<h", fn(i)) for i in range(256)]  # type: ignore[operator]


def _encode_table(fn: object) -> bytes:
    # index = sample as unsigned 16-bit
    return bytes(fn(i - 65536 if i >= 32768 else i) for i in range(65536))  # type: ignore[operator]


_ULAW_DEC = _decode_table(ulaw_to_linear)
_ALAW_DEC = _decode_table(alaw_to_linear)
_ULAW_ENC = _encode_table(linear_to_ulaw)
_ALAW_ENC = _encode_table(linear_to_alaw)
_BIG = sys.byteorder == "big"


def _u16(pcm: bytes) -> memoryview:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if _BIG:  # pragma: no cover - big-endian hosts
        import array

        arr = array.array("H", pcm)
        arr.byteswap()
        return memoryview(arr)
    return memoryview(pcm).cast("H")


class ULawCodec(Codec):
    """G.711 μ-law (8 kHz, 1 byte per sample)."""

    format = Format.ULAW
    sample_rate = 8000

    def encode(self, pcm: bytes) -> bytes:
        return bytes(map(_ULAW_ENC.__getitem__, _u16(pcm)))

    def decode(self, payload: bytes) -> bytes:
        return b"".join(map(_ULAW_DEC.__getitem__, payload))

    def samples(self, payload: bytes) -> int:
        return len(payload)


class ALawCodec(Codec):
    """G.711 A-law (8 kHz, 1 byte per sample)."""

    format = Format.ALAW
    sample_rate = 8000

    def encode(self, pcm: bytes) -> bytes:
        return bytes(map(_ALAW_ENC.__getitem__, _u16(pcm)))

    def decode(self, payload: bytes) -> bytes:
        return b"".join(map(_ALAW_DEC.__getitem__, payload))

    def samples(self, payload: bytes) -> int:
        return len(payload)

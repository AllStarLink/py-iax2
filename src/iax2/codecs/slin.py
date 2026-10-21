# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Jason D. McCormick
"""Signed linear PCM codecs.

On the IAX2 wire, ``slin``/``slin16`` samples are **big-endian** (Asterisk
byte-swaps them from network order on receipt), so these codecs swap bytes
to and from the library's little-endian PCM.
"""

from __future__ import annotations

import array
import sys

from ..formats import Format
from .base import Codec

__all__ = ["SLin16Codec", "SLinCodec"]


def _swap(data: bytes) -> bytes:
    if len(data) % 2:
        data = data[:-1]
    arr = array.array("h", data)
    arr.byteswap()
    return arr.tobytes()


class SLinCodec(Codec):
    """16-bit signed linear, 8 kHz."""

    format = Format.SLIN
    sample_rate = 8000

    def encode(self, pcm: bytes) -> bytes:
        return _swap(pcm) if sys.byteorder == "little" else bytes(pcm)

    def decode(self, payload: bytes) -> bytes:
        return _swap(payload) if sys.byteorder == "little" else bytes(payload)

    def samples(self, payload: bytes) -> int:
        return len(payload) // 2


class SLin16Codec(SLinCodec):
    """16-bit signed linear, 16 kHz."""

    format = Format.SLIN16
    sample_rate = 16000

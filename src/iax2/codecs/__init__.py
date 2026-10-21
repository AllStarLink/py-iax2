# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Jason D. McCormick
"""Audio codecs.

A :class:`Codec` converts between 16-bit signed little-endian linear PCM
("s16le", the format of WAV files) and a payload format carried in IAX2
voice frames. The built-in codecs are pure Python:

=========  ===========================  =========
Format     Class                        Rate (Hz)
=========  ===========================  =========
ULAW       :class:`~.g711.ULawCodec`    8000
ALAW       :class:`~.g711.ALawCodec`    8000
SLIN       :class:`~.slin.SLinCodec`    8000
SLIN16     :class:`~.slin.SLin16Codec`  16000
=========  ===========================  =========

Additional codecs (GSM, G.722, Opus, ...) can be added by subclassing
:class:`Codec` and calling :func:`register_codec`. Codecs are looked up by
format bit with :func:`get_codec`; calls whose format has no registered codec
still work, but only raw payload access is available.

Example::

    from iax2.codecs import Codec, register_codec
    from iax2.formats import Format

    class MyGSM(Codec):
        format = Format.GSM
        sample_rate = 8000
        def encode(self, pcm): ...
        def decode(self, payload): ...

    register_codec(MyGSM)
"""

from __future__ import annotations

from .base import Codec, available_formats, get_codec, register_codec, unregister_codec
from .g711 import ALawCodec, ULawCodec
from .slin import SLin16Codec, SLinCodec

for _c in (ULawCodec, ALawCodec, SLinCodec, SLin16Codec):
    register_codec(_c)

__all__ = [
    "ALawCodec",
    "Codec",
    "SLin16Codec",
    "SLinCodec",
    "ULawCodec",
    "available_formats",
    "get_codec",
    "register_codec",
    "unregister_codec",
]

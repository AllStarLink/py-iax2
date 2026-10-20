# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Codec base class and registry."""

from __future__ import annotations

from typing import ClassVar

from ..exceptions import CodecError
from ..formats import format_info

__all__ = ["Codec", "available_formats", "get_codec", "register_codec", "unregister_codec"]


class Codec:
    """Base class for audio codecs.

    Subclasses set :attr:`format` and :attr:`sample_rate` and implement
    :meth:`encode` and :meth:`decode`. A new instance is created per call
    direction, so codecs may keep state (e.g. ADPCM predictors).

    PCM is always mono, signed 16-bit little-endian at :attr:`sample_rate`.
    """

    #: Format bit this codec implements (a :class:`~iax2.formats.Format`).
    format: ClassVar[int] = 0
    #: Sample rate of the PCM side in Hz.
    sample_rate: ClassVar[int] = 8000

    def encode(self, pcm: bytes) -> bytes:
        """Encode s16le PCM into a payload."""
        raise NotImplementedError

    def decode(self, payload: bytes) -> bytes:
        """Decode a payload into s16le PCM."""
        raise NotImplementedError

    def samples(self, payload: bytes) -> int:
        """Return the number of samples in ``payload``.

        The default derives it from the format's fixed frame size; variable
        bit-rate codecs must override this.
        """
        n = format_info(self.format).samples_for_bytes(len(payload))
        if n is None:
            raise CodecError(f"{type(self).__name__} must implement samples()")
        return n

    def silence(self, ms: int = 20) -> bytes:
        """Return an encoded payload of ``ms`` milliseconds of silence."""
        return self.encode(bytes(2 * self.sample_rate * ms // 1000))


_REGISTRY: dict[int, type[Codec]] = {}


def register_codec(cls: type[Codec]) -> type[Codec]:
    """Register (or replace) the codec class for ``cls.format``. Usable as a decorator."""
    if not cls.format:
        raise CodecError(f"{cls.__name__} has no format")
    _REGISTRY[int(cls.format)] = cls
    return cls


def unregister_codec(fmt: int) -> None:
    """Remove the codec registered for ``fmt`` (no error if none)."""
    _REGISTRY.pop(int(fmt), None)


def get_codec(fmt: int) -> Codec | None:
    """Return a new codec instance for format ``fmt``, or ``None`` if unsupported."""
    cls = _REGISTRY.get(int(fmt))
    return cls() if cls else None


def available_formats() -> int:
    """Return the bit mask of all formats with a registered codec."""
    mask = 0
    for f in _REGISTRY:
        mask |= f
    return mask

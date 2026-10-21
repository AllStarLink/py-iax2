# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Media format (codec) bitmasks and negotiation helpers.

IAX2 identifies codecs with a 64-bit bit field ("``iax2_format``" in
Asterisk). A *capability* is a bitwise OR of every format an endpoint can
handle; a *format* is normally exactly one bit, the codec actually chosen.

This module also implements Asterisk's codec-preference string carried in
the CODEC_PREFS IE (one character per codec, ``chr(ord('A') + order)``) and
Asterisk's built-in "best codec" ranking, so that format choice matches
what Asterisk would pick given the same inputs.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

__all__ = [
    "AUDIO_MASK",
    "IMAGE_MASK",
    "TEXT_MASK",
    "VIDEO_MASK",
    "Format",
    "FormatInfo",
    "best_format",
    "choose_format",
    "decode_codec_prefs",
    "encode_codec_prefs",
    "format_by_name",
    "format_info",
    "format_names",
    "iter_formats",
]


class Format(enum.IntFlag):
    """Codec bits (Asterisk ``AST_FORMAT_*`` compatibility values)."""

    G723 = 1 << 0
    GSM = 1 << 1
    ULAW = 1 << 2
    ALAW = 1 << 3
    G726_AAL2 = 1 << 4
    ADPCM = 1 << 5
    SLIN = 1 << 6
    LPC10 = 1 << 7
    G729 = 1 << 8
    SPEEX = 1 << 9
    ILBC = 1 << 10
    G726 = 1 << 11
    G722 = 1 << 12
    SIREN7 = 1 << 13
    SIREN14 = 1 << 14
    SLIN16 = 1 << 15
    JPEG = 1 << 16
    PNG = 1 << 17
    H261 = 1 << 18
    H263 = 1 << 19
    H263P = 1 << 20
    H264 = 1 << 21
    MP4 = 1 << 22
    VP8 = 1 << 23
    T140_RED = 1 << 26
    T140 = 1 << 27
    G719 = 1 << 32
    SPEEX16 = 1 << 33
    OPUS = 1 << 34


#: Every audio format bit.
AUDIO_MASK = (
    Format.G723
    | Format.GSM
    | Format.ULAW
    | Format.ALAW
    | Format.G726_AAL2
    | Format.ADPCM
    | Format.SLIN
    | Format.LPC10
    | Format.G729
    | Format.SPEEX
    | Format.ILBC
    | Format.G726
    | Format.G722
    | Format.SIREN7
    | Format.SIREN14
    | Format.SLIN16
    | Format.G719
    | Format.SPEEX16
    | Format.OPUS
)
#: Every video format bit.
VIDEO_MASK = Format.H261 | Format.H263 | Format.H263P | Format.H264 | Format.MP4 | Format.VP8
#: Every still-image format bit.
IMAGE_MASK = Format.JPEG | Format.PNG
#: Every text format bit.
TEXT_MASK = Format.T140 | Format.T140_RED


@dataclass(frozen=True)
class FormatInfo:
    """Static description of a codec.

    Attributes:
        format: The format bit.
        name: Asterisk's short name (as used in ``allow=`` lines).
        sample_rate: Samples per second (0 for non-audio).
        default_ms: Asterisk's default packetisation in milliseconds.
        frame_bytes: Bytes per ``frame_samples`` samples for constant-bit-rate
            codecs, or ``0`` if the codec is variable-rate / non-audio.
        frame_samples: Samples per ``frame_bytes`` bytes.
    """

    format: Format
    name: str
    sample_rate: int
    default_ms: int
    frame_bytes: int = 0
    frame_samples: int = 0

    @property
    def is_audio(self) -> bool:
        """``True`` if this is an audio codec."""
        return bool(self.format & AUDIO_MASK)

    def samples_for_bytes(self, nbytes: int) -> int | None:
        """Return how many samples ``nbytes`` of payload represent.

        Returns ``None`` for variable-rate codecs where the count cannot be
        derived from the length alone.
        """
        if not self.frame_bytes:
            return None
        return (nbytes // self.frame_bytes) * self.frame_samples

    def bytes_for_ms(self, ms: int) -> int | None:
        """Return the payload size of ``ms`` milliseconds, or ``None`` if unknown."""
        if not self.frame_bytes or not self.sample_rate:
            return None
        samples = self.sample_rate * ms // 1000
        return samples * self.frame_bytes // self.frame_samples


_INFO: dict[Format, FormatInfo] = {
    f.format: f
    for f in (
        FormatInfo(Format.G723, "g723", 8000, 30, 24, 240),
        FormatInfo(Format.GSM, "gsm", 8000, 20, 33, 160),
        FormatInfo(Format.ULAW, "ulaw", 8000, 20, 1, 1),
        FormatInfo(Format.ALAW, "alaw", 8000, 20, 1, 1),
        FormatInfo(Format.G726_AAL2, "g726aal2", 8000, 20, 1, 2),
        FormatInfo(Format.ADPCM, "adpcm", 8000, 20, 1, 2),
        FormatInfo(Format.SLIN, "slin", 8000, 20, 2, 1),
        FormatInfo(Format.LPC10, "lpc10", 8000, 20, 7, 180),
        FormatInfo(Format.G729, "g729", 8000, 20, 10, 80),
        FormatInfo(Format.SPEEX, "speex", 8000, 20),
        FormatInfo(Format.ILBC, "ilbc", 8000, 30, 50, 240),
        FormatInfo(Format.G726, "g726", 8000, 20, 1, 2),
        FormatInfo(Format.G722, "g722", 16000, 20, 1, 2),
        FormatInfo(Format.SIREN7, "siren7", 16000, 20, 80, 320),
        FormatInfo(Format.SIREN14, "siren14", 32000, 20, 120, 640),
        FormatInfo(Format.SLIN16, "slin16", 16000, 20, 2, 1),
        FormatInfo(Format.JPEG, "jpeg", 0, 0),
        FormatInfo(Format.PNG, "png", 0, 0),
        FormatInfo(Format.H261, "h261", 0, 0),
        FormatInfo(Format.H263, "h263", 0, 0),
        FormatInfo(Format.H263P, "h263p", 0, 0),
        FormatInfo(Format.H264, "h264", 0, 0),
        FormatInfo(Format.MP4, "mpeg4", 0, 0),
        FormatInfo(Format.VP8, "vp8", 0, 0),
        FormatInfo(Format.T140_RED, "red", 0, 0),
        FormatInfo(Format.T140, "t140", 0, 0),
        FormatInfo(Format.G719, "g719", 48000, 20, 160, 960),
        FormatInfo(Format.SPEEX16, "speex16", 16000, 20),
        FormatInfo(Format.OPUS, "opus", 48000, 20),
    )
}
_BY_NAME = {i.name: i.format for i in _INFO.values()}

# Order matters: index + 1 is what goes over the wire in CODEC_PREFS
# (``iax2_supported_formats[]`` in Asterisk's codec_pref.c).
_PREF_ORDER: tuple[int, ...] = (
    Format.G723,
    Format.GSM,
    Format.ULAW,
    Format.ALAW,
    Format.G726,
    Format.ADPCM,
    Format.SLIN,
    Format.LPC10,
    Format.G729,
    Format.SPEEX,
    Format.SPEEX16,
    Format.ILBC,
    Format.G726_AAL2,
    Format.G722,
    Format.SLIN16,
    Format.JPEG,
    Format.PNG,
    Format.H261,
    Format.H263,
    Format.H263P,
    Format.H264,
    Format.MP4,
    Format.T140_RED,
    Format.T140,
    Format.SIREN7,
    Format.SIREN14,
    0,
    Format.G719,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    Format.OPUS,
    Format.VP8,
)

# Asterisk's opinionated "best codec" ranking (iax2_format_compatibility_best()).
_BEST: tuple[Format, ...] = (
    Format.ULAW,
    Format.ALAW,
    Format.G719,
    Format.SIREN14,
    Format.SIREN7,
    Format.G722,
    Format.SLIN16,
    Format.SLIN,
    Format.G726,
    Format.G726_AAL2,
    Format.ADPCM,
    Format.GSM,
    Format.ILBC,
    Format.SPEEX16,
    Format.SPEEX,
    Format.OPUS,
    Format.LPC10,
    Format.G729,
    Format.G723,
)


def format_info(fmt: int) -> FormatInfo:
    """Return the :class:`FormatInfo` for a single format bit.

    Raises:
        KeyError: if ``fmt`` is not exactly one known format bit.
    """
    return _INFO[Format(fmt)]


def format_by_name(name: str) -> Format:
    """Look up a format by its Asterisk name (``"ulaw"``, ``"gsm"``, ...).

    Raises:
        KeyError: if the name is unknown.
    """
    return _BY_NAME[name.strip().lower()]


def iter_formats(mask: int) -> Iterator[Format]:
    """Yield each known single-bit :class:`Format` present in ``mask``, lowest bit first."""
    for bit in range(64):
        value = 1 << bit
        if mask & value and value in _INFO:
            yield Format(value)


def format_names(mask: int) -> list[str]:
    """Return the Asterisk names of every format in ``mask``."""
    return [_INFO[f].name for f in iter_formats(mask)]


def best_format(mask: int) -> int:
    """Pick a format from ``mask`` using Asterisk's built-in ranking.

    Returns ``0`` if no audio format in ``mask`` is ranked. Mirrors
    ``iax2_format_compatibility_best()``.
    """
    for fmt in _BEST:
        if mask & fmt:
            return int(fmt)
    return 0


def encode_codec_prefs(prefs: Iterable[int]) -> str:
    """Encode an ordered list of formats as an Asterisk CODEC_PREFS string.

    Formats with no preference slot are silently skipped.
    """
    out = []
    for fmt in prefs:
        try:
            out.append(chr(ord("A") + _PREF_ORDER.index(int(fmt)) + 1))
        except ValueError:
            continue
    return "".join(out)


def decode_codec_prefs(prefs: str) -> list[int]:
    """Decode a CODEC_PREFS string into an ordered list of format bits.

    Unknown characters are ignored.
    """
    out: list[int] = []
    for ch in prefs:
        idx = ord(ch) - ord("A")
        if 1 <= idx <= len(_PREF_ORDER) and _PREF_ORDER[idx - 1]:
            out.append(_PREF_ORDER[idx - 1])
    return out


def _first_pref(prefs: Iterable[int], mask: int) -> int:
    for fmt in prefs:
        if fmt & mask & AUDIO_MASK:
            return int(fmt)
    return 0


def choose_format(
    peer_capability: int,
    peer_format: int,
    our_capability: int,
    *,
    our_prefs: Iterable[int] = (),
    peer_prefs: Iterable[int] = (),
    caller_first: bool = False,
    use_prefs: bool = True,
    requested_only: bool = False,
) -> int:
    """Choose the audio format for an incoming call exactly as Asterisk does.

    Mirrors the NEW / AUTHREP handling in ``socket_process_helper()``:

    * With preferences (the default, Asterisk ``codecpriority=host``): take the
      first of *our* preferences present in the joint capability. If
      ``caller_first`` (``codecpriority=caller``) and the caller sent a
      CODEC_PREFS list, the caller's list is tried first instead. If that
      fails, the other list is tried; if the caller sent no list, Asterisk's
      built-in ranking (:func:`best_format`) is used.
    * Without preferences (``codecpriority=disabled``): use the caller's
      requested ``peer_format`` if we support it, else the best joint codec.
    * ``requested_only`` (``codecpriority=reqonly``): only the requested
      format is acceptable.

    Args:
        peer_capability: Caller's CAPABILITY mask.
        peer_format: Caller's requested FORMAT.
        our_capability: Our capability mask.
        our_prefs: Our ordered preference list (Asterisk ``allow=`` order).
        peer_prefs: Caller's ordered list from the CODEC_PREFS IE.
        caller_first: Give the caller's preferences priority.
        use_prefs: ``False`` to ignore preference lists altogether.
        requested_only: Accept only ``peer_format``.

    Returns:
        A single format bit, or ``0`` if no common audio codec exists (the
        call should be rejected with cause BEARERCAPABILITY_NOTAVAIL).
    """
    our_prefs = list(our_prefs)
    peer_prefs = list(peer_prefs)
    joint = peer_capability & our_capability & AUDIO_MASK
    if requested_only:
        fmt = peer_format & our_capability & AUDIO_MASK
        return best_format(fmt) if fmt else 0
    if not use_prefs:
        fmt = peer_format & our_capability & AUDIO_MASK
        if fmt and fmt.bit_count() == 1:
            return int(fmt)
        return best_format(fmt or joint)
    if not joint:
        return 0
    if peer_prefs:
        first, second = (peer_prefs, our_prefs) if caller_first else (our_prefs, peer_prefs)
        return _first_pref(first, joint) or _first_pref(second, joint) or best_format(joint)
    return _first_pref(our_prefs, joint) or best_format(joint)

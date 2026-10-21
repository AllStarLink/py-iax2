# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""IAX2 wire format: frame classes, parsing and serialisation.

Four kinds of datagram exist (RFC 5456 §8):

* :class:`FullFrame` — 12-byte header with call numbers, 32-bit timestamp and
  sequence numbers. Used for all signalling and the first voice frame of a
  stream; delivered reliably.
* :class:`MiniFrame` — 4-byte header (call number, low 16 bits of the
  timestamp). Voice only; unreliable.
* :class:`VideoFrame` — 6-byte meta video header (Asterisk "mini video").
* :class:`TrunkFrame` — meta trunk frame bundling mini frames of many calls.

This module performs **no I/O** and keeps **no call state**. Converting a
mini-frame's 16-bit timestamp into a full timestamp, decryption and similar
per-call work is done by higher layers.

Example::

    >>> from iax2.frames import FullFrame, parse_datagram
    >>> from iax2.constants import IAXCommand
    >>> f = FullFrame.iax(IAXCommand.PING, source_call=1, dest_call=2, timestamp=100)
    >>> parse_datagram(f.to_bytes()) == f
    True
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .constants import (
    IAX_FLAG_FULL,
    IAX_FLAG_RETRANS,
    IAX_FLAG_SC_LOG,
    IAX_MAX_SHIFT,
    META_TRUNK_MINI,
    META_TRUNK_SUPERMINI,
    ControlType,
    FrameType,
    IAXCommand,
    MetaCommand,
)
from .exceptions import FrameError
from .ies import IEs

__all__ = [
    "FULL_HEADER_LEN",
    "MINI_HEADER_LEN",
    "VIDEO_HEADER_LEN",
    "Frame",
    "FullFrame",
    "MiniFrame",
    "TrunkEntry",
    "TrunkFrame",
    "VideoFrame",
    "compress_subclass",
    "parse_datagram",
    "peek_call_numbers",
    "uncompress_subclass",
]

FULL_HEADER_LEN = 12
MINI_HEADER_LEN = 4
VIDEO_HEADER_LEN = 6
_META_HEADER_LEN = 4
_TRUNK_TS_LEN = 4

_FULL = struct.Struct("!HHIBBBB")
_MINI = struct.Struct("!HH")
_VIDEO = struct.Struct("!HHH")
_META = struct.Struct("!HBB")

#: Frame types whose subclass is a media format bit rather than an integer.
_FORMAT_TYPES = frozenset({FrameType.VOICE, FrameType.VIDEO, FrameType.IMAGE})


def compress_subclass(subclass: int) -> int:
    """Encode a subclass value into the 8-bit wire field.

    Values below 0x80 are sent as-is; larger values must be a single power of
    two and are sent as ``0x80 | log2(value)``. ``-1`` encodes as ``0xff``.
    Mirrors Asterisk's ``compress_subclass()``.

    Raises:
        FrameError: if the value cannot be represented.
    """
    if subclass == -1:
        return 0xFF
    if 0 <= subclass < IAX_FLAG_SC_LOG:
        return subclass
    if subclass <= 0 or subclass & (subclass - 1):
        raise FrameError(f"cannot compress subclass {subclass:#x}")
    power = subclass.bit_length() - 1
    if power > IAX_MAX_SHIFT:
        raise FrameError(f"cannot compress subclass {subclass:#x}")
    return power | IAX_FLAG_SC_LOG


def uncompress_subclass(csub: int) -> int:
    """Decode the 8-bit wire subclass field (inverse of :func:`compress_subclass`)."""
    if csub & IAX_FLAG_SC_LOG:
        if csub == 0xFF:
            return -1
        return 1 << (csub & IAX_MAX_SHIFT)
    return csub


def _named(value: int, enum_type: Any) -> Any:
    try:
        return enum_type(value)
    except ValueError:
        return value


class Frame:
    """Base class for all parsed datagrams."""

    __slots__ = ()

    def to_bytes(self) -> bytes:  # pragma: no cover - abstract
        """Serialise to a datagram."""
        raise NotImplementedError

    def __bytes__(self) -> bytes:
        return self.to_bytes()


@dataclass(eq=True, slots=True)
class FullFrame(Frame):
    """A full frame (RFC 5456 §8.1.1).

    Attributes:
        source_call: Sender's call number (1–32767; 0 is allowed only in
            unsolicited messages such as POKE replies).
        dest_call: Receiver's call number (0 if not yet known).
        timestamp: 32-bit timestamp in milliseconds since call start.
        oseqno: Outbound sequence number (0–255).
        iseqno: Next inbound sequence number expected (0–255).
        frame_type: Frame type (usually a :class:`~iax2.constants.FrameType`).
        subclass: Decoded subclass: an :class:`~iax2.constants.IAXCommand`,
            :class:`~iax2.constants.ControlType`, DTMF code, or a format bit for
            voice/video/image frames.
        payload: Frame body. For IAX frames this is the encoded IEs.
        retransmission: The R bit (frame is a retransmission).
        mark: Video "end of frame" marker (only for VIDEO frames).
    """

    frame_type: int
    subclass: int
    source_call: int = 0
    dest_call: int = 0
    timestamp: int = 0
    oseqno: int = 0
    iseqno: int = 0
    payload: bytes = b""
    retransmission: bool = False
    mark: bool = False

    kind: ClassVar[str] = "full"

    def __post_init__(self) -> None:
        self.frame_type = _named(self.frame_type, FrameType)
        self.subclass = _subclass_enum(self.frame_type, self.subclass)

    # -- constructors -----------------------------------------------------

    @classmethod
    def iax(cls, command: int, ies: IEs | None = None, **kw: Any) -> FullFrame:
        """Create an IAX control frame for ``command`` carrying ``ies``."""
        return cls(FrameType.IAX, int(command), payload=ies.to_bytes() if ies else b"", **kw)

    @classmethod
    def control(cls, control: int, payload: bytes = b"", **kw: Any) -> FullFrame:
        """Create a CONTROL frame."""
        return cls(FrameType.CONTROL, int(control), payload=payload, **kw)

    # -- views ------------------------------------------------------------

    @property
    def ies(self) -> IEs:
        """The payload parsed as Information Elements.

        Raises:
            IEError: if the payload is not a valid IE sequence.
        """
        return IEs.from_bytes(self.payload)

    @property
    def command(self) -> IAXCommand | int | None:
        """The IAX command for IAX frames, else ``None``."""
        if self.frame_type != FrameType.IAX:
            return None
        cmd: IAXCommand | int = _named(self.subclass, IAXCommand)
        return cmd

    @property
    def is_ack_like(self) -> bool:
        """``True`` for frames that are never ACKed and never consume a sequence number.

        These are ACK, INVAL, TXCNT, TXACC and VNAK (Asterisk excludes them
        from the ``oseqno`` increment and from acknowledgement).
        """
        return self.frame_type == FrameType.IAX and self.subclass in _NO_SEQ_COMMANDS

    # -- serialisation ----------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise to a datagram."""
        return self.header_bytes() + self.payload

    def header_bytes(self) -> bytes:
        """Serialise only the 12-byte header."""
        if not 0 <= self.source_call < 0x8000:
            raise FrameError(f"source call number {self.source_call} out of range")
        if not 0 <= self.dest_call < 0x8000:
            raise FrameError(f"destination call number {self.dest_call} out of range")
        if self.frame_type in _FORMAT_TYPES:
            csub = compress_subclass(self.subclass)
            if self.frame_type == FrameType.VIDEO and self.mark:
                csub |= 0x40
        else:
            csub = compress_subclass(self.subclass)
        return _FULL.pack(
            self.source_call | IAX_FLAG_FULL,
            self.dest_call | (IAX_FLAG_RETRANS if self.retransmission else 0),
            self.timestamp & 0xFFFFFFFF,
            self.oseqno & 0xFF,
            self.iseqno & 0xFF,
            self.frame_type & 0xFF,
            csub,
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> FullFrame:
        """Parse a full-frame datagram.

        Raises:
            FrameError: if the datagram is too short or not a full frame.
        """
        buf = bytes(data)
        if len(buf) < FULL_HEADER_LEN:
            raise FrameError(f"full frame too short ({len(buf)} bytes)")
        scall, dcall, ts, oseq, iseq, ftype, csub = _FULL.unpack_from(buf)
        if not scall & IAX_FLAG_FULL:
            raise FrameError("not a full frame (F bit clear)")
        mark = False
        if ftype == FrameType.VIDEO:
            mark = bool(csub & 0x40) and bool(csub & IAX_FLAG_SC_LOG)
            subclass = uncompress_subclass(csub & ~0x40 if csub & IAX_FLAG_SC_LOG else csub)
        else:
            subclass = uncompress_subclass(csub)
        return cls(
            frame_type=_named(ftype, FrameType),
            subclass=_subclass_enum(ftype, subclass),
            source_call=scall & ~IAX_FLAG_FULL,
            dest_call=dcall & ~IAX_FLAG_RETRANS,
            timestamp=ts,
            oseqno=oseq,
            iseqno=iseq,
            payload=buf[FULL_HEADER_LEN:],
            retransmission=bool(dcall & IAX_FLAG_RETRANS),
            mark=mark,
        )

    def describe(self) -> str:
        """Return a one-line human readable summary (for logs/debugging)."""
        ft = (
            self.frame_type.name if isinstance(self.frame_type, FrameType) else str(self.frame_type)
        )
        sc = self.subclass.name if hasattr(self.subclass, "name") else str(self.subclass)
        extra = ""
        if self.frame_type == FrameType.IAX and self.payload:
            try:
                extra = " " + repr(self.ies.to_dict())
            except FrameError:
                extra = f" <{len(self.payload)} bytes>"
        elif self.payload:
            extra = f" <{len(self.payload)} bytes>"
        r = " (retrans)" if self.retransmission else ""
        return (
            f"{ft}/{sc} {self.source_call}->{self.dest_call} ts={self.timestamp} "
            f"o={self.oseqno} i={self.iseqno}{r}{extra}"
        )


_NO_SEQ_COMMANDS = frozenset(
    {IAXCommand.ACK, IAXCommand.INVAL, IAXCommand.TXCNT, IAXCommand.TXACC, IAXCommand.VNAK}
)


def _subclass_enum(ftype: int, subclass: int) -> Any:
    if ftype == FrameType.IAX:
        return _named(subclass, IAXCommand)
    if ftype == FrameType.CONTROL:
        return _named(subclass, ControlType)
    return subclass


@dataclass(eq=True, slots=True)
class MiniFrame(Frame):
    """A mini frame carrying voice (RFC 5456 §8.1.2).

    Attributes:
        source_call: Sender's call number (1–32767).
        timestamp: Low 16 bits of the sender's timestamp.
        payload: Encoded audio in the call's current voice format.
    """

    source_call: int
    timestamp: int
    payload: bytes = b""

    kind: ClassVar[str] = "mini"

    def to_bytes(self) -> bytes:
        """Serialise to a datagram."""
        if not 0 < self.source_call < 0x8000:
            raise FrameError(f"mini frame call number {self.source_call} out of range")
        return _MINI.pack(self.source_call, self.timestamp & 0xFFFF) + self.payload

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> MiniFrame:
        """Parse a mini-frame datagram."""
        buf = bytes(data)
        if len(buf) < MINI_HEADER_LEN:
            raise FrameError(f"mini frame too short ({len(buf)} bytes)")
        call, ts = _MINI.unpack_from(buf)
        if call & IAX_FLAG_FULL or call == 0:
            raise FrameError("not a mini frame")
        return cls(call, ts, buf[MINI_HEADER_LEN:])


@dataclass(eq=True, slots=True)
class VideoFrame(Frame):
    """A meta video frame ("mini video frame", RFC 5456 §8.1.3.1).

    Attributes:
        source_call: Sender's call number.
        timestamp: Low 15 bits of the sender's timestamp.
        mark: End-of-video-frame marker.
        payload: Encoded video in the call's current video format.
    """

    source_call: int
    timestamp: int
    mark: bool = False
    payload: bytes = b""

    kind: ClassVar[str] = "video"

    def to_bytes(self) -> bytes:
        """Serialise to a datagram."""
        if not 0 < self.source_call < 0x8000:
            raise FrameError(f"video frame call number {self.source_call} out of range")
        ts = (self.timestamp & 0x7FFF) | (0x8000 if self.mark else 0)
        return _VIDEO.pack(0, self.source_call | 0x8000, ts) + self.payload

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> VideoFrame:
        """Parse a meta video datagram."""
        buf = bytes(data)
        if len(buf) < VIDEO_HEADER_LEN:
            raise FrameError(f"video frame too short ({len(buf)} bytes)")
        zeros, call, ts = _VIDEO.unpack_from(buf)
        if zeros or not call & 0x8000:
            raise FrameError("not a video meta frame")
        return cls(call & 0x7FFF, ts & 0x7FFF, bool(ts & 0x8000), buf[VIDEO_HEADER_LEN:])


@dataclass(eq=True, slots=True)
class TrunkEntry:
    """One call's media inside a :class:`TrunkFrame`.

    Attributes:
        source_call: Sender's call number.
        payload: Voice payload.
        timestamp: Low 16 bits of the call timestamp when the trunk frame
            uses trunk timestamps, else ``None``.
    """

    source_call: int
    payload: bytes
    timestamp: int | None = None


@dataclass(eq=True, slots=True)
class TrunkFrame(Frame):
    """A meta trunk frame (RFC 5456 §8.1.3.2).

    Attributes:
        timestamp: 32-bit trunk timestamp (ms since the trunk started).
        entries: Per-call media.
        trunk_timestamps: ``True`` if each entry carries its own 16-bit
            timestamp (Asterisk ``trunktimestamps=yes``; ``cmddata`` = 1).
    """

    timestamp: int
    entries: list[TrunkEntry] = field(default_factory=list)
    trunk_timestamps: bool = False

    kind: ClassVar[str] = "trunk"

    def to_bytes(self) -> bytes:
        """Serialise to a datagram."""
        out = [
            _META.pack(
                0,
                MetaCommand.TRUNK,
                META_TRUNK_MINI if self.trunk_timestamps else META_TRUNK_SUPERMINI,
            ),
            struct.pack("!I", self.timestamp & 0xFFFFFFFF),
        ]
        for e in self.entries:
            if self.trunk_timestamps:
                out.append(
                    struct.pack("!HHH", len(e.payload), e.source_call, (e.timestamp or 0) & 0xFFFF)
                )
            else:
                out.append(struct.pack("!HH", e.source_call, len(e.payload)))
            out.append(e.payload)
        return b"".join(out)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> TrunkFrame:
        """Parse a meta trunk datagram.

        Truncated trailing entries are dropped, as Asterisk does.

        Raises:
            FrameError: if the header is malformed or cmddata is unknown.
        """
        buf = bytes(data)
        if len(buf) < _META_HEADER_LEN + _TRUNK_TS_LEN:
            raise FrameError(f"trunk frame too short ({len(buf)} bytes)")
        zeros, cmd, cmddata = _META.unpack_from(buf)
        if zeros or cmd != MetaCommand.TRUNK:
            raise FrameError("not a meta trunk frame")
        if cmddata not in (META_TRUNK_MINI, META_TRUNK_SUPERMINI):
            raise FrameError(f"unknown meta trunk cmddata {cmddata}")
        (ts,) = struct.unpack_from("!I", buf, _META_HEADER_LEN)
        pos = _META_HEADER_LEN + _TRUNK_TS_LEN
        entries: list[TrunkEntry] = []
        with_ts = cmddata == META_TRUNK_MINI
        hdr = 6 if with_ts else 4
        while len(buf) - pos >= hdr:
            entry_ts: int | None
            if with_ts:
                ln, call, entry_ts = struct.unpack_from("!HHH", buf, pos)
            else:
                call, ln = struct.unpack_from("!HH", buf, pos)
                entry_ts = None
            pos += hdr
            if ln > len(buf) - pos:
                break
            entries.append(TrunkEntry(call & ~IAX_FLAG_FULL, buf[pos : pos + ln], entry_ts))
            pos += ln
        return cls(ts, entries, with_ts)


def peek_call_numbers(data: bytes | bytearray | memoryview) -> tuple[str, int, int]:
    """Classify a datagram cheaply without full parsing.

    Returns:
        ``(kind, source_call, dest_call)`` where ``kind`` is ``"full"``,
        ``"mini"``, ``"video"``, ``"meta"`` or ``"invalid"``. ``dest_call`` is
        0 for anything but full frames.
    """
    if len(data) < 4:
        return ("invalid", 0, 0)
    w0 = (data[0] << 8) | data[1]
    w1 = (data[2] << 8) | data[3]
    if w0 & IAX_FLAG_FULL:
        if len(data) < FULL_HEADER_LEN:
            return ("invalid", 0, 0)
        return ("full", w0 & 0x7FFF, w1 & 0x7FFF)
    if w0 == 0:
        if w1 & 0x8000:
            return ("video", w1 & 0x7FFF, 0)
        return ("meta", 0, 0)
    return ("mini", w0, 0)


def parse_datagram(data: bytes | bytearray | memoryview) -> Frame:
    """Parse any IAX2 datagram into the appropriate :class:`Frame` subclass.

    Raises:
        FrameError: if the datagram is malformed.
    """
    kind, _, _ = peek_call_numbers(data)
    if kind == "full":
        return FullFrame.from_bytes(data)
    if kind == "mini":
        return MiniFrame.from_bytes(data)
    if kind == "video":
        return VideoFrame.from_bytes(data)
    if kind == "meta":
        if len(data) >= 3 and data[2] == MetaCommand.TRUNK:
            return TrunkFrame.from_bytes(data)
        raise FrameError(f"unsupported meta command {data[2] if len(data) > 2 else None}")
    raise FrameError(f"datagram too short ({len(data)} bytes)")

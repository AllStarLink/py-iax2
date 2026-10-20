# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Information Element (IE) encoding and decoding.

IAX2 control messages (``FrameType.IAX`` full frames) carry their
parameters as a sequence of type-length-value *Information Elements*::

    +--------+--------+--------------------------+
    | type   | length | data (length bytes)      |
    +--------+--------+--------------------------+

:class:`IEs` is an ordered container of IEs that keeps the raw bytes of
each element (so unknown or malformed elements survive a round trip) and
offers typed accessors that encode and decode values the way Asterisk 22
does (``channels/iax2/parser.c``).

Example::

    >>> from iax2.ies import IEs
    >>> from iax2.constants import IE
    >>> ies = IEs()
    >>> ies.add(IE.USERNAME, "alice").add(IE.VERSION, 2)
    IEs([USERNAME='alice', VERSION=2])
    >>> IEs.from_bytes(ies.to_bytes()).get(IE.USERNAME)
    'alice'
"""

from __future__ import annotations

import datetime as _dt
import enum
import ipaddress
import struct
from collections.abc import Iterable, Iterator
from typing import Any

from .constants import IE
from .exceptions import IEError

__all__ = [
    "IEKind",
    "IEs",
    "datetime_from_ie",
    "datetime_to_ie",
    "decode_addr",
    "encode_addr",
    "ie_kind",
]


class IEKind(enum.Enum):
    """How an IE's payload is interpreted."""

    STRING = "string"
    """UTF-8 text (no terminator on the wire)."""
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    VERSIONED_U64 = "versioned_u64"
    """One version byte (0) followed by a big-endian u64."""
    EMPTY = "empty"
    """No payload; presence is the value (decoded as ``True``)."""
    RAW = "raw"
    """Opaque bytes."""
    ADDR = "addr"
    """A ``struct sockaddr_in``/``sockaddr_in6`` as Asterisk sends it."""
    DATETIME = "datetime"
    """A packed 32-bit date/time (RFC 5456 §8.6.28)."""
    VARIABLE = "variable"
    """A ``name=value`` channel-variable string."""


_KINDS: dict[int, IEKind] = {
    IE.CALLED_NUMBER: IEKind.STRING,
    IE.CALLING_NUMBER: IEKind.STRING,
    IE.CALLING_ANI: IEKind.STRING,
    IE.CALLING_NAME: IEKind.STRING,
    IE.CALLED_CONTEXT: IEKind.STRING,
    IE.USERNAME: IEKind.STRING,
    IE.PASSWORD: IEKind.STRING,
    IE.CAPABILITY: IEKind.U32,
    IE.FORMAT: IEKind.U32,
    IE.LANGUAGE: IEKind.STRING,
    IE.VERSION: IEKind.U16,
    IE.ADSICPE: IEKind.U16,
    IE.DNID: IEKind.STRING,
    IE.AUTHMETHODS: IEKind.U16,
    IE.CHALLENGE: IEKind.STRING,
    IE.MD5_RESULT: IEKind.STRING,
    IE.RSA_RESULT: IEKind.STRING,
    IE.APPARENT_ADDR: IEKind.ADDR,
    IE.REFRESH: IEKind.U16,
    IE.DPSTATUS: IEKind.U16,
    IE.CALLNO: IEKind.U16,
    IE.CAUSE: IEKind.STRING,
    IE.IAX_UNKNOWN: IEKind.U8,
    IE.MSGCOUNT: IEKind.U16,
    IE.AUTOANSWER: IEKind.EMPTY,
    IE.MUSICONHOLD: IEKind.EMPTY,
    IE.TRANSFERID: IEKind.U32,
    IE.RDNIS: IEKind.STRING,
    IE.PROVISIONING: IEKind.RAW,
    IE.AESPROVISIONING: IEKind.RAW,
    IE.DATETIME: IEKind.DATETIME,
    IE.DEVICETYPE: IEKind.STRING,
    IE.SERVICEIDENT: IEKind.STRING,
    IE.FIRMWAREVER: IEKind.U16,
    IE.FWBLOCKDESC: IEKind.U32,
    IE.FWBLOCKDATA: IEKind.RAW,
    IE.PROVVER: IEKind.U32,
    IE.CALLINGPRES: IEKind.U8,
    IE.CALLINGTON: IEKind.U8,
    IE.CALLINGTNS: IEKind.U16,
    IE.SAMPLINGRATE: IEKind.U16,
    IE.CAUSECODE: IEKind.U8,
    IE.ENCRYPTION: IEKind.U16,
    IE.ENCKEY: IEKind.RAW,
    IE.CODEC_PREFS: IEKind.STRING,
    IE.RR_JITTER: IEKind.U32,
    IE.RR_LOSS: IEKind.U32,
    IE.RR_PKTS: IEKind.U32,
    IE.RR_DELAY: IEKind.U16,
    IE.RR_DROPPED: IEKind.U32,
    IE.RR_OOO: IEKind.U32,
    IE.VARIABLE: IEKind.VARIABLE,
    IE.OSPTOKEN: IEKind.RAW,
    IE.CALLTOKEN: IEKind.RAW,
    IE.CAPABILITY2: IEKind.VERSIONED_U64,
    IE.FORMAT2: IEKind.VERSIONED_U64,
    IE.CALLINGANI2: IEKind.U32,
}

_FIXED = {IEKind.U8: 1, IEKind.U16: 2, IEKind.U32: 4, IEKind.VERSIONED_U64: 9,
          IEKind.DATETIME: 4}

_AF_INET = 2
_AF_INET6 = 10  # Linux value; Asterisk sends host-order struct sockaddr.


def ie_kind(ie: int) -> IEKind:
    """Return the :class:`IEKind` of an IE type (``RAW`` for unknown types)."""
    return _KINDS.get(ie, IEKind.RAW)


def _ie_name(ie: int) -> str:
    try:
        return IE(ie).name
    except ValueError:
        return f"IE_{ie}"


# --------------------------------------------------------------------------
# DATETIME
# --------------------------------------------------------------------------

def datetime_to_ie(when: _dt.datetime | float | None = None) -> int:
    """Pack a time into the 32-bit IAX2 DATETIME value.

    Layout (MSB→LSB): year-2000 (7 bits), month 1–12 (4), day (5),
    hours (5), minutes (6), seconds/2 (5). Asterisk encodes *local* time;
    this function encodes the given ``datetime`` as-is, or local time for a
    POSIX timestamp / ``None`` (now).

    Args:
        when: An aware or naive :class:`~datetime.datetime`, a POSIX
            timestamp, or ``None`` for the current time.
    """
    if when is None:
        dt = _dt.datetime.now()
    elif isinstance(when, _dt.datetime):
        dt = when
    else:
        dt = _dt.datetime.fromtimestamp(when)
    if not 2000 <= dt.year <= 2127:
        raise IEError(f"DATETIME year {dt.year} out of range 2000-2127")
    return (
        ((dt.year - 2000) << 25)
        | (dt.month << 21)
        | (dt.day << 16)
        | (dt.hour << 11)
        | (dt.minute << 5)
        | (dt.second >> 1)
    )


def datetime_from_ie(value: int) -> _dt.datetime:
    """Unpack a 32-bit DATETIME value into a naive :class:`~datetime.datetime`.

    Raises:
        IEError: if the packed fields do not form a valid date.
    """
    try:
        return _dt.datetime(
            2000 + ((value >> 25) & 0x7F),
            (value >> 21) & 0x0F,
            (value >> 16) & 0x1F,
            (value >> 11) & 0x1F,
            (value >> 5) & 0x3F,
            (value & 0x1F) << 1,
        )
    except ValueError as exc:
        raise IEError(f"invalid DATETIME value 0x{value:08x}: {exc}") from None


# --------------------------------------------------------------------------
# APPARENT_ADDR
# --------------------------------------------------------------------------

def encode_addr(host: str, port: int) -> bytes:
    """Encode an address the way Asterisk does on Linux.

    Asterisk copies a raw ``struct sockaddr_in`` (16 bytes) or
    ``sockaddr_in6`` (28 bytes) into the IE. The address family is in *host*
    byte order (little-endian on every platform ASL runs on); port and
    address are in network byte order.
    """
    ip = ipaddress.ip_address(host)
    if ip.version == 4:
        return struct.pack("<H", _AF_INET) + struct.pack("!H", port) + ip.packed + bytes(8)
    return (
        struct.pack("<H", _AF_INET6) + struct.pack("!HI", port, 0) + ip.packed + struct.pack("!I", 0)
    )


def decode_addr(data: bytes) -> tuple[str, int]:
    """Decode an APPARENT_ADDR payload into ``(host, port)``.

    Accepts both host-order (Asterisk) and network-order (RFC) family fields.

    Raises:
        IEError: if the payload is not a recognisable sockaddr.
    """
    if len(data) >= 8:
        port = struct.unpack_from("!H", data, 2)[0]
        if len(data) == 16:
            return str(ipaddress.IPv4Address(data[4:8])), port
        if len(data) == 28:
            ip6 = ipaddress.IPv6Address(data[8:24])
            v4 = ip6.ipv4_mapped
            return (str(v4) if v4 else str(ip6)), port
    raise IEError(f"invalid APPARENT_ADDR length {len(data)}")


# --------------------------------------------------------------------------
# Value codecs
# --------------------------------------------------------------------------

def encode_value(ie: int, value: Any) -> bytes:
    """Encode a Python value as the payload (without TLV header) of ``ie``.

    Accepted value types by kind:

    * STRING / VARIABLE — ``str`` (UTF-8 encoded) or ``bytes``. A VARIABLE may
      also be a ``(name, value)`` tuple.
    * U8/U16/U32 — ``int``.
    * VERSIONED_U64 — ``int`` (version 0 is written).
    * EMPTY — ignored (pass ``True``/``None``).
    * RAW — ``bytes``.
    * ADDR — ``(host, port)`` tuple or raw ``bytes``.
    * DATETIME — ``datetime``, POSIX timestamp, packed ``int``, or ``None``.

    Raises:
        IEError: if the value does not fit the IE.
    """
    kind = ie_kind(ie)
    try:
        if kind in (IEKind.STRING, IEKind.VARIABLE):
            if kind is IEKind.VARIABLE and isinstance(value, tuple):
                value = f"{value[0]}={value[1]}"
            return value if isinstance(value, bytes) else str(value).encode("utf-8", "surrogateescape")
        if kind is IEKind.U8:
            return struct.pack("!B", value)
        if kind is IEKind.U16:
            return struct.pack("!H", value)
        if kind is IEKind.U32:
            return struct.pack("!I", value & 0xFFFFFFFF if value < 0 else value)
        if kind is IEKind.VERSIONED_U64:
            return struct.pack("!BQ", 0, value & 0xFFFFFFFFFFFFFFFF)
        if kind is IEKind.EMPTY:
            return b""
        if kind is IEKind.ADDR:
            if isinstance(value, bytes):
                return value
            return encode_addr(value[0], value[1])
        if kind is IEKind.DATETIME:
            if isinstance(value, int) and not isinstance(value, bool):
                return struct.pack("!I", value)
            return struct.pack("!I", datetime_to_ie(value))
        return bytes(value)
    except (struct.error, TypeError, ValueError) as exc:
        raise IEError(f"cannot encode {value!r} for {_ie_name(ie)}: {exc}") from None


def decode_value(ie: int, data: bytes) -> Any:
    """Decode the payload of ``ie`` into a Python value.

    Returns ``str`` for strings, ``int`` for integers, ``True`` for EMPTY,
    ``(host, port)`` for ADDR, :class:`~datetime.datetime` for DATETIME,
    ``(name, value)`` for VARIABLE and ``bytes`` otherwise.

    Raises:
        IEError: if the payload has the wrong length for the IE (Asterisk logs
            and ignores such IEs).
    """
    kind = ie_kind(ie)
    want = _FIXED.get(kind)
    if want is not None and len(data) != want:
        raise IEError(f"{_ie_name(ie)}: expected {want} bytes, got {len(data)}")
    if kind is IEKind.STRING:
        return data.decode("utf-8", "surrogateescape")
    if kind is IEKind.VARIABLE:
        text = data.decode("utf-8", "surrogateescape")
        name, _, val = text.partition("=")
        return (name, val)
    if kind is IEKind.U8:
        return data[0]
    if kind is IEKind.U16:
        return struct.unpack("!H", data)[0]
    if kind is IEKind.U32:
        return struct.unpack("!I", data)[0]
    if kind is IEKind.VERSIONED_U64:
        version, value = struct.unpack("!BQ", data)
        if version != 0:
            raise IEError(f"{_ie_name(ie)}: unsupported version {version}")
        return value
    if kind is IEKind.EMPTY:
        return True
    if kind is IEKind.ADDR:
        return decode_addr(data)
    if kind is IEKind.DATETIME:
        return datetime_from_ie(struct.unpack("!I", data)[0])
    return bytes(data)


# --------------------------------------------------------------------------
# Container
# --------------------------------------------------------------------------

class IEs:
    """An ordered collection of Information Elements.

    Elements are stored as ``(type, payload_bytes)`` pairs in wire order, so
    repeated elements (e.g. VARIABLE, OSPTOKEN) and unknown types are
    preserved.

    The container supports ``in`` (``IE.USERNAME in ies``), iteration over
    ``(type, payload)`` pairs, ``len()`` and equality.
    """

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[tuple[int, bytes]] = ()):
        self._items: list[tuple[int, bytes]] = [(int(t), bytes(d)) for t, d in items]

    # -- construction -----------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> IEs:
        """Parse a buffer of concatenated IEs.

        Raises:
            IEError: if an element's length runs past the end of the buffer,
                or a single stray byte remains (Asterisk rejects both).
        """
        buf = bytes(data)
        items: list[tuple[int, bytes]] = []
        pos = 0
        end = len(buf)
        while end - pos >= 2:
            ie, ln = buf[pos], buf[pos + 1]
            if ln > end - pos - 2:
                raise IEError(
                    f"IE {_ie_name(ie)} length {ln} exceeds remaining {end - pos - 2} bytes"
                )
            items.append((ie, buf[pos + 2 : pos + 2 + ln]))
            pos += 2 + ln
        if pos != end:
            raise IEError("invalid IE contents: strange boundary")
        obj = cls.__new__(cls)
        obj._items = items
        return obj

    @classmethod
    def build(cls, *pairs: tuple[int, Any], **named: Any) -> IEs:
        """Build from ``(IE, value)`` pairs and/or ``NAME=value`` keywords.

        Keyword names are :class:`~iax2.constants.IE` member names
        (case-insensitive). ``None`` values are skipped, which makes
        optional elements convenient::

            IEs.build(username="bob", version=2, called_context=None)
        """
        obj = cls()
        for ie, value in pairs:
            if value is not None:
                obj.add(ie, value)
        for name, value in named.items():
            if value is not None:
                obj.add(IE[name.upper()], value)
        return obj

    def add(self, ie: int, value: Any = True) -> IEs:
        """Append an element, encoding ``value`` per the IE's kind. Returns ``self``."""
        data = encode_value(ie, value)
        if len(data) > 255:
            raise IEError(f"{_ie_name(ie)} payload too long ({len(data)} > 255)")
        self._items.append((int(ie), data))
        return self

    def add_raw(self, ie: int, data: bytes) -> IEs:
        """Append an element with an already-encoded payload. Returns ``self``."""
        if len(data) > 255:
            raise IEError(f"{_ie_name(ie)} payload too long ({len(data)} > 255)")
        self._items.append((int(ie), bytes(data)))
        return self

    def set(self, ie: int, value: Any = True) -> IEs:
        """Replace every element of type ``ie`` with a single new one. Returns ``self``."""
        self.remove(ie)
        return self.add(ie, value)

    def remove(self, ie: int) -> int:
        """Remove every element of type ``ie``; return how many were removed."""
        before = len(self._items)
        self._items = [(t, d) for t, d in self._items if t != ie]
        return before - len(self._items)

    # -- serialisation ----------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise all elements in order."""
        return b"".join(bytes((t, len(d))) + d for t, d in self._items)

    __bytes__ = to_bytes

    # -- access -----------------------------------------------------------

    def raw(self, ie: int) -> bytes | None:
        """Return the payload of the *last* element of type ``ie``, or ``None``.

        Asterisk's parser overwrites earlier occurrences, so the last one wins.
        """
        for t, d in reversed(self._items):
            if t == ie:
                return d
        return None

    def raw_all(self, ie: int) -> list[bytes]:
        """Return the payloads of every element of type ``ie``, in order."""
        return [d for t, d in self._items if t == ie]

    def get(self, ie: int, default: Any = None) -> Any:
        """Return the decoded value of ``ie`` (last occurrence), or ``default``.

        Malformed elements (wrong length) are treated as absent, matching
        Asterisk which logs and ignores them.
        """
        data = self.raw(ie)
        if data is None:
            return default
        try:
            return decode_value(ie, data)
        except IEError:
            return default

    def get_all(self, ie: int) -> list[Any]:
        """Return the decoded values of every well-formed element of type ``ie``."""
        out = []
        for d in self.raw_all(ie):
            try:
                out.append(decode_value(ie, d))
            except IEError:
                pass
        return out

    # Convenience accessors that apply Asterisk's precedence rules.

    @property
    def capability(self) -> int:
        """Capability mask: CAPABILITY2 (64-bit) if present, else CAPABILITY, else 0."""
        v = self.get(IE.CAPABILITY2)
        if v is None:
            v = self.get(IE.CAPABILITY, 0)
        return int(v)

    @property
    def format(self) -> int:
        """Desired format: FORMAT2 (64-bit) if present, else FORMAT, else 0."""
        v = self.get(IE.FORMAT2)
        if v is None:
            v = self.get(IE.FORMAT, 0)
        return int(v)

    @property
    def variables(self) -> dict[str, str]:
        """Channel variables from VARIABLE IEs.

        Repeated names are concatenated, as Asterisk does for values longer
        than one IE.
        """
        out: dict[str, str] = {}
        for name, value in self.get_all(IE.VARIABLE):
            out[name] = out.get(name, "") + value
        return out

    @property
    def calltoken(self) -> bytes | None:
        """The CALLTOKEN payload (``b""`` for an empty token request) or ``None``."""
        return self.raw(IE.CALLTOKEN)

    # -- dunder -----------------------------------------------------------

    def __contains__(self, ie: object) -> bool:
        return any(t == ie for t, _ in self._items)

    def __iter__(self) -> Iterator[tuple[int, bytes]]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IEs):
            return NotImplemented
        return self._items == other._items

    __hash__ = None  # type: ignore[assignment]  # mutable container

    def __repr__(self) -> str:
        parts = []
        for t, d in self._items:
            try:
                val = decode_value(t, d)
            except IEError:
                val = d
            parts.append(f"{_ie_name(t)}={val!r}")
        return f"IEs([{', '.join(parts)}])"

    def to_dict(self) -> dict[str, Any]:
        """Return ``{IE name: decoded value}`` (last occurrence wins); handy for logging."""
        out: dict[str, Any] = {}
        for t, d in self._items:
            try:
                out[_ie_name(t)] = decode_value(t, d)
            except IEError:
                out[_ie_name(t)] = d
        return out

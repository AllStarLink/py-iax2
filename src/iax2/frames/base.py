"""Abstract Frame and header (de)serialisation.
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later

pyiax2.frames.base
==================

Common helpers shared by all IAX2 frame and IE codec code.

This module deliberately contains *no* I/O. Everything here is pure
byte-in / byte-out so that frame and IE parsing can be unit-tested
without sockets, and so the same code paths work for both live UDP
traffic and pcap replay.

Conventions
-----------
* All multi-byte integers are network byte order (big-endian) per
  RFC 5456 §8.
* Strings carried in IEs are UTF-8 per RFC 5456 §8.6.
* Encoders return ``bytes``. Decoders accept any bytes-like buffer
  and return a Python value appropriate to the IE.
* TLV header is fixed-width: 1 byte type, 1 byte length, then payload.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from typing import Callable
import time

def encode_datetime_ie_from_timestamp(ts=None) -> bytes:
    """
    Encodes a UTC timestamp (POSIX) into the 32-bit DATETIME format.
    Returns:
        4-byte big-endian representation of the encoded timestamp.
    """
    if ts is None:
        ts = time.time()

    # Convert to UTC time tuple
    utc = time.gmtime(ts)

    year = utc.tm_year - 2000  # offset from 2000
    month = utc.tm_mon
    day = utc.tm_mday
    hour = utc.tm_hour
    minute = utc.tm_min
    second = utc.tm_sec

    encoded = (
        (year & 0x7F) << 25
        | (month & 0x0F) << 21
        | (day & 0x1F) << 16
        | (hour & 0x1F) << 11
        | (minute & 0x3F) << 5
        | ((second // 2) & 0x1F)    # seconds is a 5-bit field w/ 2 second resolution
    )

    return encoded.to_bytes(4, byteorder="big")

def decode_datetime_ie_to_timestamp(data: bytes) -> float:
    """
    Decode a 4-byte IAX2 DATETIME IE (RFC 5456 §8.6) into a POSIX timestamp.

    Bit layout (big-endian, MSB first):
        bits 31..25  year - 2000   (7 bits)
        bits 24..21  month         (4 bits, 1-12)
        bits 20..16  day           (5 bits, 1-31)
        bits 15..11  hour          (5 bits, 0-23)
        bits 10..5   minute        (6 bits, 0-59)
        bits  4..0   seconds / 2   (5 bits, 0-30)  -- 2-second resolution

    Args:
        data: Exactly 4 bytes, big-endian.

    Returns:
        POSIX timestamp (float seconds since the epoch, UTC).
    """
    if len(data) != 4:
        raise ValueError(f"DATETIME IE must be 4 bytes, got {len(data)}")

    encoded = int.from_bytes(data, byteorder="big")

    year   = ((encoded >> 25) & 0x7F) + 2000
    month  =  (encoded >> 21) & 0x0F
    day    =  (encoded >> 16) & 0x1F
    hour   =  (encoded >> 11) & 0x1F
    minute =  (encoded >>  5) & 0x3F
    second = ( encoded        & 0x1F) * 2  # # seconds is a 5-bit field w/ 2 second resolution

    # Build a UTC datetime and convert to POSIX timestamp.
    # Using datetime (not time.mktime) because mktime assumes local time.
    from datetime import datetime, timezone
    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return dt.timestamp()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class IEDecodeError(ValueError):
    """Raised when an IE payload cannot be parsed."""


class IEEncodeError(ValueError):
    """Raised when a value cannot be encoded into an IE payload."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IE_HEADER_LEN = 2          # 1-byte type + 1-byte length
IE_MAX_PAYLOAD = 0xFF      # length field is a single octet

# struct format prefixes used across the module
_FMT_U8 = "!B"
_FMT_U16 = "!H"
_FMT_U32 = "!I"
_FMT_TLV_HEADER = "!BB"

# Address-family constants for the APPARENT ADDR IE. RFC 5456 §8.6.17
# defines this IE in terms of the POSIX sockaddr struct, so the values
# encoded on the wire are the host's AF_INET / AF_INET6 constants
# (typically 2 and 10 on Linux). Asterisk hard-codes Linux values, so
# for interop we always emit those regardless of the local platform.
AF_INET_WIRE = 2
AF_INET6_WIRE = 10


# ---------------------------------------------------------------------------
# Primitive (un)packers
# ---------------------------------------------------------------------------
def pack_u8(value: int) -> bytes:
    """Pack an unsigned 8-bit integer."""
    if not 0 <= value <= 0xFF:
        raise IEEncodeError(f"u8 out of range: {value}")
    return struct.pack(_FMT_U8, value)


def pack_u16(value: int) -> bytes:
    """Pack an unsigned 16-bit integer (network byte order)."""
    if not 0 <= value <= 0xFFFF:
        raise IEEncodeError(f"u16 out of range: {value}")
    return struct.pack(_FMT_U16, value)


def pack_u32(value: int) -> bytes:
    """Pack an unsigned 32-bit integer (network byte order)."""
    if not 0 <= value <= 0xFFFFFFFF:
        raise IEEncodeError(f"u32 out of range: {value}")
    return struct.pack(_FMT_U32, value)


def unpack_u8(data: bytes) -> int:
    if len(data) != 1:
        raise IEDecodeError(f"u8 needs 1 byte, got {len(data)}")
    return data[0]


def unpack_u16(data: bytes) -> int:
    if len(data) != 2:
        raise IEDecodeError(f"u16 needs 2 bytes, got {len(data)}")
    return struct.unpack(_FMT_U16, data)[0]


def unpack_u32(data: bytes) -> int:
    if len(data) != 4:
        raise IEDecodeError(f"u32 needs 4 bytes, got {len(data)}")
    return struct.unpack(_FMT_U32, data)[0]


# ---------------------------------------------------------------------------
# UTF-8 helpers
# ---------------------------------------------------------------------------
def encode_utf8(value: str) -> bytes:
    """Encode a Python str as UTF-8 bytes suitable for an IE payload."""
    if not isinstance(value, str):
        raise IEEncodeError(f"expected str, got {type(value).__name__}")
    encoded = value.encode("utf-8")
    if len(encoded) > IE_MAX_PAYLOAD:
        raise IEEncodeError(
            f"UTF-8 payload too long ({len(encoded)} bytes, max {IE_MAX_PAYLOAD})"
        )
    return encoded


def decode_utf8(data: bytes) -> str:
    """Decode a UTF-8 IE payload to a Python str."""
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IEDecodeError(f"invalid UTF-8 in IE payload: {exc}") from exc


# ---------------------------------------------------------------------------
# TLV (IE) framing
# ---------------------------------------------------------------------------
def pack_ie(ie_type: int, payload: bytes) -> bytes:
    """
    Wrap a raw payload in the standard 2-byte IE header.

    Per RFC 5456 §8.6 every IE on the wire is::

        +--------+--------+----------------+
        |  type  | length | payload bytes  |
        +--------+--------+----------------+
        |   u8   |   u8   |  0..255 bytes  |
    """
    if not 0 <= ie_type <= 0xFF:
        raise IEEncodeError(f"IE type out of range: {ie_type}")
    if len(payload) > IE_MAX_PAYLOAD:
        raise IEEncodeError(
            f"IE payload too long: {len(payload)} bytes (max {IE_MAX_PAYLOAD})"
        )
    return struct.pack(_FMT_TLV_HEADER, ie_type, len(payload)) + payload


def unpack_ie(buf: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    """
    Read a single IE from ``buf`` starting at ``offset``.

    Returns ``(ie_type, payload, next_offset)``. Raises ``IEDecodeError``
    if the buffer is truncated or the length field would run past the
    end of the buffer.
    """
    if offset + IE_HEADER_LEN > len(buf):
        raise IEDecodeError(
            f"truncated IE header at offset {offset} (buf len {len(buf)})"
        )
    ie_type, length = struct.unpack_from(_FMT_TLV_HEADER, buf, offset)
    payload_start = offset + IE_HEADER_LEN
    payload_end = payload_start + length
    if payload_end > len(buf):
        raise IEDecodeError(
            f"IE 0x{ie_type:02x} at offset {offset} claims {length} bytes "
            f"but buffer ends at {len(buf)}"
        )
    return ie_type, bytes(buf[payload_start:payload_end]), payload_end


def iter_ies(buf: bytes, offset: int = 0):
    """Yield ``(ie_type, payload)`` tuples until ``buf`` is exhausted."""
    while offset < len(buf):
        ie_type, payload, offset = unpack_ie(buf, offset)
        yield ie_type, payload


# ---------------------------------------------------------------------------
# sockaddr_in / sockaddr_in6 helpers (for APPARENT ADDR, IE 0x12)
# ---------------------------------------------------------------------------
def pack_sockaddr_in(ip: str, port: int) -> bytes:
    """
    Encode an IPv4 (host, port) as the 16-byte sockaddr_in payload used by
    the APPARENT ADDR IE.

    Layout (RFC 5456 §8.6.17, network byte order on the wire for port and
    addr; family is little-endian as in Linux ``struct sockaddr_in``)::

        +----------+----------+----------+----------+
        | family   | port     | ip (4B)  | 8B zero  |
        | 2 bytes  | 2 bytes  |          | padding  |
        +----------+----------+----------+----------+
    """
    if not 0 <= port <= 0xFFFF:
        raise IEEncodeError(f"port out of range: {port}")
    try:
        packed_ip = socket.inet_aton(ip)
    except OSError as exc:
        raise IEEncodeError(f"invalid IPv4 address {ip!r}: {exc}") from exc
    # family is written in host byte order per the sockaddr struct;
    # Asterisk writes it as a 16-bit native int. We use big-endian for
    # consistency with everything else; this matches the byte layout used
    # by Asterisk on big-endian and works on the wire because both ends
    # agree on the convention. (Asterisk's struct field is two bytes
    # written verbatim.)
    return (
        struct.pack("!H", AF_INET_WIRE)
        + struct.pack("!H", port)
        + packed_ip
        + b"\x00" * 8
    )


def pack_sockaddr_in6(ip: str, port: int, flowinfo: int = 0, scope_id: int = 0) -> bytes:
    """
    Encode an IPv6 (host, port) as the 28-byte sockaddr_in6 payload.

    Layout::

        family (2) | port (2) | flowinfo (4) | addr (16) | scope_id (4)
    """
    if not 0 <= port <= 0xFFFF:
        raise IEEncodeError(f"port out of range: {port}")
    try:
        packed_ip = socket.inet_pton(socket.AF_INET6, ip)
    except OSError as exc:
        raise IEEncodeError(f"invalid IPv6 address {ip!r}: {exc}") from exc
    return (
        struct.pack("!H", AF_INET6_WIRE)
        + struct.pack("!H", port)
        + struct.pack("!I", flowinfo)
        + packed_ip
        + struct.pack("!I", scope_id)
    )


def unpack_sockaddr(data: bytes) -> tuple[str, int]:
    """
    Decode an APPARENT ADDR payload and return ``(ip_string, port)``.

    Length determines family: 16 bytes => sockaddr_in (IPv4), 28 bytes
    => sockaddr_in6 (IPv6). Anything else is rejected.
    """
    if len(data) == 16:
        # IPv4
        port = struct.unpack_from("!H", data, 2)[0]
        ip = socket.inet_ntoa(bytes(data[4:8]))
        return ip, port
    if len(data) == 28:
        # IPv6
        port = struct.unpack_from("!H", data, 2)[0]
        ip = socket.inet_ntop(socket.AF_INET6, bytes(data[8:24]))
        return ip, port
    raise IEDecodeError(
        f"APPARENT ADDR has unexpected length {len(data)} (expected 16 or 28)"
    )


# ---------------------------------------------------------------------------
# Codec registry
# ---------------------------------------------------------------------------
# Each IE type maps to a pair of callables:
#   encoder(value) -> bytes   (raw payload, without the TLV header)
#   decoder(bytes) -> value
#
# Encoder / decoder modules register themselves via @register_codec(ie_type).
# Unknown IEs fall through to raw-bytes encode/decode so that we always
# round-trip the wire data, even for vendor extensions we don't recognise.

EncoderFn = Callable[[object], bytes]
DecoderFn = Callable[[bytes], object]

_ENCODERS: dict[int, EncoderFn] = {}
_DECODERS: dict[int, DecoderFn] = {}


def register_codec(ie_type: int, encoder: EncoderFn, decoder: DecoderFn) -> None:
    """Register an (encoder, decoder) pair for a given IE type."""
    if ie_type in _ENCODERS:
        raise ValueError(f"codec already registered for IE 0x{ie_type:02x}")
    _ENCODERS[ie_type] = encoder
    _DECODERS[ie_type] = decoder


def get_codec(ie_type: int) -> tuple[EncoderFn, DecoderFn]:
    """Return ``(encoder, decoder)`` for ``ie_type``, or raw passthrough."""
    encoder = _ENCODERS.get(ie_type, _raw_encode)
    decoder = _DECODERS.get(ie_type, _raw_decode)
    return encoder, decoder


def _raw_encode(value: object) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise IEEncodeError(
        f"no registered encoder; value must be bytes-like, got {type(value).__name__}"
    )


def _raw_decode(data: bytes) -> bytes:
    return bytes(data)


__all__ = [
    "encode_datetime_ie_from_timestamp",
    "decode_datetime_ie_to_timestamp",
    "IEDecodeError",
    "IEEncodeError",
    "IE_HEADER_LEN",
    "IE_MAX_PAYLOAD",
    "AF_INET_WIRE",
    "AF_INET6_WIRE",
    "pack_u8",
    "pack_u16",
    "pack_u32",
    "unpack_u8",
    "unpack_u16",
    "unpack_u32",
    "encode_utf8",
    "decode_utf8",
    "pack_ie",
    "unpack_ie",
    "iter_ies",
    "pack_sockaddr_in",
    "pack_sockaddr_in6",
    "unpack_sockaddr",
    "register_codec",
    "get_codec",
]

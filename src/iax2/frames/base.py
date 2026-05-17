"""Abstract Frame and header (de)serialisation.
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later
"""
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
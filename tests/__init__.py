"""Tests for Information Element encode/decode."""
import time
from datetime import datetime, timezone

import pytest

from iax2.frames.base import (
    decode_datetime_ie_to_timestamp,
    encode_datetime_ie_from_timestamp,
)


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------
def test_round_trip_current_time():
    """Encoding then decoding should return the original timestamp
    (rounded to the format's 2-second resolution)."""
    now = (int(time.time()) // 2) * 2  # even second
    encoded = encode_datetime_ie_from_timestamp(now)
    assert len(encoded) == 4
    decoded = decode_datetime_ie_to_timestamp(encoded)
    assert decoded == now


def test_round_trip_known_timestamp():
    """Pin a specific date and verify exact bytes."""
    # 2024-01-15 13:30:46 UTC -> year=24, mon=1, mday=15, h=13, m=30, s=46
    dt = datetime(2024, 1, 15, 13, 30, 46, tzinfo=timezone.utc)
    ts = dt.timestamp()
    encoded = encode_datetime_ie_from_timestamp(ts)

    # Hand-computed expected bits:
    # year=24 (0x18) << 25 | mon=1 << 21 | mday=15 << 16
    #   | hour=13 << 11 | min=30 << 5 | sec=46//2=23
    expected = (
        (24 << 25) | (1 << 21) | (15 << 16)
        | (13 << 11) | (30 << 5) | 23
    ).to_bytes(4, "big")
    assert encoded == expected

    # And it should decode back to the original (within 2s resolution)
    decoded = decode_datetime_ie_to_timestamp(encoded)
    assert decoded == ts  # 46 is even, so no rounding loss


# ---------------------------------------------------------------------------
# Edge cases — parametrized to cover many at once
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "year, month, day, hour, minute, second",
    [
        (2000, 1, 1, 0, 0, 0),      # epoch of the format
        (2127, 12, 31, 23, 59, 58), # last representable moment
        (2024, 2, 29, 12, 0, 0),    # leap day
        (2024, 6, 15, 23, 59, 58),  # max even second
    ],
)
def test_round_trip_edges(year, month, day, hour, minute, second):
    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    ts = dt.timestamp()
    decoded = decode_datetime_ie_to_timestamp(
        encode_datetime_ie_from_timestamp(ts)
    )
    assert decoded == ts


def test_odd_seconds_truncate_to_even():
    """The format has 2-second resolution; odd seconds round down."""
    dt = datetime(2024, 6, 15, 12, 0, 45, tzinfo=timezone.utc)
    decoded = decode_datetime_ie_to_timestamp(
        encode_datetime_ie_from_timestamp(dt.timestamp())
    )
    # 45 -> stored as 22 -> decoded as 44
    assert decoded == dt.replace(second=44).timestamp()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
def test_decode_rejects_wrong_length():
    with pytest.raises(ValueError, match="must be 4 bytes"):
        decode_datetime_ie_to_timestamp(b"\x00\x00\x00")


def test_encode_rejects_year_before_2000():
    pre_epoch = datetime(1999, 12, 31, tzinfo=timezone.utc).timestamp()
    with pytest.raises(ValueError, match="out of range"):
        encode_datetime_ie_from_timestamp(pre_epoch)


def test_encode_rejects_year_after_2127():
    too_far = datetime(2128, 1, 1, tzinfo=timezone.utc).timestamp()
    with pytest.raises(ValueError, match="out of range"):
        encode_datetime_ie_from_timestamp(too_far)
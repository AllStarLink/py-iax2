"""Unit tests for constants, formats, IEs and frames."""

from __future__ import annotations

import datetime as dt
import struct

import pytest

from iax2.constants import IE, ControlType, FrameType, IAXCommand
from iax2.exceptions import FrameError, IEError
from iax2.formats import (
    Format,
    best_format,
    choose_format,
    decode_codec_prefs,
    encode_codec_prefs,
    format_by_name,
    format_info,
    format_names,
)
from iax2.frames import (
    FullFrame,
    MiniFrame,
    TrunkEntry,
    TrunkFrame,
    VideoFrame,
    compress_subclass,
    parse_datagram,
    peek_call_numbers,
    uncompress_subclass,
)
from iax2.ies import IEs, datetime_from_ie, datetime_to_ie, decode_addr, encode_addr

# --------------------------------------------------------------------------- subclass


@pytest.mark.parametrize("value", [0, 1, 0x7F, 0x80, 1 << 12, 1 << 34, 1 << 63])
def test_subclass_round_trip(value):
    assert uncompress_subclass(compress_subclass(value)) == value


def test_subclass_examples():
    assert compress_subclass(IAXCommand.NEW) == 1
    assert compress_subclass(Format.ULAW) == 4  # < 0x80, sent raw
    assert compress_subclass(Format.G722) == 0x80 | 12
    assert compress_subclass(-1) == 0xFF
    assert uncompress_subclass(0xFF) == -1


def test_subclass_uncompressible():
    with pytest.raises(FrameError):
        compress_subclass(0x180)


# --------------------------------------------------------------------------- formats


def test_format_info():
    info = format_info(Format.GSM)
    assert info.name == "gsm"
    assert info.samples_for_bytes(66) == 320
    assert format_info(Format.ULAW).bytes_for_ms(20) == 160
    assert format_info(Format.SLIN).bytes_for_ms(20) == 320
    assert format_info(Format.G722).bytes_for_ms(20) == 160
    assert format_by_name("ULAW") is Format.ULAW
    assert format_names(Format.ULAW | Format.GSM) == ["gsm", "ulaw"]


def test_codec_prefs_match_asterisk_table():
    # ulaw is index 2 -> order 3 -> 'D'; gsm order 2 -> 'C'; g722 order 14 -> 'O'
    assert encode_codec_prefs([Format.ULAW, Format.GSM, Format.G722]) == "DCO"
    assert decode_codec_prefs("DCO") == [Format.ULAW, Format.GSM, Format.G722]
    assert encode_codec_prefs([Format.OPUS]) == "f"  # order 37
    assert decode_codec_prefs("D?C") == [Format.ULAW, Format.GSM]


def test_best_and_choose():
    assert best_format(Format.GSM | Format.SLIN | Format.ALAW) == Format.ALAW
    assert best_format(Format.H264) == 0
    caps = Format.ULAW | Format.GSM | Format.ALAW
    # host priority: our prefs win over the requested format
    assert choose_format(caps, Format.GSM, caps, our_prefs=[Format.ALAW, Format.GSM]) == Format.ALAW
    # caller priority with caller prefs
    assert (
        choose_format(
            caps, 0, caps, our_prefs=[Format.ALAW], peer_prefs=[Format.GSM], caller_first=True
        )
        == Format.GSM
    )
    # our prefs miss -> caller prefs -> best
    assert (
        choose_format(caps, 0, caps, our_prefs=[Format.G729], peer_prefs=[Format.GSM]) == Format.GSM
    )
    assert choose_format(caps, 0, caps, our_prefs=[Format.G729]) == Format.ULAW
    # prefs disabled: requested format if supported
    assert choose_format(caps, Format.GSM, caps, use_prefs=False) == Format.GSM
    assert choose_format(caps, Format.G729, caps, use_prefs=False) == Format.ULAW
    assert choose_format(caps, Format.G729, caps, requested_only=True) == 0
    assert choose_format(Format.G729, 0, Format.ULAW) == 0


# --------------------------------------------------------------------------- IEs


def test_ie_round_trip_kinds():
    when = dt.datetime(2026, 10, 19, 14, 31, 58)
    ies = IEs.build(
        (IE.USERNAME, "alice"),
        (IE.VERSION, 2),
        (IE.CAPABILITY, 0xFFFF),
        (IE.CAPABILITY2, 1 << 34),
        (IE.CAUSECODE, 16),
        (IE.AUTOANSWER, True),
        (IE.CALLTOKEN, b""),
        (IE.APPARENT_ADDR, ("192.0.2.7", 4569)),
        (IE.DATETIME, when),
        (IE.VARIABLE, ("FOO", "bar")),
    )
    parsed = IEs.from_bytes(ies.to_bytes())
    assert parsed == ies
    assert parsed.get(IE.USERNAME) == "alice"
    assert parsed.get(IE.VERSION) == 2
    assert parsed.capability == 1 << 34  # CAPABILITY2 wins
    assert parsed.get(IE.AUTOANSWER) is True
    assert parsed.calltoken == b""
    assert parsed.get(IE.APPARENT_ADDR) == ("192.0.2.7", 4569)
    assert parsed.get(IE.DATETIME) == when
    assert parsed.variables == {"FOO": "bar"}
    assert IE.USERNAME in parsed and IE.PASSWORD not in parsed


def test_ie_wire_bytes():
    assert IEs().add(IE.USERNAME, "ab").to_bytes() == b"\x06\x02ab"
    assert IEs().add(IE.VERSION, 2).to_bytes() == b"\x0b\x02\x00\x02"
    assert IEs().add(IE.FORMAT2, 4).to_bytes() == b"\x38\x09\x00" + struct.pack("!Q", 4)


def test_ie_keyword_build_skips_none():
    ies = IEs.build(username="bob", called_context=None, version=2)
    assert [t for t, _ in ies] == [IE.USERNAME, IE.VERSION]


def test_ie_parse_errors():
    with pytest.raises(IEError):
        IEs.from_bytes(b"\x06\x05ab")  # overrun
    with pytest.raises(IEError):
        IEs.from_bytes(b"\x06\x02ab\x01")  # stray byte


def test_ie_bad_length_is_ignored_like_asterisk():
    ies = IEs.from_bytes(b"\x0b\x01\x02")  # VERSION must be 2 bytes
    assert ies.get(IE.VERSION) is None
    assert ies.raw(IE.VERSION) == b"\x02"


def test_ie_last_occurrence_wins_and_variables_concat():
    ies = IEs().add(IE.USERNAME, "a").add(IE.USERNAME, "b")
    assert ies.get(IE.USERNAME) == "b"
    v = IEs().add(IE.VARIABLE, "X=12").add(IE.VARIABLE, "X=34")
    assert v.variables == {"X": "1234"}


def test_ie_too_long():
    with pytest.raises(IEError):
        IEs().add(IE.CAUSE, "x" * 256)


def test_unknown_ie_preserved():
    raw = b"\xc8\x03xyz"
    ies = IEs.from_bytes(raw)
    assert ies.to_bytes() == raw
    assert ies.get(200) == b"xyz"


def test_datetime_packing():
    when = dt.datetime(2026, 1, 2, 3, 4, 6)
    v = datetime_to_ie(when)
    assert (v >> 25) == 26 and ((v >> 21) & 0xF) == 1
    assert datetime_from_ie(v) == when
    assert datetime_from_ie(datetime_to_ie(dt.datetime(2026, 1, 2, 3, 4, 7))).second == 6
    with pytest.raises(IEError):
        datetime_from_ie(0)  # month 0


def test_addr_encoding_matches_linux_sockaddr():
    raw = encode_addr("10.1.2.3", 4569)
    assert raw == b"\x02\x00\x11\xd9\x0a\x01\x02\x03" + bytes(8)
    assert decode_addr(raw) == ("10.1.2.3", 4569)
    # network-order family (RFC style) also accepted
    assert decode_addr(b"\x00\x02" + raw[2:]) == ("10.1.2.3", 4569)
    raw6 = encode_addr("2001:db8::1", 4570)
    assert len(raw6) == 28 and decode_addr(raw6) == ("2001:db8::1", 4570)
    with pytest.raises(IEError):
        decode_addr(b"\x00" * 5)


# --------------------------------------------------------------------------- frames


def test_full_frame_wire_layout():
    f = FullFrame.iax(
        IAXCommand.NEW,
        IEs().add(IE.VERSION, 2),
        source_call=0x123,
        dest_call=0,
        timestamp=0x01020304,
        oseqno=5,
        iseqno=6,
    )
    raw = f.to_bytes()
    assert raw[:12] == bytes.fromhex("8123 0000 01020304 05 06 06 01".replace(" ", ""))
    back = parse_datagram(raw)
    assert back == f
    assert back.command is IAXCommand.NEW
    assert back.ies.get(IE.VERSION) == 2


def test_full_frame_retrans_and_enums():
    f = FullFrame.control(ControlType.ANSWER, source_call=1, dest_call=2, retransmission=True)
    raw = f.to_bytes()
    assert raw[2] & 0x80
    back = FullFrame.from_bytes(raw)
    assert back.retransmission and back.dest_call == 2
    assert back.subclass is ControlType.ANSWER
    assert back.frame_type is FrameType.CONTROL


def test_full_frame_voice_g722():
    f = FullFrame(FrameType.VOICE, Format.G722, source_call=7, dest_call=9, payload=b"\x00" * 160)
    raw = f.to_bytes()
    assert raw[11] == 0x8C
    assert FullFrame.from_bytes(raw).subclass == Format.G722


def test_full_frame_video_mark():
    f = FullFrame(FrameType.VIDEO, Format.H264, source_call=7, dest_call=9, mark=True)
    raw = f.to_bytes()
    assert raw[11] == 0x80 | 0x40 | 21
    back = FullFrame.from_bytes(raw)
    assert back.mark and back.subclass == Format.H264


def test_full_frame_errors():
    with pytest.raises(FrameError):
        FullFrame.from_bytes(b"\x80\x01" + b"\x00" * 5)
    with pytest.raises(FrameError):
        FullFrame(FrameType.IAX, 1, source_call=0x8000).to_bytes()


def test_mini_frame():
    m = MiniFrame(0x1234, 0xABCD, b"audio")
    raw = m.to_bytes()
    assert raw == b"\x12\x34\xab\xcdaudio"
    assert parse_datagram(raw) == m
    with pytest.raises(FrameError):
        MiniFrame(0, 1).to_bytes()


def test_video_frame():
    v = VideoFrame(5, 0x1234, True, b"vid")
    raw = v.to_bytes()
    assert raw[:6] == b"\x00\x00\x80\x05\x92\x34"
    assert parse_datagram(raw) == v


@pytest.mark.parametrize("with_ts", [False, True])
def test_trunk_frame(with_ts):
    t = TrunkFrame(
        123456,
        [
            TrunkEntry(3, b"aaaa", 10 if with_ts else None),
            TrunkEntry(4, b"bb", 20 if with_ts else None),
        ],
        with_ts,
    )
    raw = t.to_bytes()
    assert raw[:4] == bytes((0, 0, 1, 1 if with_ts else 0))
    assert parse_datagram(raw) == t


def test_trunk_truncated_entry_dropped():
    t = TrunkFrame(1, [TrunkEntry(3, b"aaaa"), TrunkEntry(4, b"bbbb")])
    back = TrunkFrame.from_bytes(t.to_bytes()[:-2])
    assert back.entries == [TrunkEntry(3, b"aaaa")]


def test_peek():
    assert peek_call_numbers(FullFrame(6, 2, source_call=3, dest_call=4).to_bytes()) == (
        "full",
        3,
        4,
    )
    assert peek_call_numbers(b"\x00\x05\x00\x00") == ("mini", 5, 0)
    assert peek_call_numbers(b"\x00\x00\x80\x05\x00\x00") == ("video", 5, 0)
    assert peek_call_numbers(b"\x00\x00\x01\x00") == ("meta", 0, 0)
    assert peek_call_numbers(b"\x00") == ("invalid", 0, 0)
    with pytest.raises(FrameError):
        parse_datagram(b"\x00\x00\x07\x00")


def test_describe():
    f = FullFrame.iax(IAXCommand.PING, source_call=1, dest_call=2, timestamp=3)
    assert "IAX/PING 1->2 ts=3" in f.describe()

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Protocol constants for IAX2.

Numeric values are taken from RFC 5456 and, where Asterisk extends the
protocol, from Asterisk's ``channels/iax2/include/iax2.h`` and
``include/asterisk/frame.h`` so that this library is wire-compatible with
Asterisk 22 LTS.

All enumerations are :class:`enum.IntEnum` (or :class:`enum.IntFlag` for
bit fields) so they can be used anywhere a plain ``int`` is expected.
"""

from __future__ import annotations

import enum

__all__ = [
    "DEFAULT_PORT",
    "DEFAULT_REG_EXPIRE",
    "IAX_FLAG_FULL",
    "IAX_FLAG_RETRANS",
    "IAX_FLAG_SC_LOG",
    "IAX_MAX_CALLS",
    "IAX_MAX_SHIFT",
    "IAX_PROTO_VERSION",
    "IAX_WINDOW",
    "IE",
    "AuthMethod",
    "Cause",
    "ControlType",
    "DPStatus",
    "Encryption",
    "FrameType",
    "IAXCommand",
    "MetaCommand",
    "SamplingRate",
]

#: IAX protocol version spoken by this library (sent in the VERSION IE).
IAX_PROTO_VERSION = 2
#: Default UDP port for IAX2 (RFC 5456 §2).
DEFAULT_PORT = 4569
#: Default registration refresh interval in seconds (Asterisk default).
DEFAULT_REG_EXPIRE = 60
#: Largest call number permitted by the 15-bit call number field.
IAX_MAX_CALLS = 32768
#: High bit of the first 16-bit word, set on full frames.
IAX_FLAG_FULL = 0x8000
#: High bit of the destination call number, set on retransmitted full frames.
IAX_FLAG_RETRANS = 0x8000
#: High bit of the subclass byte, meaning the subclass is a power-of-two exponent.
IAX_FLAG_SC_LOG = 0x80
#: Mask of the exponent in a compressed subclass.
IAX_MAX_SHIFT = 0x3F
#: Size of the Asterisk sequence-number window.
IAX_WINDOW = 64


class FrameType(enum.IntEnum):
    """Full-frame type byte (RFC 5456 §8.2; numbering follows Asterisk)."""

    DTMF_END = 0x01
    """DTMF digit released (called "DTMF" in the RFC). Subclass is the ASCII digit."""
    VOICE = 0x02
    """Audio payload. Subclass is the codec format bit."""
    VIDEO = 0x03
    """Video payload. Subclass is the codec format bit (bit 0 = mark)."""
    CONTROL = 0x04
    """Session control (ringing, answer, hangup, ...). See :class:`ControlType`."""
    NULL = 0x05
    """Empty frame."""
    IAX = 0x06
    """IAX protocol control message. See :class:`IAXCommand`."""
    TEXT = 0x07
    """Text message; payload is UTF-8 text."""
    IMAGE = 0x08
    """Still image; subclass is the image format bit."""
    HTML = 0x09
    """HTML/URL message. Subclass is an HTML sub-command."""
    CNG = 0x0A
    """Comfort noise; subclass/first byte is the noise level in -dBov."""
    MODEM = 0x0B
    """Modem-over-IP data (T.38 / V.150)."""
    DTMF_BEGIN = 0x0C
    """DTMF digit pressed (Asterisk extension)."""


class IAXCommand(enum.IntEnum):
    """Subclass values for :attr:`FrameType.IAX` frames (RFC 5456 §8.4)."""

    NEW = 1
    PING = 2
    PONG = 3
    ACK = 4
    HANGUP = 5
    REJECT = 6
    ACCEPT = 7
    AUTHREQ = 8
    AUTHREP = 9
    INVAL = 10
    LAGRQ = 11
    LAGRP = 12
    REGREQ = 13
    REGAUTH = 14
    REGACK = 15
    REGREJ = 16
    REGREL = 17
    VNAK = 18
    DPREQ = 19
    DPREP = 20
    DIAL = 21
    TXREQ = 22
    TXCNT = 23
    TXACC = 24
    TXREADY = 25
    TXREL = 26
    TXREJ = 27
    QUELCH = 28
    UNQUELCH = 29
    POKE = 30
    PAGE = 31
    MWI = 32
    UNSUPPORT = 33
    TRANSFER = 34
    PROVISION = 35
    FWDOWNL = 36
    FWDATA = 37
    TXMEDIA = 38
    RTKEY = 39
    CALLTOKEN = 40


class ControlType(enum.IntEnum):
    """Subclass values for :attr:`FrameType.CONTROL` frames.

    Only the subset that Asterisk forwards over IAX2 is listed in
    :data:`IAX_ALLOWED_CONTROLS`; other values are dropped by Asterisk.
    """

    HANGUP = 1
    RING = 2
    RINGING = 3
    ANSWER = 4
    BUSY = 5
    TAKEOFFHOOK = 6
    OFFHOOK = 7
    CONGESTION = 8
    FLASH = 9
    WINK = 10
    OPTION = 11
    RADIO_KEY = 12
    RADIO_UNKEY = 13
    PROGRESS = 14
    PROCEEDING = 15
    HOLD = 16
    UNHOLD = 17
    VIDUPDATE = 18
    SRCUPDATE = 20
    TRANSFER = 21
    CONNECTED_LINE = 22
    REDIRECTING = 23
    T38_PARAMETERS = 24
    CC = 25
    SRCCHANGE = 26
    READ_ACTION = 27
    AOC = 28
    END_OF_Q = 29
    INCOMPLETE = 30
    MCID = 31
    UPDATE_RTP_PEER = 32
    PVT_CAUSE_CODE = 33


#: Control subclasses Asterisk will send or accept over IAX2
#: (``iax2_is_control_frame_allowed()`` in chan_iax2.c).
IAX_ALLOWED_CONTROLS: frozenset[int] = frozenset(
    {
        ControlType.HANGUP,
        ControlType.RING,
        ControlType.RINGING,
        ControlType.ANSWER,
        ControlType.BUSY,
        ControlType.TAKEOFFHOOK,
        ControlType.OFFHOOK,
        ControlType.CONGESTION,
        ControlType.FLASH,
        ControlType.WINK,
        ControlType.OPTION,
        ControlType.RADIO_KEY,
        ControlType.RADIO_UNKEY,
        ControlType.PROGRESS,
        ControlType.PROCEEDING,
        ControlType.HOLD,
        ControlType.UNHOLD,
        ControlType.VIDUPDATE,
        ControlType.CONNECTED_LINE,
        ControlType.REDIRECTING,
        ControlType.T38_PARAMETERS,
        ControlType.AOC,
        ControlType.INCOMPLETE,
        ControlType.MCID,
    }
)
__all__.append("IAX_ALLOWED_CONTROLS")


class IE(enum.IntEnum):
    """Information Element type codes (RFC 5456 §8.6, plus Asterisk)."""

    CALLED_NUMBER = 1
    CALLING_NUMBER = 2
    CALLING_ANI = 3
    CALLING_NAME = 4
    CALLED_CONTEXT = 5
    USERNAME = 6
    PASSWORD = 7
    CAPABILITY = 8
    FORMAT = 9
    LANGUAGE = 10
    VERSION = 11
    ADSICPE = 12
    DNID = 13
    AUTHMETHODS = 14
    CHALLENGE = 15
    MD5_RESULT = 16
    RSA_RESULT = 17
    APPARENT_ADDR = 18
    REFRESH = 19
    DPSTATUS = 20
    CALLNO = 21
    CAUSE = 22
    IAX_UNKNOWN = 23
    MSGCOUNT = 24
    AUTOANSWER = 25
    MUSICONHOLD = 26
    TRANSFERID = 27
    RDNIS = 28
    PROVISIONING = 29
    AESPROVISIONING = 30
    DATETIME = 31
    DEVICETYPE = 32
    SERVICEIDENT = 33
    FIRMWAREVER = 34
    FWBLOCKDESC = 35
    FWBLOCKDATA = 36
    PROVVER = 37
    CALLINGPRES = 38
    CALLINGTON = 39
    CALLINGTNS = 40
    SAMPLINGRATE = 41
    CAUSECODE = 42
    ENCRYPTION = 43
    ENCKEY = 44
    CODEC_PREFS = 45
    RR_JITTER = 46
    RR_LOSS = 47
    RR_PKTS = 48
    RR_DELAY = 49
    RR_DROPPED = 50
    RR_OOO = 51
    VARIABLE = 52
    OSPTOKEN = 53
    CALLTOKEN = 54
    CAPABILITY2 = 55
    FORMAT2 = 56
    CALLINGANI2 = 57


class AuthMethod(enum.IntFlag):
    """Bit flags carried in the AUTHMETHODS IE."""

    NONE = 0
    PLAINTEXT = 1 << 0
    MD5 = 1 << 1
    RSA = 1 << 2


class Encryption(enum.IntFlag):
    """Bit flags carried in the ENCRYPTION IE."""

    NONE = 0
    AES128 = 1 << 0
    KEYROTATE = 1 << 15


class MetaCommand(enum.IntEnum):
    """Meta-frame command byte (RFC 5456 §8.1.3 / Asterisk)."""

    TRUNK = 1
    VIDEO = 2


#: Trunk meta-frame command data for "classic" (callno, len) entries.
META_TRUNK_SUPERMINI = 0
#: Trunk meta-frame command data for (len, mini-frame) entries with timestamps.
META_TRUNK_MINI = 1
__all__ += ["META_TRUNK_MINI", "META_TRUNK_SUPERMINI"]


class SamplingRate(enum.IntFlag):
    """Bit flags carried in the SAMPLINGRATE IE."""

    RATE_8KHZ = 1 << 0
    RATE_11KHZ = 1 << 1
    RATE_16KHZ = 1 << 2
    RATE_22KHZ = 1 << 3
    RATE_44KHZ = 1 << 4
    RATE_48KHZ = 1 << 5


class DPStatus(enum.IntFlag):
    """Bit flags carried in the DPSTATUS IE (dialplan query replies)."""

    EXISTS = 1 << 0
    CANEXIST = 1 << 1
    NONEXISTENT = 1 << 2
    IGNOREPAT = 1 << 14
    MATCHMORE = 1 << 15


class HTMLCommand(enum.IntEnum):
    """Subclass values for :attr:`FrameType.HTML` frames."""

    URL = 1
    DATA = 2
    BEGIN = 4
    END = 8
    LDCOMPLETE = 16
    NOSUPPORT = 17
    LINKURL = 18
    UNLINK = 19
    LINKREJECT = 20


__all__.append("HTMLCommand")


class Cause(enum.IntEnum):
    """Q.931 hangup cause codes carried in the CAUSECODE IE."""

    NOTDEFINED = 0
    UNALLOCATED = 1
    NO_ROUTE_TRANSIT_NET = 2
    NO_ROUTE_DESTINATION = 3
    MISDIALLED_TRUNK_PREFIX = 5
    CHANNEL_UNACCEPTABLE = 6
    CALL_AWARDED_DELIVERED = 7
    PRE_EMPTED = 8
    NUMBER_PORTED_NOT_HERE = 14
    NORMAL_CLEARING = 16
    USER_BUSY = 17
    NO_USER_RESPONSE = 18
    NO_ANSWER = 19
    SUBSCRIBER_ABSENT = 20
    CALL_REJECTED = 21
    NUMBER_CHANGED = 22
    REDIRECTED_TO_NEW_DESTINATION = 23
    ANSWERED_ELSEWHERE = 26
    DESTINATION_OUT_OF_ORDER = 27
    INVALID_NUMBER_FORMAT = 28
    FACILITY_REJECTED = 29
    RESPONSE_TO_STATUS_ENQUIRY = 30
    NORMAL_UNSPECIFIED = 31
    NORMAL_CIRCUIT_CONGESTION = 34
    NETWORK_OUT_OF_ORDER = 38
    NORMAL_TEMPORARY_FAILURE = 41
    SWITCH_CONGESTION = 42
    ACCESS_INFO_DISCARDED = 43
    REQUESTED_CHAN_UNAVAIL = 44
    FACILITY_NOT_SUBSCRIBED = 50
    OUTGOING_CALL_BARRED = 52
    INCOMING_CALL_BARRED = 54
    BEARERCAPABILITY_NOTAUTH = 57
    BEARERCAPABILITY_NOTAVAIL = 58
    BEARERCAPABILITY_NOTIMPL = 65
    CHAN_NOT_IMPLEMENTED = 66
    FACILITY_NOT_IMPLEMENTED = 69
    INVALID_CALL_REFERENCE = 81
    INCOMPATIBLE_DESTINATION = 88
    INVALID_MSG_UNSPECIFIED = 95
    MANDATORY_IE_MISSING = 96
    MESSAGE_TYPE_NONEXIST = 97
    WRONG_MESSAGE = 98
    IE_NONEXIST = 99
    INVALID_IE_CONTENTS = 100
    WRONG_CALL_STATE = 101
    RECOVERY_ON_TIMER_EXPIRE = 102
    MANDATORY_IE_LENGTH_ERROR = 103
    PROTOCOL_ERROR = 111
    INTERWORKING = 127

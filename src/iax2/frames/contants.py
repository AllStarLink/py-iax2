"""Information Element encode/decode.
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from enum import IntEnum

class InformationElements(IntEnum):
    CALLED_NUMBER   = 0x01
    CALLING_NUMBER  = 0x02
    CALLING_ANI     = 0x03
    CALLING_NAME    = 0x04
    CALLED_CONTEXT  = 0x05
    USERNAME        = 0x06
    PASSWORD        = 0x07
    CAPABILITY      = 0x08
    FORMAT          = 0x09
    LANGUAGE        = 0x0A
    VERSION         = 0x0B
    ADSICPE         = 0x0C
    DNID            = 0x0D
    AUTHMETHODS     = 0x0E
    CHALLENGE       = 0x0F
    MD5_RESULT      = 0x10
    RSA_RESULT      = 0x11
    APPARENT_ADDR   = 0x12
    REFRESH         = 0x13
    DPSTATUS        = 0x14
    CALLNO          = 0x15
    CAUSE           = 0x16
    IAX_UNKNOWN     = 0x17
    MSGCOUNT        = 0x18
    AUTOANSWER      = 0x19
    MUSICONHOLD     = 0x1A
    TRANSFERID      = 0x1B
    RDNIS           = 0x1C
    DATETIME        = 0x1F
    CALLINGPRES     = 0x26
    CALLINGTON      = 0x27
    CALLINGTNS      = 0x28
    SAMPLINGRATE    = 0x29
    CAUSECODE       = 0x2A
    ENCRYPTION      = 0x2B
    ENCKEY          = 0x2C
    CODEC_PREFS     = 0x2D
    RR_JITTER       = 0x2E
    RR_LOSS         = 0x2F
    RR_PKTS         = 0x30
    RR_DELAY        = 0x31
    RR_DROPPED      = 0x32
    RR_OOO          = 0x33
    OSPTOKEN        = 0x34
    CALLTOKEN       = 0x36   # Asterisk extension (not in RFC 5456/5457)

class Commands(IntEnum):
    """
    IAX Control Frame subclass values.

    These are the subclass values used when a Full Frame has frametype
    0x06 (IAX Control). Defined in RFC 5456 Section 8.4; initial
    assignments registered in RFC 5457 Section 2.4.

    Valid range: 0x01-0x7F.
    """
    NEW       = 0x01  # Initiate a new call
    PING      = 0x02  # Ping request
    PONG      = 0x03  # Ping or poke reply
    ACK       = 0x04  # Explicit acknowledgment
    HANGUP    = 0x05  # Initiate call tear-down
    REJECT    = 0x06  # Reject a call
    ACCEPT    = 0x07  # Accept a call
    AUTHREQ   = 0x08  # Authentication request
    AUTHREP   = 0x09  # Authentication reply
    INVAL     = 0x0A  # Invalid message
    LAGRQ     = 0x0B  # Lag request
    LAGRP     = 0x0C  # Lag reply
    REGREQ    = 0x0D  # Registration request
    REGAUTH   = 0x0E  # Registration authentication
    REGACK    = 0x0F  # Registration acknowledgement
    REGREJ    = 0x10  # Registration reject
    REGREL    = 0x11  # Registration release
    VNAK      = 0x12  # Video/Voice retransmit request
    DPREQ     = 0x13  # Dialplan request
    DPREP     = 0x14  # Dialplan reply
    DIAL      = 0x15  # Dial
    TXREQ     = 0x16  # Transfer request
    TXCNT     = 0x17  # Transfer connect
    TXACC     = 0x18  # Transfer accept
    TXREADY   = 0x19  # Transfer ready
    TXREL     = 0x1A  # Transfer release
    TXREJ     = 0x1B  # Transfer reject
    QUELCH    = 0x1C  # Halt audio/video [media] transmission
    UNQUELCH  = 0x1D  # Resume audio/video [media] transmission
    POKE      = 0x1E  # Poke request
    # 0x1F reserved
    MWI       = 0x20  # Message waiting indication
    UNSUPPORT = 0x21  # Unsupported message
    TRANSFER  = 0x22  # Remote transfer request
    # 0x23 - 0x25 reserved
    # ---- Asterisk extensions (not in RFC 5456/5457) ---------------------
    # These are widely deployed via Asterisk but were never registered
    # with IANA. Include only if you need Asterisk interop.
    PROVISION = 0x23  # Provision device
    FWDOWNL   = 0x24  # Firmware download
    FWDATA    = 0x25  # Firmware data
    TXMEDIA   = 0x26  # Transfer media (path-optimized transfer)
    RTKEY     = 0x27  # Rotate encryption key
    CALLTOKEN = 0x28  # Call token (DoS mitigation handshake)
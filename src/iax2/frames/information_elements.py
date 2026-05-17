"""Information Element encode/decode.
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from enum import IntEnum

class IAX_IE(IntEnum):
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
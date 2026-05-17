"""Call object (scall/dcall, peer info).
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later
"""

import struct

CALL_NUMBER_MASK = 0x7FFF  # low 15 bits; bit 15 is the "F" / reply flag

def decode_iax_call(msg: bytes, offset: int) -> int:
    """Decode the 15-bit call number at `offset`, masking off bit 15."""
    return struct.unpack_from("!H", msg, offset)[0] & CALL_NUMBER_MASK
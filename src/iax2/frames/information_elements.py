"""
Author: Jason McCormick N8EI <jason@mfamily.org>

This code is licensed under the GNU AFFERO GENERAL PUBLIC LICENSE v3
SPDX-License-Identifier: AGPL-3.0-or-later

pyiax2.frames.information_elements
==================================

Encoders and decoders for every IAX2 Information Element.

Coverage:
    * RFC 5456 §8.6 (IE 0x01–0x34) — the standard IEs
    * Asterisk extensions from channels/iax2/include/iax2.h:
        - 0x1D PROVISIONING      (provisioning data)
        - 0x1E AESPROVISIONING   (encrypted provisioning data)
        - 0x20 DEVICETYPE        (string)
        - 0x21 SERVICEIDENT      (string)
        - 0x22 FIRMWAREVER       (u16)
        - 0x23 FWBLOCKDESC       (u32)
        - 0x24 FWBLOCKDATA       (raw)
        - 0x25 PROVVER           (u32)
        - 0x34 VARIABLE          (string "name=value")
        - 0x36 CALLTOKEN         (opaque DoS-mitigation token)
        - 0x37 CAPABILITY2       (u8 version + iax2_format[] u64)
        - 0x38 FORMAT2           (u8 version + iax2_format[] u64)
        - 0x39 CALLINGANI2       (u32 ANI II digits)

Each IE has two functions:
    encode_<name>(value)    -> bytes  (full IE incl. TLV header)
    decode_<name>(payload)  -> Python value (TLV header already stripped)

Generic facades sit at the bottom:
    encode_ie(ie_type, value)  -> bytes
    decode_ie(buf, offset)     -> (ie_type, value, next_offset)
    iter_decoded_ies(buf)      -> yields (ie_type, value)
"""

from __future__ import annotations

import struct
from typing import Iterator

from .base import (
    IEDecodeError,
    IEEncodeError,
    decode_utf8,
    encode_utf8,
    get_codec,
    pack_ie,
    pack_sockaddr_in,
    pack_sockaddr_in6,
    pack_u16,
    pack_u32,
    pack_u8,
    register_codec,
    unpack_ie,
    unpack_sockaddr,
    unpack_u16,
    unpack_u32,
    unpack_u8,
)
from ..constants import IAX_IE  # the IntEnum built earlier


# ---------------------------------------------------------------------------
# 8.6.1  CALLED NUMBER (0x01) — UTF-8
# 8.6.2  CALLING NUMBER (0x02) — UTF-8
# 8.6.3  CALLING ANI    (0x03) — UTF-8
# 8.6.4  CALLING NAME   (0x04) — UTF-8
# 8.6.5  CALLED CONTEXT (0x05) — UTF-8
# 8.6.6  USERNAME       (0x06) — UTF-8
# 8.6.7  PASSWORD       (0x07) — UTF-8
# 8.6.9  LANGUAGE       (0x0A) — UTF-8
# 8.6.12 DNID           (0x0D) — UTF-8
# 8.6.14 CHALLENGE      (0x0F) — UTF-8 (challenge ascii)
# 8.6.15 MD5 RESULT     (0x10) — UTF-8 (hex digest)
# 8.6.16 RSA RESULT     (0x11) — UTF-8 (sig)
# 8.6.21 CAUSE          (0x16) — UTF-8
# 8.6.27 RDNIS          (0x1C) — UTF-8
# 8.6.35 CODEC PREFS    (0x2D) — UTF-8 (asterisk codec pref string)
# ---------------------------------------------------------------------------
def _string_codec(ie_type: int):
    """Build encode/decode pair for a string-valued IE."""

    def encode(value: str) -> bytes:
        return pack_ie(ie_type, encode_utf8(value))

    def decode(payload: bytes) -> str:
        return decode_utf8(payload)

    return encode, decode


encode_called_number,  decode_called_number  = _string_codec(IAX_IE.CALLED_NUMBER)
encode_calling_number, decode_calling_number = _string_codec(IAX_IE.CALLING_NUMBER)
encode_calling_ani,    decode_calling_ani    = _string_codec(IAX_IE.CALLING_ANI)
encode_calling_name,   decode_calling_name   = _string_codec(IAX_IE.CALLING_NAME)
encode_called_context, decode_called_context = _string_codec(IAX_IE.CALLED_CONTEXT)
encode_username,       decode_username       = _string_codec(IAX_IE.USERNAME)
encode_password,       decode_password       = _string_codec(IAX_IE.PASSWORD)
encode_language,       decode_language       = _string_codec(IAX_IE.LANGUAGE)
encode_dnid,           decode_dnid           = _string_codec(IAX_IE.DNID)
encode_challenge,      decode_challenge      = _string_codec(IAX_IE.CHALLENGE)
encode_md5_result,     decode_md5_result     = _string_codec(IAX_IE.MD5_RESULT)
encode_rsa_result,     decode_rsa_result     = _string_codec(IAX_IE.RSA_RESULT)
encode_cause,          decode_cause          = _string_codec(IAX_IE.CAUSE)
encode_rdnis,          decode_rdnis          = _string_codec(IAX_IE.RDNIS)
encode_codec_prefs,    decode_codec_prefs    = _string_codec(IAX_IE.CODEC_PREFS)


# ---------------------------------------------------------------------------
# 8.6.7  CAPABILITY (0x08) — u32 bitmask of supported codecs
# 8.6.8  FORMAT     (0x09) — u32 selected codec
# ---------------------------------------------------------------------------
def encode_capability(bitmask: int) -> bytes:
    return pack_ie(IAX_IE.CAPABILITY, pack_u32(bitmask))


def decode_capability(payload: bytes) -> int:
    return unpack_u32(payload)


def encode_format(codec: int) -> bytes:
    return pack_ie(IAX_IE.FORMAT, pack_u32(codec))


def decode_format(payload: bytes) -> int:
    return unpack_u32(payload)


# ---------------------------------------------------------------------------
# 8.6.10 VERSION  (0x0B) — u16, MUST be 2
# 8.6.11 ADSICPE  (0x0C) — u16
# 8.6.13 AUTHMETHODS (0x0E) — u16 bitmask (MD5=2, RSA=4)
# 8.6.18 REFRESH  (0x13) — u16 seconds
# 8.6.19 DPSTATUS (0x14) — u16 bitmask
# 8.6.20 CALLNO   (0x15) — u16
# 8.6.23 MSGCOUNT (0x18) — u16 (high byte old, low byte new)
# 8.6.32 SAMPLINGRATE (0x29) — u16 in Hz
# 8.6.34 ENCRYPTION (0x2B) — u16 bitmask (AES128=1, KEYROTATE=0x8000)
# ---------------------------------------------------------------------------
def _u16_codec(ie_type: int):
    def encode(value: int) -> bytes:
        return pack_ie(ie_type, pack_u16(value))

    def decode(payload: bytes) -> int:
        return unpack_u16(payload)

    return encode, decode


encode_version,      decode_version      = _u16_codec(IAX_IE.VERSION)
encode_adsicpe,      decode_adsicpe      = _u16_codec(IAX_IE.ADSICPE)
encode_authmethods,  decode_authmethods  = _u16_codec(IAX_IE.AUTHMETHODS)
encode_refresh,      decode_refresh      = _u16_codec(IAX_IE.REFRESH)
encode_dpstatus,     decode_dpstatus     = _u16_codec(IAX_IE.DPSTATUS)
encode_callno,       decode_callno       = _u16_codec(IAX_IE.CALLNO)
encode_msgcount,     decode_msgcount     = _u16_codec(IAX_IE.MSGCOUNT)
encode_samplingrate, decode_samplingrate = _u16_codec(IAX_IE.SAMPLINGRATE)
encode_encryption,   decode_encryption   = _u16_codec(IAX_IE.ENCRYPTION)


# ---------------------------------------------------------------------------
# 8.6.17 APPARENT ADDR (0x12) — sockaddr_in / sockaddr_in6
# ---------------------------------------------------------------------------
def encode_apparent_addr(ip: str, port: int) -> bytes:
    """Encode an IPv4 or IPv6 (ip, port) pair as APPARENT ADDR."""
    if ":" in ip:
        payload = pack_sockaddr_in6(ip, port)
    else:
        payload = pack_sockaddr_in(ip, port)
    return pack_ie(IAX_IE.APPARENT_ADDR, payload)


def decode_apparent_addr(payload: bytes) -> tuple[str, int]:
    return unpack_sockaddr(payload)


# ---------------------------------------------------------------------------
# 8.6.22 IAX UNKNOWN (0x17) — u8 (subclass of unsupported command)
# 8.6.29 CALLINGPRES (0x26) — u8 Q.931 presentation
# 8.6.30 CALLINGTON  (0x27) — u8 Q.931 type-of-number
# 8.6.33 CAUSECODE   (0x2A) — u8 Q.931 cause code
# ---------------------------------------------------------------------------
def _u8_codec(ie_type: int):
    def encode(value: int) -> bytes:
        return pack_ie(ie_type, pack_u8(value))

    def decode(payload: bytes) -> int:
        return unpack_u8(payload)

    return encode, decode


encode_iax_unknown, decode_iax_unknown = _u8_codec(IAX_IE.IAX_UNKNOWN)
encode_callingpres, decode_callingpres = _u8_codec(IAX_IE.CALLINGPRES)
encode_callington,  decode_callington  = _u8_codec(IAX_IE.CALLINGTON)
encode_causecode,   decode_causecode   = _u8_codec(IAX_IE.CAUSECODE)


# ---------------------------------------------------------------------------
# 8.6.24 AUTOANSWER (0x19) — zero-length payload; presence is the signal
# ---------------------------------------------------------------------------
def encode_autoanswer() -> bytes:
    return pack_ie(IAX_IE.AUTOANSWER, b"")


def decode_autoanswer(payload: bytes) -> bool:
    if len(payload) != 0:
        raise IEDecodeError(
            f"AUTOANSWER must be zero-length, got {len(payload)} bytes"
        )
    return True


# ---------------------------------------------------------------------------
# 8.6.25 MUSICONHOLD (0x1A) — optional UTF-8 class name (may be empty)
# ---------------------------------------------------------------------------
def encode_musiconhold(moh_class: str | None = None) -> bytes:
    payload = encode_utf8(moh_class) if moh_class else b""
    return pack_ie(IAX_IE.MUSICONHOLD, payload)


def decode_musiconhold(payload: bytes) -> str | None:
    return decode_utf8(payload) if payload else None


# ---------------------------------------------------------------------------
# 8.6.26 TRANSFERID (0x1B) — u32
# ---------------------------------------------------------------------------
def encode_transferid(value: int) -> bytes:
    return pack_ie(IAX_IE.TRANSFERID, pack_u32(value))


def decode_transferid(payload: bytes) -> int:
    return unpack_u32(payload)


# ---------------------------------------------------------------------------
# 8.6.28 DATETIME (0x1F) — 32-bit packed UTC date/time
#
#   bits 31..25  year - 2000   (7 bits)
#   bits 24..21  month         (4 bits, 1-12)
#   bits 20..16  day           (5 bits, 1-31)
#   bits 15..11  hour          (5 bits, 0-23)
#   bits 10..5   minute        (6 bits, 0-59)
#   bits  4..0   seconds / 2   (5 bits, 0-30, 2-second resolution)
# ---------------------------------------------------------------------------
def encode_datetime(year: int, month: int, day: int,
                    hour: int, minute: int, second: int) -> bytes:
    """Encode a UTC date/time as the DATETIME IE (full TLV including header)."""
    year_offset = year - 2000
    if not 0 <= year_offset <= 127:
        raise IEEncodeError(f"year {year} out of range 2000..2127")
    if not 1 <= month <= 12:
        raise IEEncodeError(f"month {month} out of range")
    if not 1 <= day <= 31:
        raise IEEncodeError(f"day {day} out of range")
    if not 0 <= hour <= 23:
        raise IEEncodeError(f"hour {hour} out of range")
    if not 0 <= minute <= 59:
        raise IEEncodeError(f"minute {minute} out of range")
    if not 0 <= second <= 59:
        raise IEEncodeError(f"second {second} out of range")

    packed = (
        ((year_offset & 0x7F) << 25)
        | ((month & 0x0F) << 21)
        | ((day & 0x1F) << 16)
        | ((hour & 0x1F) << 11)
        | ((minute & 0x3F) << 5)
        | (((second // 2) & 0x1F))
    )
    return pack_ie(IAX_IE.DATETIME, pack_u32(packed))


def decode_datetime(payload: bytes) -> tuple[int, int, int, int, int, int]:
    """Decode DATETIME payload to (year, month, day, hour, minute, second)."""
    if len(payload) != 4:
        raise IEDecodeError(f"DATETIME must be 4 bytes, got {len(payload)}")
    raw = unpack_u32(payload)
    return (
        ((raw >> 25) & 0x7F) + 2000,   # year
         (raw >> 21) & 0x0F,           # month
         (raw >> 16) & 0x1F,           # day
         (raw >> 11) & 0x1F,           # hour
         (raw >>  5) & 0x3F,           # minute
         (raw        & 0x1F) * 2,      # second (2s resolution)
    )


# ---------------------------------------------------------------------------
# 8.6.31 CALLINGTNS (0x28) — 16-bit network identification + UTF-8 net id
#
#   octet 0: low 4 bits = plan, next 3 bits = type-of-network
#   octet 1+: UTF-8 network identification
# ---------------------------------------------------------------------------
def encode_callingtns(plan: int, ton: int, net_id: str = "") -> bytes:
    if not 0 <= plan <= 0x0F:
        raise IEEncodeError(f"plan {plan} out of 4-bit range")
    if not 0 <= ton <= 0x07:
        raise IEEncodeError(f"ton {ton} out of 3-bit range")
    header = bytes([(ton << 4) | plan])
    payload = header + encode_utf8(net_id) if net_id else header
    return pack_ie(IAX_IE.CALLINGTNS, payload)


def decode_callingtns(payload: bytes) -> tuple[int, int, str]:
    if len(payload) < 1:
        raise IEDecodeError("CALLINGTNS needs at least 1 byte")
    octet = payload[0]
    plan = octet & 0x0F
    ton = (octet >> 4) & 0x07
    net_id = decode_utf8(payload[1:]) if len(payload) > 1 else ""
    return plan, ton, net_id


# ---------------------------------------------------------------------------
# 8.6.36–8.6.41 Receiver-report IEs (RFC 3550 metrics) — all u32 except DELAY
#   RR JITTER   (0x2E) u32
#   RR LOSS     (0x2F) u32 (high byte = pct, low 24 bits = count)
#   RR PKTS     (0x30) u32
#   RR DELAY    (0x31) u16  -- yes, u16, per RFC 5456 §8.6.39 wording AND
#                              the Asterisk header (u16). The RFC ASCII art
#                              shows a 4-byte field but the prose and
#                              implementations use 2 bytes; we follow the
#                              implementations for interop.
#   RR DROPPED  (0x32) u32
#   RR OOO      (0x33) u32
# ---------------------------------------------------------------------------
def encode_rr_jitter(value: int) -> bytes:
    return pack_ie(IAX_IE.RR_JITTER, pack_u32(value))


def decode_rr_jitter(payload: bytes) -> int:
    return unpack_u32(payload)


def encode_rr_loss(pct: int, count: int) -> bytes:
    """Encode loss percentage (u8) and 24-bit count into one u32."""
    if not 0 <= pct <= 0xFF:
        raise IEEncodeError(f"loss pct {pct} out of range")
    if not 0 <= count <= 0xFFFFFF:
        raise IEEncodeError(f"loss count {count} out of 24-bit range")
    return pack_ie(IAX_IE.RR_LOSS, pack_u32((pct << 24) | count))


def decode_rr_loss(payload: bytes) -> tuple[int, int]:
    raw = unpack_u32(payload)
    return (raw >> 24) & 0xFF, raw & 0xFFFFFF


def encode_rr_pkts(value: int) -> bytes:
    return pack_ie(IAX_IE.RR_PKTS, pack_u32(value))


def decode_rr_pkts(payload: bytes) -> int:
    return unpack_u32(payload)


def encode_rr_delay(value: int) -> bytes:
    """Max playout delay in ms (u16 per Asterisk implementation)."""
    return pack_ie(IAX_IE.RR_DELAY, pack_u16(value))


def decode_rr_delay(payload: bytes) -> int:
    return unpack_u16(payload)


def encode_rr_dropped(value: int) -> bytes:
    return pack_ie(IAX_IE.RR_DROPPED, pack_u32(value))


def decode_rr_dropped(payload: bytes) -> int:
    return unpack_u32(payload)


def encode_rr_ooo(value: int) -> bytes:
    return pack_ie(IAX_IE.RR_OOO, pack_u32(value))


def decode_rr_ooo(payload: bytes) -> int:
    return unpack_u32(payload)


# ---------------------------------------------------------------------------
# 8.6.42 OSPTOKEN (0x34) — raw OSP token bytes
# ---------------------------------------------------------------------------
def encode_osptoken(token: bytes) -> bytes:
    return pack_ie(IAX_IE.OSPTOKEN, bytes(token))


def decode_osptoken(payload: bytes) -> bytes:
    return bytes(payload)


# ---------------------------------------------------------------------------
# 8.6.44 ENCKEY (0x2C) — raw encryption key material
# (RFC 5456 lists this as "reserved for future use", but Asterisk emits it
#  during key rotation, so we treat it as opaque bytes.)
# ---------------------------------------------------------------------------
def encode_enckey(data: bytes) -> bytes:
    return pack_ie(IAX_IE.ENCKEY, bytes(data))


def decode_enckey(payload: bytes) -> bytes:
    return bytes(payload)


# ===========================================================================
# Asterisk-specific extensions (not in RFC 5456)
# ===========================================================================
# These IE numbers are not in the IANA registry but appear in
# channels/iax2/include/iax2.h. They're widely deployed and required for
# real-world Asterisk interop.

# IAX_IE_PROVISIONING (0x1D) — raw provisioning blob
def encode_provisioning(data: bytes) -> bytes:
    return pack_ie(0x1D, bytes(data))


def decode_provisioning(payload: bytes) -> bytes:
    return bytes(payload)


# IAX_IE_AESPROVISIONING (0x1E) — AES-encrypted provisioning blob
def encode_aesprovisioning(data: bytes) -> bytes:
    return pack_ie(0x1E, bytes(data))


def decode_aesprovisioning(payload: bytes) -> bytes:
    return bytes(payload)


# IAX_IE_DEVICETYPE (0x20) — UTF-8 device model string
def encode_devicetype(value: str) -> bytes:
    return pack_ie(0x20, encode_utf8(value))


def decode_devicetype(payload: bytes) -> str:
    return decode_utf8(payload)


# IAX_IE_SERVICEIDENT (0x21) — UTF-8 service identifier
def encode_serviceident(value: str) -> bytes:
    return pack_ie(0x21, encode_utf8(value))


def decode_serviceident(payload: bytes) -> str:
    return decode_utf8(payload)


# IAX_IE_FIRMWAREVER (0x22) — u16 firmware revision
def encode_firmwarever(value: int) -> bytes:
    return pack_ie(0x22, pack_u16(value))


def decode_firmwarever(payload: bytes) -> int:
    return unpack_u16(payload)


# IAX_IE_FWBLOCKDESC (0x23) — u32 firmware block descriptor / offset
def encode_fwblockdesc(value: int) -> bytes:
    return pack_ie(0x23, pack_u32(value))


def decode_fwblockdesc(payload: bytes) -> int:
    return unpack_u32(payload)


# IAX_IE_FWBLOCKDATA (0x24) — raw firmware block payload
def encode_fwblockdata(data: bytes) -> bytes:
    return pack_ie(0x24, bytes(data))


def decode_fwblockdata(payload: bytes) -> bytes:
    return bytes(payload)


# IAX_IE_PROVVER (0x25) — u32 provisioning version
def encode_provver(value: int) -> bytes:
    return pack_ie(0x25, pack_u32(value))


def decode_provver(payload: bytes) -> int:
    return unpack_u32(payload)


# IAX_IE_VARIABLE (0x34 in Asterisk = 52) — UTF-8 "name=value"
# NOTE: Asterisk numbers this 52 (0x34) which overlaps the RFC's OSPTOKEN.
# The two never appear in the same conversation in practice (OSP is used at
# call setup; VARIABLE during established calls), but a parser MUST resolve
# this by context. We expose both encoders with explicit names so callers
# choose deliberately.
def encode_variable(name: str, value: str) -> bytes:
    return pack_ie(0x34, encode_utf8(f"{name}={value}"))


def decode_variable(payload: bytes) -> tuple[str, str]:
    text = decode_utf8(payload)
    name, _, value = text.partition("=")
    return name, value


# IAX_IE_CALLTOKEN (0x36) — opaque token for DoS mitigation (zero-length
# from client to advertise support; non-zero on server response)
def encode_calltoken(token: bytes = b"") -> bytes:
    return pack_ie(0x36, bytes(token))


def decode_calltoken(payload: bytes) -> bytes:
    return bytes(payload)


# IAX_IE_CAPABILITY2 (0x37) — u8 version + array of 64-bit codec IDs
# IAX_IE_FORMAT2     (0x38) — same layout
def _encode_format2(ie_type: int, version: int, formats: list[int]) -> bytes:
    if not 0 <= version <= 0xFF:
        raise IEEncodeError(f"version {version} out of u8 range")
    payload = bytes([version])
    for fmt in formats:
        if not 0 <= fmt <= 0xFFFFFFFFFFFFFFFF:
            raise IEEncodeError(f"format value {fmt} out of u64 range")
        payload += struct.pack("!Q", fmt)
    return pack_ie(ie_type, payload)


def _decode_format2(payload: bytes) -> tuple[int, list[int]]:
    if len(payload) < 1:
        raise IEDecodeError("CAPABILITY2/FORMAT2 needs at least 1 byte")
    version = payload[0]
    rest = payload[1:]
    if len(rest) % 8 != 0:
        raise IEDecodeError(
            f"CAPABILITY2/FORMAT2 payload after version must be multiple of 8, "
            f"got {len(rest)}"
        )
    formats = [
        struct.unpack_from("!Q", rest, i)[0]
        for i in range(0, len(rest), 8)
    ]
    return version, formats


def encode_capability2(version: int, formats: list[int]) -> bytes:
    return _encode_format2(0x37, version, formats)


def decode_capability2(payload: bytes) -> tuple[int, list[int]]:
    return _decode_format2(payload)


def encode_format2(version: int, formats: list[int]) -> bytes:
    return _encode_format2(0x38, version, formats)


def decode_format2(payload: bytes) -> tuple[int, list[int]]:
    return _decode_format2(payload)


# IAX_IE_CALLINGANI2 (0x39) — u32 ANI II info digits
def encode_callingani2(value: int) -> bytes:
    return pack_ie(0x39, pack_u32(value))


def decode_callingani2(payload: bytes) -> int:
    return unpack_u32(payload)


# ===========================================================================
# Codec registry — wire IE type -> decoder. Lets a generic parser turn raw
# bytes into Python values without a huge if/elif ladder.
# ===========================================================================
# (encoder, decoder) pairs. The encoder takes the *payload value* and returns
# a *raw payload* (no TLV header). The dispatch wrappers above add the TLV
# header via pack_ie() because some IEs have non-scalar arg signatures.

# For the registry we wrap each per-IE function so it takes a single "value"
# argument and returns just the payload bytes (no TLV). That way the generic
# encode_ie() facade can prepend the type/length itself.
def _payload_only(encoder_with_header):
    """
    Wrap an ``encode_*`` function (which returns a full TLV) so it returns
    just the payload bytes, given a single value argument.
    """
    def wrapper(value):
        # All the encode_* helpers above accept a single positional arg
        # except a few (encode_apparent_addr, encode_callingtns,
        # encode_rr_loss, encode_datetime, encode_variable, encode_capability2,
        # encode_format2). Callers needing those should call them directly;
        # the registry handles the simple single-value case for parsing.
        full = encoder_with_header(value) if not isinstance(value, tuple) \
               else encoder_with_header(*value)
        return full[2:]  # strip 2-byte TLV header
    return wrapper


# Per-IE decoder registration. Encoders are NOT registered through this path
# because many IEs take multiple positional args; use the dedicated
# ``encode_<name>`` functions directly. The decoder side is uniform: take
# bytes, return value.
_DECODERS_BY_TYPE = {
    IAX_IE.CALLED_NUMBER:   decode_called_number,
    IAX_IE.CALLING_NUMBER:  decode_calling_number,
    IAX_IE.CALLING_ANI:     decode_calling_ani,
    IAX_IE.CALLING_NAME:    decode_calling_name,
    IAX_IE.CALLED_CONTEXT:  decode_called_context,
    IAX_IE.USERNAME:        decode_username,
    IAX_IE.PASSWORD:        decode_password,
    IAX_IE.CAPABILITY:      decode_capability,
    IAX_IE.FORMAT:          decode_format,
    IAX_IE.LANGUAGE:        decode_language,
    IAX_IE.VERSION:         decode_version,
    IAX_IE.ADSICPE:         decode_adsicpe,
    IAX_IE.DNID:            decode_dnid,
    IAX_IE.AUTHMETHODS:     decode_authmethods,
    IAX_IE.CHALLENGE:       decode_challenge,
    IAX_IE.MD5_RESULT:      decode_md5_result,
    IAX_IE.RSA_RESULT:      decode_rsa_result,
    IAX_IE.APPARENT_ADDR:   decode_apparent_addr,
    IAX_IE.REFRESH:         decode_refresh,
    IAX_IE.DPSTATUS:        decode_dpstatus,
    IAX_IE.CALLNO:          decode_callno,
    IAX_IE.CAUSE:           decode_cause,
    IAX_IE.IAX_UNKNOWN:     decode_iax_unknown,
    IAX_IE.MSGCOUNT:        decode_msgcount,
    IAX_IE.AUTOANSWER:      decode_autoanswer,
    IAX_IE.MUSICONHOLD:     decode_musiconhold,
    IAX_IE.TRANSFERID:      decode_transferid,
    IAX_IE.RDNIS:           decode_rdnis,
    IAX_IE.DATETIME:        decode_datetime,
    IAX_IE.CALLINGPRES:     decode_callingpres,
    IAX_IE.CALLINGTON:      decode_callington,
    IAX_IE.CALLINGTNS:      decode_callingtns,
    IAX_IE.SAMPLINGRATE:    decode_samplingrate,
    IAX_IE.CAUSECODE:       decode_causecode,
    IAX_IE.ENCRYPTION:      decode_encryption,
    IAX_IE.ENCKEY:          decode_enckey,
    IAX_IE.CODEC_PREFS:     decode_codec_prefs,
    IAX_IE.RR_JITTER:       decode_rr_jitter,
    IAX_IE.RR_LOSS:         decode_rr_loss,
    IAX_IE.RR_PKTS:         decode_rr_pkts,
    IAX_IE.RR_DELAY:        decode_rr_delay,
    IAX_IE.RR_DROPPED:      decode_rr_dropped,
    IAX_IE.RR_OOO:          decode_rr_ooo,
    IAX_IE.OSPTOKEN:        decode_osptoken,

    # Asterisk extensions
    0x1D:                   decode_provisioning,
    0x1E:                   decode_aesprovisioning,
    0x20:                   decode_devicetype,
    0x21:                   decode_serviceident,
    0x22:                   decode_firmwarever,
    0x23:                   decode_fwblockdesc,
    0x24:                   decode_fwblockdata,
    0x25:                   decode_provver,
    # 0x34 overlaps OSPTOKEN — keep OSPTOKEN as the default, decode VARIABLE
    # only when context demands it (use decode_variable explicitly).
    0x36:                   decode_calltoken,
    0x37:                   decode_capability2,
    0x38:                   decode_format2,
    0x39:                   decode_callingani2,
}


def decode_ie(buf: bytes, offset: int = 0) -> tuple[int, object, int]:
    """
    Read one IE from ``buf`` at ``offset`` and return ``(ie_type, value,
    next_offset)``. Unknown IE types return their raw payload as bytes.
    """
    ie_type, payload, next_offset = unpack_ie(buf, offset)
    decoder = _DECODERS_BY_TYPE.get(ie_type)
    if decoder is None:
        return ie_type, bytes(payload), next_offset
    return ie_type, decoder(payload), next_offset


def iter_decoded_ies(buf: bytes, offset: int = 0) -> Iterator[tuple[int, object]]:
    """Yield ``(ie_type, value)`` for every IE in ``buf``."""
    while offset < len(buf):
        ie_type, value, offset = decode_ie(buf, offset)
        yield ie_type, value


def encode_ie(ie_type: int, *args, **kwargs) -> bytes:
    """
    Generic encode facade for IEs taking a single scalar value.

    For IEs with non-scalar signatures (APPARENT_ADDR, CALLINGTNS, RR_LOSS,
    DATETIME, VARIABLE, CAPABILITY2, FORMAT2), call the dedicated
    ``encode_<name>`` function directly instead.
    """
    encoders = {
        IAX_IE.CALLED_NUMBER:   encode_called_number,
        IAX_IE.CALLING_NUMBER:  encode_calling_number,
        IAX_IE.CALLING_ANI:     encode_calling_ani,
        IAX_IE.CALLING_NAME:    encode_calling_name,
        IAX_IE.CALLED_CONTEXT:  encode_called_context,
        IAX_IE.USERNAME:        encode_username,
        IAX_IE.PASSWORD:        encode_password,
        IAX_IE.CAPABILITY:      encode_capability,
        IAX_IE.FORMAT:          encode_format,
        IAX_IE.LANGUAGE:        encode_language,
        IAX_IE.VERSION:         encode_version,
        IAX_IE.ADSICPE:         encode_adsicpe,
        IAX_IE.DNID:            encode_dnid,
        IAX_IE.AUTHMETHODS:     encode_authmethods,
        IAX_IE.CHALLENGE:       encode_challenge,
        IAX_IE.MD5_RESULT:      encode_md5_result,
        IAX_IE.RSA_RESULT:      encode_rsa_result,
        IAX_IE.REFRESH:         encode_refresh,
        IAX_IE.DPSTATUS:        encode_dpstatus,
        IAX_IE.CALLNO:          encode_callno,
        IAX_IE.CAUSE:           encode_cause,
        IAX_IE.IAX_UNKNOWN:     encode_iax_unknown,
        IAX_IE.MSGCOUNT:        encode_msgcount,
        IAX_IE.MUSICONHOLD:     encode_musiconhold,
        IAX_IE.TRANSFERID:      encode_transferid,
        IAX_IE.RDNIS:           encode_rdnis,
        IAX_IE.CALLINGPRES:     encode_callingpres,
        IAX_IE.CALLINGTON:      encode_callington,
        IAX_IE.SAMPLINGRATE:    encode_samplingrate,
        IAX_IE.CAUSECODE:       encode_causecode,
        IAX_IE.ENCRYPTION:      encode_encryption,
        IAX_IE.ENCKEY:          encode_enckey,
        IAX_IE.CODEC_PREFS:     encode_codec_prefs,
        IAX_IE.RR_JITTER:       encode_rr_jitter,
        IAX_IE.RR_PKTS:         encode_rr_pkts,
        IAX_IE.RR_DELAY:        encode_rr_delay,
        IAX_IE.RR_DROPPED:      encode_rr_dropped,
        IAX_IE.RR_OOO:          encode_rr_ooo,
        IAX_IE.OSPTOKEN:        encode_osptoken,

        0x1D: encode_provisioning,
        0x1E: encode_aesprovisioning,
        0x20: encode_devicetype,
        0x21: encode_serviceident,
        0x22: encode_firmwarever,
        0x23: encode_fwblockdesc,
        0x24: encode_fwblockdata,
        0x25: encode_provver,
        0x36: encode_calltoken,
        0x39: encode_callingani2,
    }
    if ie_type == IAX_IE.AUTOANSWER:
        return encode_autoanswer()
    encoder = encoders.get(ie_type)
    if encoder is None:
        # Fall back to raw-bytes IE: caller passed (data,)
        if len(args) != 1 or not isinstance(args[0], (bytes, bytearray, memoryview)):
            raise IEEncodeError(
                f"no encoder for IE 0x{ie_type:02x}; pass raw bytes to use raw encode"
            )
        return pack_ie(ie_type, bytes(args[0]))
    return encoder(*args, **kwargs)


__all__ = [
    # standard IEs
    "encode_called_number", "decode_called_number",
    "encode_calling_number", "decode_calling_number",
    "encode_calling_ani", "decode_calling_ani",
    "encode_calling_name", "decode_calling_name",
    "encode_called_context", "decode_called_context",
    "encode_username", "decode_username",
    "encode_password", "decode_password",
    "encode_capability", "decode_capability",
    "encode_format", "decode_format",
    "encode_language", "decode_language",
    "encode_version", "decode_version",
    "encode_adsicpe", "decode_adsicpe",
    "encode_dnid", "decode_dnid",
    "encode_authmethods", "decode_authmethods",
    "encode_challenge", "decode_challenge",
    "encode_md5_result", "decode_md5_result",
    "encode_rsa_result", "decode_rsa_result",
    "encode_apparent_addr", "decode_apparent_addr",
    "encode_refresh", "decode_refresh",
    "encode_dpstatus", "decode_dpstatus",
    "encode_callno", "decode_callno",
    "encode_cause", "decode_cause",
    "encode_iax_unknown", "decode_iax_unknown",
    "encode_msgcount", "decode_msgcount",
    "encode_autoanswer", "decode_autoanswer",
    "encode_musiconhold", "decode_musiconhold",
    "encode_transferid", "decode_transferid",
    "encode_rdnis", "decode_rdnis",
    "encode_datetime", "decode_datetime",
    "encode_callingpres", "decode_callingpres",
    "encode_callington", "decode_callington",
    "encode_callingtns", "decode_callingtns",
    "encode_samplingrate", "decode_samplingrate",
    "encode_causecode", "decode_causecode",
    "encode_encryption", "decode_encryption",
    "encode_enckey", "decode_enckey",
    "encode_codec_prefs", "decode_codec_prefs",
    "encode_rr_jitter", "decode_rr_jitter",
    "encode_rr_loss", "decode_rr_loss",
    "encode_rr_pkts", "decode_rr_pkts",
    "encode_rr_delay", "decode_rr_delay",
    "encode_rr_dropped", "decode_rr_dropped",
    "encode_rr_ooo", "decode_rr_ooo",
    "encode_osptoken", "decode_osptoken",

    # Asterisk extensions
    "encode_provisioning", "decode_provisioning",
    "encode_aesprovisioning", "decode_aesprovisioning",
    "encode_devicetype", "decode_devicetype",
    "encode_serviceident", "decode_serviceident",
    "encode_firmwarever", "decode_firmwarever",
    "encode_fwblockdesc", "decode_fwblockdesc",
    "encode_fwblockdata", "decode_fwblockdata",
    "encode_provver", "decode_provver",
    "encode_variable", "decode_variable",
    "encode_calltoken", "decode_calltoken",
    "encode_capability2", "decode_capability2",
    "encode_format2", "decode_format2",
    "encode_callingani2", "decode_callingani2",

    # generic facades
    "encode_ie", "decode_ie", "iter_decoded_ies",
]

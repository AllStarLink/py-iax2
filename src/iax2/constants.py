"""IAX2 protocol constants (frame types, subclasses, IE codes, etc.)."""
from enum import IntEnum


IAX2_DEFAULT_PORT = 4569


class FrameType(IntEnum):
    DTMF    = 0x01
    VOICE   = 0x02
    VIDEO   = 0x03
    CONTROL = 0x04
    NULL    = 0x05
    IAX     = 0x06
    TEXT    = 0x07
    IMAGE   = 0x08
    HTML    = 0x09
    CNG     = 0x0A
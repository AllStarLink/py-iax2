Protocol notes
==============

This page records how the implementation maps to RFC 5456 and where it
follows Asterisk 22 (``channels/chan_iax2.c``, ``channels/iax2/parser.c``)
instead. These details matter for interoperability.

Reliability
-----------

* Every full frame except ACK, INVAL, TXCNT, TXACC and VNAK consumes an
  outbound sequence number; those are also never acknowledged.
* Received frames are acknowledged implicitly by the ``iseqno`` of the next
  full frame sent; an explicit ACK (echoing the frame's timestamp) is sent
  whenever the peer could otherwise be left waiting.
* Out-of-order frames that were already seen are re-ACKed; frames from the
  future trigger a VNAK, which makes the peer resend everything unacknowledged.
* Retransmission starts after 2 × the measured round-trip time (initially
  2 s), clamped to 100 ms–10 s, multiplied by 10 each time; a frame is sent
  at most 4 times. If it is still unacknowledged the dialog is dropped (call
  hangup cause ``DESTINATION_OUT_OF_ORDER``).
* "Final" frames (HANGUP, REJECT, REGACK, REGREJ) destroy the dialog when
  acknowledged.
* Non-IAX frames (control, DTMF, voice) are held until the peer's call number
  is known (Asterisk ``hold_signaling``).

Timestamps
----------

* Time zero is the first frame of the dialog, rounded down to 20 ms.
* IAX (signalling) frames use the real clock, strictly increasing by at least
  3 ms; voice uses predicted timestamps that advance by the frame duration and
  are pulled 10 % toward the clock; other frames are slotted 3 ms after the
  last one. Explicit timestamps are echoed in ACK, PONG and LAGRP.
* A voice mini frame is used unless the codec changed or the upper 16 bits
  of the timestamp moved by more than one step; receivers reconstruct the
  upper bits with a ±50 s wrap-around heuristic (±25 s for video's 15 bits).

Call setup and negotiation
--------------------------

* ``NEW`` carries VERSION first and an empty CALLTOKEN last, plus both the
  32-bit (FORMAT, CAPABILITY) and versioned 64-bit (FORMAT2, CAPABILITY2)
  codec IEs; receivers prefer the 64-bit ones.
* The callee picks the codec from its own preference list first
  (``codecpriority=host``), then the caller's CODEC_PREFS, then a built-in
  ranking (ulaw, alaw, g719, siren14, siren7, g722, slin16, slin, ...).
* ``ACCEPT`` is sent as soon as the caller is authorised; ringing and
  ``ANSWER`` follow as CONTROL frames.
* Control subclasses that Asterisk doesn't allow on IAX2 are dropped on
  send and receive.

Call tokens
-----------

Server tokens are ``"<unix time>?<sha1>"`` with the hash over
``"<address>:<port><time><random secret>"`` and a 10 s lifetime. Replies to
unknown dialogs (CALLTOKEN, PONG to POKE, REJECT/REGREJ) come from call
number 1 ("apathetic replies"). A client receiving CALLTOKEN resends its
first frame with the token, resetting sequence numbers.

Authentication
--------------

* Challenges are up to 9 decimal digits.
* MD5 responses compare case-insensitively; several ``;``-separated secrets
  may be configured.
* RSA: PKCS#1 v1.5 / SHA-1 signature, base64, 1024-bit keys.
* Unknown registrants are still challenged (REGAUTH) before being rejected.

Encryption
----------

AES-128-CBC with a zero IV, keyed by md5(challenge+secret), 16–31 bytes of
random leading padding whose low nibble is in byte 15, everything after the
call numbers encrypted, no integrity check. The callee starts expecting
encrypted frames right after sending AUTHREQ; RTKEY rotates the sender's key.

Information Elements
--------------------

* Malformed fixed-size IEs are ignored (not fatal); an IE overrunning the
  frame, or a stray trailing byte, makes the whole frame undecodable.
* When an IE repeats, the last one wins — except VARIABLE, whose values for
  the same name are concatenated (Asterisk splits long values that way).
* APPARENT_ADDR is a raw Linux ``sockaddr_in``/``sockaddr_in6`` with the
  address family in host (little-endian) byte order.
* DATETIME is local time packed as year-2000:7, month:4, day:5, hour:5,
  minute:6, second/2:5.

Media
-----

* ``slin`` and ``slin16`` are big-endian on the wire.
* Text frames are NUL-terminated.
* Video full frames carry the end-of-picture mark as bit 0x40 of the
  compressed subclass; meta video frames as bit 15 of the timestamp.
* Trunk frames without per-call timestamps never carry two entries of the
  same call (Asterisk's jitter buffer would drop the second).

Unsupported / not applicable
----------------------------

* Firmware download (FWDOWNL) and provisioning are answered UNSUPPORT.
* ``PAGE`` is not implemented.
* OSP tokens are passed through as raw IEs only.

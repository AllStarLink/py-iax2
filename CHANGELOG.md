# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - unreleased

First release: a complete rewrite of the earlier skeleton.

### Added

- Wire format: full, mini, meta video and meta trunk frames; every RFC 5456
  and Asterisk Information Element.
- Reliable session layer modelled on Asterisk 22 `chan_iax2` (sequence
  numbers, implicit/explicit ACK, VNAK, retransmission back-off, timestamp
  prediction, signalling hold, PING/LAGRQ).
- `Endpoint` and `Call`: outgoing and incoming calls, events, DTMF, text,
  HTML/URL, image, control frames, quelch, blind transfer requests,
  dialplan queries.
- Authentication: MD5, RSA (Asterisk key files) and plaintext; call tokens.
- Asterisk-compatible AES-128 encryption with key rotation.
- Codecs: μ-law, A-law, slin, slin16; codec registry for plug-in codecs;
  Asterisk-compatible codec negotiation.
- Adaptive jitter buffer and `Call.playout()`.
- Registration client and registrar.
- Native transfers (endpoint and server roles) and trunking.
- Blocking API (`iax2.sync`).
- `iax2-tool` command line utility and example programs.
- Sphinx documentation, unit/loopback tests and an Asterisk interoperability
  test suite.
- Debian packaging.
- Protection against hostile traffic: per-IP call-number limit
  (`EndpointConfig.max_callnumbers_per_ip`, default 2048 as in Asterisk),
  clean rejection when call numbers run out, rate-limited warnings for
  unauthenticated traffic, and fuzz/flood tests.
- GitHub Actions CI, `SECURITY.md` and `CONTRIBUTING.md`.

### Changed

- `allow_guest` now defaults to `False`: callers without a known username are
  rejected unless you opt in. `iax2-tool serve` needs `--guest` to accept them.
- Licensed under GPL-2.0-only, matching Asterisk.

# py-iax2 development plan & progress log

Working notes for building the library. **If you are resuming work, read this
file first, then `git log --oneline | head`, then continue at the first
unchecked milestone.**

## Ground rules (decided with the project owner, 2026-10-19)

- Behaviour reference: RFC 5456; when the RFC is vague or Asterisk differs,
  match **Asterisk 22 LTS** (`chan_iax2.c`, `iax2/parser.c`).
- Reference sources (outside repo, not committed):
  - `../_src/asterisk-22.10.1/` — upstream tag matching installed asl3-asterisk
    (the ASL3 chan_iax2 patch is already upstream in 22.10.1).
  - `../_src/asl3-asterisk/` — ASL3 Debian packaging/patches.
  - `../_src/rfc5456.txt`
- Local asl3-asterisk 22.10.1 is a **test box**: configs may be edited and the
  service restarted freely for interop tests.
- API: **asyncio core** + thin **sync wrapper** (background event-loop thread).
- Codecs: ulaw / alaw / slin built in (pure Python, no deps). Other codecs
  via optional extras / pluggable `Codec` classes. Payload passthrough always
  possible.
- Optional deps only: `cryptography` (RSA auth, AES-128 encryption).
- Python >= 3.11 (Debian 12 bookworm is the oldest ASL3 target).
- Git: commit directly to `main` at each milestone with `--no-gpg-sign`; never push.
- Deliverables: pip-installable (hatchling), Debian packaging (`debian/`,
  pybuild), Sphinx docs, examples (client + server), unit + interop tests.

## Architecture (src/iax2/)

| module | responsibility |
|---|---|
| `constants.py` | enums: FrameType, IAXCommand, ControlType, IE, AuthMethod, Cause, DPStatus, Encryption, … |
| `formats.py` | codec bitmask (`iax2_format`), names, frame sizing, codec-pref string, negotiation helpers |
| `ies.py` | Information Element encode/decode (`IEList`, typed accessors) |
| `frames.py` | wire format: FullFrame / MiniFrame / VideoFrame / Trunk meta; `parse_datagram()`; subclass (de)compression |
| `auth.py` | plaintext / MD5 / RSA challenge-response |
| `crypto.py` | AES-128 call encryption (Asterisk-compatible), optional |
| `calltoken.py` | CALLTOKEN generation/validation (Asterisk format) |
| `timing.py` | timestamp generation rules (Asterisk `calc_timestamp`) |
| `transport.py` | asyncio UDP endpoint, call-number allocation, demux |
| `reliability.py` | seqnos, ACK, retransmit, VNAK |
| `call.py` | `Call` object + state machine, media send/recv, DTMF, text, hangup |
| `registration.py` | client registration + server registrar |
| `endpoint.py` | `Endpoint` — public façade: listen, dial, register, accept, poke |
| `jitterbuffer.py` | simple adaptive jitter buffer (opt-in) |
| `codecs/` | Codec ABC, registry, ulaw, alaw, slin |
| `sync.py` | blocking wrapper |

## Milestones

- [x] M0  Clean skeleton: pyproject fix, package layout, tooling (ruff/mypy/pytest config)
- [x] M1  Wire format: constants, formats, IEs, frames + exhaustive unit tests
- [x] M2  Auth (plaintext/MD5/RSA), call tokens
- [ ] M3  Transport + reliability (ACK/retransmit/VNAK/timestamps), POKE/PING/LAGRQ
- [ ] M4  Calls: outbound NEW→ACCEPT→ANSWER, inbound, hangup/reject, auth flows
- [ ] M5  Media: voice full/mini frames, codecs ulaw/alaw/slin, DTMF, text, HTML/URL, control frames, quelch
- [ ] M6  Registration client + registrar server
- [ ] M7  Interop test harness against local Asterisk (scripted config, pytest marker `interop`)
- [ ] M8  Transfers (TXREQ…), trunking (meta trunk rx/tx), video meta frames, DPREQ, MWI
- [ ] M9  AES-128 encryption (optional dep)
- [ ] M10 Sync wrapper, jitter buffer
- [ ] M11 Examples: client (dial/play/record), server (echo, IVR-ish, registrar), CLI
- [ ] M12 Docs (Sphinx): tutorial, API reference, protocol notes, Asterisk interop guide
- [ ] M13 Packaging: wheel/sdist check, debian/ (pybuild), CI-ish `tox`, changelog

## Progress log

- 2026-10-19: Plan written; Asterisk 22.10.1 source + RFC fetched.

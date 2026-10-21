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
- [x] M3  Transport + reliability (ACK/retransmit/VNAK/timestamps), POKE/PING/LAGRQ
- [x] M4  Calls: outbound NEW→ACCEPT→ANSWER, inbound, hangup/reject, auth flows
- [x] M5  Media: voice full/mini frames, codecs ulaw/alaw/slin, DTMF, text, HTML/URL, control frames, quelch
- [x] M6  Registration client + registrar server
- [x] M7  Interop test harness against local Asterisk (scripted config, pytest marker `interop`)
- [x] M8  Transfers (TXREQ…), trunking (meta trunk rx/tx), video meta frames, DPREQ, MWI
- [x] M9  AES-128 encryption (optional dep)
- [x] M10 Sync wrapper, jitter buffer
- [x] M11 Examples: client (dial/play/record), server (echo, IVR-ish, registrar), CLI
- [x] M12 Docs (Sphinx): tutorial, API reference, protocol notes, Asterisk interop guide
- [x] M13 Packaging: wheel/sdist check, debian/ (pybuild), CI-ish `tox`, changelog

## Progress log

- 2026-10-19: Plan written; Asterisk 22.10.1 source + RFC fetched.
- 2026-10-20: M3–M6 done (session/reliability, calls, media+codecs, registration) with loopback tests. Transfer/trunk implemented but untested; crypto is a stub. Next: M7 interop vs local Asterisk.
- 2026-10-20: M7 interop harness: `sudo python3 tests/interop/setup_asterisk.py`, then
  `PYTHONPATH=src python3 -m pytest -m interop tests/interop` (18 tests, all pass vs 22.10.1).
  Findings: ASL3 has autoload=no (setup loads app_echo/playtones/senddtmf/read/res_clioriginate);
  Asterisk keys live in /usr/share/asterisk/keys; Asterisk's forced jitter buffer holds DTMF
  until voice flows (not our bug); supermini trunk frames must not carry two entries of one call.
- 2026-10-20: M8 done: native transfers (endpoint + server role; interop: our server transfers two Asterisk legs), video meta frames, DPREQ/DPREP, MWI hook. NOTE interop tests must only hang up IAX2/pyiax-* channels (never 'hangup all': kills app_rpt node channels).
- 2026-10-20: M9 done: AES-128 (Asterisk scheme, CBC zero IV, random pad, RTKEY rotation) verified both directions vs Asterisk incl. rotation.
- 2026-10-20: M10 done: iax2.sync (SyncEndpoint/SyncCall on a loop thread), JitterBuffer + Call.playout(), RR stats (jitter/OOO) in PONG.
- 2026-10-20: M11 done: iax2-tool CLI (poke/call/serve/register/decode), iax2.audio helpers, examples/ (echo_server, dial_play_record, sync_client, mini_pbx, allstar_client) all run against Asterisk/app_rpt. Interop has an app_rpt client test (pyiax-rpt user).
- 2026-10-20: M12 done: Sphinx docs (guide, CLI, protocol notes, API; nitpicky -W clean), README, CHANGELOG. Installed via apt: python3-sphinx, python3-hatchling, dh-python, pybuild-plugin-pyproject, debhelper, devscripts, python3-build.
- 2026-10-20: M13 done: wheel/sdist build clean (python3 -m build --no-isolation); debian/ (pybuild + dh-sequence-python3, tests run at build, manpage); dpkg-buildpackage -b OK, lintian only ITP warning; .deb smoke-tested then removed. Next: quality pass (ruff, mypy --strict), review, then report to user.
- 2026-10-21: Final pass done. ruff check + ruff format clean (RUF002 ignored: intentional typography);
  mypy --strict clean; JitterBuffer.reset no longer calls __init__; examples keep refs to per-call tasks.
  Release prep: PyPI name `iax2` unclaimed; sdist/wheel build + `twine check --strict` pass; py.typed shipped.
  Tests: 69 unit + 23 interop pass (before and after). Remaining before an actual release (owner's call):
  bump 0.1.0.dev0 -> 0.1.0, CHANGELOG date, tag, `twine upload`.

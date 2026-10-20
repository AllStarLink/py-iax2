# py-iax2

A complete, pure-Python implementation of the **IAX2** VoIP protocol
([RFC 5456](https://www.rfc-editor.org/rfc/rfc5456)) for `asyncio`, built to
interoperate with **Asterisk** — including **AllStarLink** `app_rpt` nodes.
Where the RFC is vague or Asterisk differs, py-iax2 does what Asterisk 22 LTS
does, and its test suite runs against a live Asterisk.

```python
import asyncio
import iax2

async def main():
    async with iax2.Endpoint(port=0) as ep:
        call = await ep.dial("pbx.example.org", "600", username="me", secret="s3cret")
        await call.play_pcm(iax2.audio.tone(440, 2000))
        await call.hangup()

asyncio.run(main())
```

## Features

- **Calls**, outgoing and incoming, with Asterisk-exact sequencing, ACKs,
  retransmission and timestamps; ringing, answer, reject, hangup causes.
- **Authentication**: MD5, RSA (Asterisk key files) and plaintext;
  **call tokens** as client and server.
- **AES-128 encryption** with key rotation, compatible with Asterisk's
  `encryption=aes128`.
- **Media**: μ-law, A-law, slin, slin16 built in (no dependencies), pluggable
  codecs, payload pass-through for any format, adaptive jitter buffer,
  trunking, video, DTMF, text, URL and image frames.
- **Registration** client and registrar, **POKE** qualification, PING/LAGRQ
  statistics, dialplan queries, **native transfers** (endpoint and server).
- A **blocking API** (`iax2.sync`) for non-asyncio code.
- `iax2-tool` CLI: poke, call, serve, register, decode.
- Examples: echo server, mini PBX, AllStarLink client, and more.

## Install

```console
pip install iax2              # core, pure Python
pip install "iax2[crypto]"    # + RSA authentication and AES encryption
```

Python 3.11+. A `debian/` directory builds a `python3-iax2` package.

## Answering calls

```python
import asyncio
import iax2

async def echo(call):
    await call.answer()
    async for frame in call.audio():
        call.send_audio(frame.payload)

async def main():
    users = {"alice": iax2.Credentials(secret="s3cret")}
    async with iax2.Endpoint(port=4569, users=users, allow_guest=False) as ep:
        async for call in ep.incoming():
            asyncio.create_task(echo(call))

asyncio.run(main())
```

## Command line

```console
iax2-tool poke pbx.example.org
iax2-tool call pbx.example.org 600 -u me -s s3cret --tone 440 --record out.wav
iax2-tool serve -u alice:s3cret
```

## Documentation

Full documentation (user guide, Asterisk/AllStarLink notes, protocol notes,
API reference) is in `docs/`; build it with
`sphinx-build -b html docs docs/_build/html`.

## Testing

```console
pytest                                          # unit + loopback tests
sudo python3 tests/interop/setup_asterisk.py    # configure a *test* Asterisk
pytest -m interop tests/interop                 # interoperability tests
```

## License

AGPL-3.0-or-later. Copyright © 2026 Jason D. McCormick.

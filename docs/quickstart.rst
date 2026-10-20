Quick start
===========

Everything starts with an :class:`~iax2.Endpoint`: one UDP socket that can
place calls, accept calls and register, all at the same time.

Placing a call
--------------

.. code-block:: python

    import asyncio
    import iax2
    from iax2 import audio

    async def main():
        async with iax2.Endpoint(port=0) as ep:          # port 0: any free port
            call = await ep.dial(
                "pbx.example.org", "600",                 # host, extension
                username="me", secret="s3cret",           # MD5 authentication
                caller_number="1001", caller_name="Me",
            )
            # dial() returns when the call is answered.
            await call.play_pcm(audio.read_wav("hello.wav"))
            frame = await call.recv_audio(timeout=1)      # what the far end sent
            await call.hangup()

    asyncio.run(main())

``dial()`` raises :class:`~iax2.CallRejected` if the call is rejected, busy
or hung up before answer, and :class:`~iax2.CallTimeout` if the far end
stops responding.

Answering calls
---------------

.. code-block:: python

    async def handle(call: iax2.Call):
        print("call from", call.info.calling_number, "to", call.info.called_number)
        await call.answer()
        async for frame in call.audio():                  # until hangup
            call.send_audio(frame.payload)                # echo it back

    async def main():
        users = {"alice": iax2.Credentials(secret="s3cret")}
        async with iax2.Endpoint(port=4569, users=users, allow_guest=False) as ep:
            async for call in ep.incoming():
                asyncio.create_task(handle(call))

    asyncio.run(main())

Callers must authenticate as ``alice`` with MD5; anybody else is rejected,
exactly as Asterisk would do with an ``iax.conf`` user.

Events
------

Everything that happens on a call is reported as an event:

.. code-block:: python

    async for ev in call.events():
        match ev:
            case iax2.Ringing():
                print("ringing")
            case iax2.DTMFEvent(digit=d, begin=False):
                print("pressed", d)
            case iax2.TextEvent(text=t):
                print("text:", t)
            case iax2.HangupEvent(cause_code=c):
                print("hung up, cause", c)

Without asyncio
---------------

.. code-block:: python

    from iax2.sync import SyncEndpoint

    with SyncEndpoint(port=0) as ep:
        call = ep.dial("pbx.example.org", "600", username="me", secret="s3cret")
        call.send_dtmf("1234")
        call.hangup()

See :doc:`guide/sync`.

From the command line
---------------------

.. code-block:: console

    $ iax2-tool poke pbx.example.org
    $ iax2-tool call pbx.example.org 600 -u me -s s3cret --tone 440 --record out.wav
    $ iax2-tool serve -u alice:s3cret            # an echo server

See :doc:`cli`.

Authentication and security
===========================

Authentication methods
----------------------

=============  ==========================================================  ============
Method         How                                                         Asterisk
=============  ==========================================================  ============
MD5            response = md5(challenge + secret) as hex                   ``auth=md5``
RSA            base64 RSA-SHA1 signature of the challenge                  ``auth=rsa``
Plaintext      the secret itself is sent (avoid)                           ``auth=plaintext``
=============  ==========================================================  ============

As a client pass ``secret=`` (MD5, falling back to plaintext if that is all
the server offers) and/or ``private_key=`` (RSA, preferred when offered).
As a server describe each user with :class:`~iax2.Credentials`; offered
methods default to MD5 when a secret is set and RSA when public keys are.

RSA keys
--------

Asterisk keeps RSA keys as PEM files: ``name.pub`` (public) and
``name.key`` (private, often 3DES-encrypted) in its key directory
(``/usr/share/asterisk/keys`` on ASL3/Debian). Asterisk only supports
1024-bit keys. :class:`~iax2.RSAKeyStore` loads them by name:

.. code-block:: python

    keys = iax2.RSAKeyStore("/usr/share/asterisk/keys", passphrases={"mynode": "pw"})
    call = await ep.dial(host, "100", username="mynode", private_key=keys.private("mynode"))
    users = {"node": iax2.Credentials(public_keys=[keys.public("allstar")])}

Create a compatible key pair with OpenSSL (or Asterisk's ``astgenkey``)::

    openssl genrsa -traditional -out mynode.key 1024
    openssl rsa -in mynode.key -pubout -out mynode.pub

RSA needs ``pip install iax2[crypto]``.

Encryption
----------

Asterisk-compatible AES-128 encryption protects everything after the call
numbers. It requires MD5 authentication (the key is derived from the
challenge and the secret):

.. code-block:: python

    enc = iax2.Encryption.AES128 | iax2.Encryption.KEYROTATE
    ep = iax2.Endpoint(encryption=enc)                         # offer/accept it
    ep = iax2.Endpoint(encryption=enc, force_encryption=True)  # require it

Asterisk users/peers need ``encryption=aes128`` (and
``forceencryption=yes`` to require it). With ``KEYROTATE`` the side
sending voice changes its key every 2–5 minutes. Note that this is
Asterisk's design: unauthenticated CBC with no integrity check — it hides
content from eavesdroppers but is not a modern secure channel. Use a VPN
when that matters.

Call tokens
-----------

See :doc:`server`. Clients always offer call tokens and resend
transparently when the server issues one.

Hardening checklist
-------------------

* Prefer RSA or MD5; never plaintext on untrusted networks.
* Keep ``require_calltoken=True`` unless ancient clients need it.
* Consider ``delay_reject=True`` to slow down password guessing.
* Use an ``authenticator`` to rate-limit or allow-list source addresses.

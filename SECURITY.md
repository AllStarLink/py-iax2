# Security policy

## Supported versions

py-iax2 is pre-1.0. Security fixes are made on `main` and released in the
next version; only the latest release is supported.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately through GitHub's
[private vulnerability reporting](https://github.com/AllStarLink/py-iax2/security/advisories/new),
or by email to the maintainer listed in `pyproject.toml`. Include the version,
a description of the issue and, if possible, a packet capture or script that
reproduces it.

You should get an acknowledgement within a week. Once a fix is ready we will
publish an advisory and credit you unless you prefer otherwise.

## Scope

In scope: anything a remote IAX2 peer can do to an application using this
library: crashes, unbounded memory or CPU use, authentication or call-token
bypass, encryption weaknesses in our implementation, call hijacking.

Out of scope: weaknesses of the IAX2 protocol itself (for example, MD5
challenge/response and plaintext authentication are inherently weak, and
signalling is unencrypted unless encryption is negotiated), and
misconfiguration of the application using the library.

## Hardening an internet-facing endpoint

* Leave `allow_guest=False` (the default) unless you really want
  unauthenticated callers; `True` behaves like a permissive Asterisk `guest`
  user.
* Keep `require_calltoken=True` (the default). Call tokens stop spoofed-source
  floods from allocating call numbers.
* Lower `max_callnumbers_per_ip` (default 2048, as Asterisk) if you expect few
  sessions per peer.
* Prefer RSA or MD5 authentication over plaintext, and set
  `force_encryption=True` where both ends support AES-128.

"""Tests for authentication helpers and call tokens."""

from __future__ import annotations

import base64

import pytest

from iax2.auth import (
    Credentials,
    RSAKeyStore,
    build_auth_reply,
    md5_response,
    new_challenge,
    rsa_sign,
    rsa_verify,
    verify_auth_reply,
)
from iax2.calltoken import CallTokenGenerator, format_addr
from iax2.constants import IE, AuthMethod
from iax2.exceptions import AuthenticationError
from iax2.ies import IEs

rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key, key.public_key()


def test_challenge_format():
    c = new_challenge()
    assert c.isdigit() and len(c) <= 9


def test_md5_matches_known_value():
    assert md5_response("123456", "s3cret") == "5ed1f0dc9003f2a40604fea3cf6cd609"


def test_client_prefers_rsa_then_md5_then_plaintext(keypair):
    priv, _ = keypair
    creds = Credentials(secret="pw", private_key=priv)
    _, m = build_auth_reply(creds, AuthMethod.RSA | AuthMethod.MD5, "1")
    assert m is AuthMethod.RSA
    _, m = build_auth_reply(creds, AuthMethod.MD5 | AuthMethod.PLAINTEXT, "1")
    assert m is AuthMethod.MD5
    ies, m = build_auth_reply(creds, AuthMethod.PLAINTEXT, "1")
    assert m is AuthMethod.PLAINTEXT and ies.get(IE.PASSWORD) == "pw"
    with pytest.raises(AuthenticationError):
        build_auth_reply(Credentials(), AuthMethod.MD5, "1")


def test_md5_verify_multiple_secrets_case_insensitive():
    server = Credentials(secret="a;b;c")
    reply = IEs().add(IE.MD5_RESULT, md5_response("99", "b").upper())
    assert verify_auth_reply(server, "99", reply) is AuthMethod.MD5
    bad = IEs().add(IE.MD5_RESULT, md5_response("99", "x"))
    assert verify_auth_reply(server, "99", bad) is None
    assert verify_auth_reply(server, "99", IEs()) is None


def test_plaintext_verify():
    server = Credentials(secret="pw", methods=AuthMethod.PLAINTEXT)
    assert verify_auth_reply(server, "1", IEs().add(IE.PASSWORD, "pw")) is AuthMethod.PLAINTEXT
    assert verify_auth_reply(server, "1", IEs().add(IE.PASSWORD, "no")) is None


def test_rsa_round_trip(keypair):
    priv, pub = keypair
    sig = rsa_sign(priv, "12345")
    assert len(base64.b64decode(sig)) == 128
    assert rsa_verify(pub, "12345", sig)
    assert not rsa_verify(pub, "12346", sig)
    assert not rsa_verify(pub, "12345", "not base64!!")
    server = Credentials(public_keys=[pub])
    assert server.offered_methods() == AuthMethod.RSA
    ies, _ = build_auth_reply(Credentials(private_key=priv), server.offered_methods(), "777")
    assert verify_auth_reply(server, "777", ies) is AuthMethod.RSA


def test_keystore_loads_asterisk_layout(tmp_path, keypair):
    from cryptography.hazmat.primitives import serialization

    priv, pub = keypair
    (tmp_path / "node.key").write_bytes(
        priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.BestAvailableEncryption(b"pass"),
        )
    )
    (tmp_path / "node.pub").write_bytes(
        pub.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    store = RSAKeyStore(tmp_path, passphrases={"node": "pass"})
    sig = rsa_sign(store.private("node"), "42")
    assert rsa_verify(store.public("node"), "42", sig)


def test_calltoken():
    gen = CallTokenGenerator(secret=1234)
    tok = gen.issue("192.0.2.1", 4569, now=1000)
    ts, _, h = tok.partition("?")
    assert ts == "1000" and len(h) == 40
    assert gen.validate(tok, "192.0.2.1", 4569, now=1005)
    assert not gen.validate(tok, "192.0.2.1", 4569, now=1010)  # expired (>= max_delay)
    assert not gen.validate(tok, "192.0.2.2", 4569, now=1001)  # wrong addr
    assert not gen.validate(tok, "192.0.2.1", 4569, now=999)  # from the future
    assert not gen.validate(b"garbage", "192.0.2.1", 4569)
    assert format_addr("::1", 5) == "[::1]:5"

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configure a local Asterisk for the py-iax2 interoperability tests.

Writes ``/etc/asterisk/custom/iax.conf`` and ``custom/extensions.conf``
(both are ``#tryinclude``d by the ASL3 stock configuration), installs an RSA
test key pair and reloads chan_iax2 and the dialplan. Requires root (sudo).

DO NOT run this on a production node: it adds test users and extensions.

Usage::

    sudo python3 tests/interop/setup_asterisk.py          # install
    sudo python3 tests/interop/setup_asterisk.py --remove # uninstall
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ETC = Path("/etc/asterisk/custom")
KEYS = Path("/usr/share/asterisk/keys")  # astdatadir/keys
MARK = "; managed by py-iax2 interop tests"

#: Port our library listens on when Asterisk calls *it*.
LIB_PORT = 4570

IAX_CONF = f"""{MARK}
[pyiax-md5]
type=friend
host=dynamic
secret=pyiaxsecret
auth=md5
context=pyiax-test
requirecalltoken=yes
disallow=all
allow=ulaw
allow=alaw
allow=gsm
allow=slin
transfer=no

[pyiax-plain]
type=user
auth=plaintext
secret=plainpw
context=pyiax-test
requirecalltoken=no
disallow=all
allow=alaw

[pyiax-rsa]
type=user
auth=rsa
inkeys=pyiax-test
context=pyiax-test
disallow=all
allow=ulaw

[pyiax-out]
type=peer
host=127.0.0.1
port={LIB_PORT}
username=asterisk
secret=outsecret
auth=md5
requirecalltoken=yes
disallow=all
allow=ulaw
allow=slin
transfer=no

[pyiax-enc]
type=user
secret=encsecret
auth=md5
encryption=aes128
forceencryption=yes
context=pyiax-test
disallow=all
allow=ulaw

[pyiax-out-enc]
type=peer
host=127.0.0.1
port={LIB_PORT}
username=asterisk
secret=outsecret
auth=md5
encryption=aes128
forceencryption=yes
disallow=all
allow=ulaw

[pyiax-trunk]
type=friend
host=dynamic
secret=pyiaxsecret
auth=md5
context=pyiax-test
trunk=yes
disallow=all
allow=ulaw
"""

EXT_CONF = f"""{MARK}
[pyiax-test]
; 100: echo
exten => 100,1,Answer()
 same => n,Echo()
; 101: ring, answer, play 1 kHz tone, hang up
exten => 101,1,Ringing()
 same => n,Wait(0.5)
 same => n,Answer()
 same => n,Playtones(1000)
 same => n,Wait(2)
 same => n,Hangup(16)
; 102: busy
exten => 102,1,Busy(5)
; 103: answer, send text and DTMF, hang up
exten => 103,1,Answer()
 same => n,Wait(0.5)
 same => n,SendText(hello from asterisk)
 same => n,SendDTMF(123#,250)
 same => n,Wait(1)
 same => n,Hangup(16)
; 104: read one DTMF digit and send it back as text, plus the PYVAR variable
exten => 104,1,Answer()
 same => n,Read(DIG,,1,,,5)
 same => n,SendText(got ${{DIG}} var=${{IAXVAR(PYVAR)}} cid=${{CALLERID(num)}})
 same => n,Wait(1)
 same => n,Hangup(16)
; 105: congestion
exten => 105,1,Congestion(5)
; 106: answer and wait (keep-alive / long call tests)
exten => 106,1,Answer()
 same => n,Wait(60)
 same => n,Hangup()
; 107: reject without answering (cause 21)
exten => 107,1,Hangup(21)
; 108: answer then quelch-ish hold and unhold
exten => 108,1,Answer()
 same => n,Wait(0.5)
 same => n,Hangup(17)
"""


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def ensure_key() -> None:
    """Create the RSA test key: public half for Asterisk, private half for tests."""
    priv = HERE / "pyiax-test.key"
    pub = HERE / "pyiax-test.pub"
    if not priv.exists():
        run("openssl", "genrsa", "-traditional", "-out", str(priv), "1024")
        run("openssl", "rsa", "-in", str(priv), "-pubout", "-out", str(pub))
        uid = int(os.environ.get("SUDO_UID", os.getuid()))
        gid = int(os.environ.get("SUDO_GID", os.getgid()))
        for p in (priv, pub):
            os.chown(p, uid, gid)
    KEYS.mkdir(parents=True, exist_ok=True)
    (KEYS / "pyiax-test.pub").write_bytes(pub.read_bytes())
    os.chmod(KEYS / "pyiax-test.pub", 0o644)


#: Dialplan applications the test extensions use (ASL3 uses ``autoload=no``).
MODULES = ("app_echo.so", "app_playtones.so", "app_senddtmf.so", "app_sendtext.so",
           "app_read.so", "res_indications.so", "res_clioriginate.so")


def reload() -> None:
    for mod in MODULES:
        subprocess.run(["asterisk", "-rx", f"module load {mod}"], check=False,
                       capture_output=True)
    for cmd in ("keys init", "module reload res_crypto.so", "iax2 reload", "dialplan reload"):
        subprocess.run(["asterisk", "-rx", cmd], check=False, capture_output=True)


def install() -> None:
    ETC.mkdir(parents=True, exist_ok=True)
    for name, text in (("iax.conf", IAX_CONF), ("extensions.conf", EXT_CONF)):
        path = ETC / name
        if path.exists() and MARK not in path.read_text():
            sys.exit(f"{path} exists and is not managed by py-iax2; refusing to overwrite")
        path.write_text(text)
    ensure_key()
    reload()
    print("Asterisk configured for py-iax2 interop tests")


def remove() -> None:
    for name in ("iax.conf", "extensions.conf"):
        path = ETC / name
        if path.exists() and MARK in path.read_text():
            path.unlink()
    (KEYS / "pyiax-test.pub").unlink(missing_ok=True)
    reload()
    print("py-iax2 interop configuration removed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--remove", action="store_true", help="remove the test configuration")
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must be run as root (sudo)")
    remove() if args.remove else install()

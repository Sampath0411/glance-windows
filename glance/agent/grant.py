"""
Grant writing for the Glance agent.

The C++ provider (provider/GlanceGrant.cpp) is the reference for this format. If
these two ever disagree by a single byte, unlock stops working - so the byte layout
is duplicated deliberately and checked by tests/test_grant.py rather than shared through
some generated header.

File layout, 84 bytes, little-endian:

    0   "GLNC"
    4   uint16 version (1)
    6   uint16 reserved (0)
    8   uint32 session id
   12   uint32 issued  (unix seconds)
   16   uint32 expires (unix seconds)
   20   16-byte nonce
   36   16-byte SHA-256(utf8(SID))[0:16]
   52   32-byte HMAC-SHA256(key, bytes 0..51)

The key lives at HKLM\\SOFTWARE\\Glance\\GrantKey. It is readable by users on purpose:
the agent runs in the user session, and the threat model behind this whole feature is a
stranger at a locked laptop, not malware running as the account owner. See PHASE3.md.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import subprocess
import time
from pathlib import Path

MAGIC = b"GLNC"
VERSION = 1
BODY_SIZE = 52
FILE_SIZE = 84
KEY_SIZE = 32
DEFAULT_TTL = 10

GRANT_PATH = Path(r"C:\ProgramData\Glance\grant.bin")
LOG_PATH = Path(r"C:\ProgramData\Glance\logs\agent.log")


def read_key() -> bytes:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Glance") as key:
        value, kind = winreg.QueryValueEx(key, "GrantKey")
    if kind != winreg.REG_BINARY or len(value) != KEY_SIZE:
        raise RuntimeError(f"GrantKey is {len(value)} bytes, expected {KEY_SIZE}")
    return bytes(value)


def current_sid() -> str:
    """The SID of the account the agent runs as.

    whoami is used rather than a ctypes TOKEN_USER walk: it is one call at startup,
    and it cannot silently return the wrong length the way a hand-rolled struct can.
    """
    output = subprocess.run(
        ["whoami", "/user"], capture_output=True, text=True, timeout=10
    ).stdout
    for token in output.replace("\r", " ").replace("\n", " ").split():
        if token.startswith("S-1-"):
            return token.strip()
    raise RuntimeError(f"could not find a SID in whoami output: {output!r}")


def session_id() -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    # GetCurrentProcessId(), not GetCurrentProcess(): a HANDLE returned without an
    # explicit restype is truncated to 32 bits and every call fails silently.
    value = wintypes.DWORD(0)
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(value)):
        return 0
    return int(value.value)


def sid_hash(sid: str) -> bytes:
    return hashlib.sha256(sid.encode("utf-8")).digest()[:16]


def build(key: bytes, sid: str, session: int, issued: int, expires: int,
          nonce: bytes) -> bytes:
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(nonce) != 16:
        raise ValueError("nonce must be 16 bytes")

    body = (MAGIC
            + struct.pack("<HHIII", VERSION, 0, session, issued, expires)
            + nonce + sid_hash(sid))
    assert len(body) == BODY_SIZE, len(body)
    return body + hmac.new(key, body, hashlib.sha256).digest()


def write_grant(ttl: int = DEFAULT_TTL, path: Path = GRANT_PATH) -> Path:
    """Writes atomically: the provider must never read a half-written grant.

    os.replace on the same volume is atomic, so a reader sees either the old file
    (absent, usually) or the complete new one.
    """
    key = read_key()
    sid = current_sid()
    session = session_id()
    now = int(time.time())

    data = build(key, sid, session, now, now + ttl, os.urandom(16))
    assert len(data) == FILE_SIZE

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def clear_pending_grant(path: Path = GRANT_PATH) -> bool:
    """Revoke an unconsumed Glance authorization; absence already means revoked."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def main() -> int:
    """Diagnostics: proves the key, SID and session are all obtainable."""
    import argparse

    parser = argparse.ArgumentParser(description="Glance grant diagnostics")
    parser.add_argument("--write", action="store_true", help="write a grant now")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    args = parser.parse_args()

    try:
        key = read_key()
        print(f"key              : present ({len(key)} bytes)")
    except Exception as error:  # noqa: BLE001 - diagnostics should not traceback
        print(f"key              : UNAVAILABLE - {error}")
        return 1

    sid = current_sid()
    assert session_id() >= 0
    print(f"account sid      : {sid}")
    print(f"session id       : {session_id()}")
    print(f"sid hash         : {sid_hash(sid).hex()}")

    if args.write:
        path = write_grant(args.ttl)
        print(f"wrote            : {path} ({path.stat().st_size} bytes, ttl {args.ttl}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

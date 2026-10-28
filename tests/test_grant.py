"""
Cross-language test of the grant format.

The Python agent writes grants; the C++ provider validates them. If the two ever
disagree about a single byte, unlocking silently never works - or worse, works when it
should not. So the C++ validator is tested against grants produced independently here,
including every way a grant can be wrong.

Run:  .venv\\Scripts\\python.exe -m tests.test_grant
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import subprocess
import tempfile
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "provider" / "build" / "grant_tool.exe"
KEY = bytes(range(32))
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"

MAGIC = b"GLNC"
BODY_SIZE = 52
FILE_SIZE = 84

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def sid_hash(sid: str) -> bytes:
    return hashlib.sha256(sid.encode("utf-8")).digest()[:16]


def build_grant(key: bytes, sid: str, session: int,
                issued: int, expires: int, nonce: bytes = b"\x07" * 16) -> bytes:
    body = (MAGIC
            + struct.pack("<HHIII", 1, 0, session, issued, expires)
            + nonce + sid_hash(sid))
    assert len(body) == BODY_SIZE, len(body)
    return body + hmac.new(key, body, hashlib.sha256).digest()


def verify(path: Path, sid: str = SID, session: int = 1, key: bytes = KEY) -> tuple[int, str]:
    proc = subprocess.run(
        [str(TOOL), "verify", "--path", str(path), "--sid", sid,
         "--session", str(session), "--key-hex", key.hex()],
        capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    if not TOOL.exists():
        print(f"missing {TOOL} - run provider\\build.ps1 first", flush=True)
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="glance_grant_"))
    now = int(time.time())

    # 1. the happy path
    good = workdir / "good.bin"
    good.write_bytes(build_grant(KEY, SID, 1, now, now + 10))
    code, out = verify(good)
    check("valid grant verifies", code == 0 and out == "ok", out)

    # 2. one flipped byte anywhere in the body
    tampered = bytearray(good.read_bytes())
    tampered[12] ^= 0x01  # issued timestamp
    p = workdir / "tampered.bin"
    p.write_bytes(bytes(tampered))
    code, out = verify(p)
    check("tampered timestamp rejected", code != 0 and "signature invalid" in out, out)

    # 3. forged with a different key
    p = workdir / "wrongkey.bin"
    p.write_bytes(build_grant(bytes([9]) * 32, SID, 1, now, now + 10))
    code, out = verify(p)
    check("forged with another key rejected", code != 0 and "signature invalid" in out, out)

    # 4. wrong account
    p = workdir / "wrongsid.bin"
    p.write_bytes(build_grant(KEY, "S-1-5-21-9-9-9-500", 1, now, now + 10))
    code, out = verify(p)
    check("grant for another account rejected", code != 0 and "another account" in out, out)

    # 5. wrong session
    p = workdir / "wrongsession.bin"
    p.write_bytes(build_grant(KEY, SID, 7, now, now + 10))
    code, out = verify(p)
    check("grant from another session rejected", code != 0 and "another session" in out, out)

    # 6. expired
    p = workdir / "expired.bin"
    p.write_bytes(build_grant(KEY, SID, 1, now - 60, now - 10))
    code, out = verify(p)
    check("expired grant rejected", code != 0 and "expired" in out, out)

    # 7. issued in the future
    p = workdir / "future.bin"
    p.write_bytes(build_grant(KEY, SID, 1, now + 120, now + 130))
    code, out = verify(p)
    check("future grant rejected", code != 0 and "future" in out, out)

    # 8. lifetime longer than policy
    p = workdir / "long.bin"
    p.write_bytes(build_grant(KEY, SID, 1, now, now + 600))
    code, out = verify(p)
    check("over-long lifetime rejected", code != 0 and "lifetime" in out, out)

    # 9. wrong size
    p = workdir / "short.bin"
    p.write_bytes(good.read_bytes()[:40])
    code, out = verify(p)
    check("truncated grant rejected", code != 0 and "wrong size" in out, out)

    # 10. oversized - must be refused without reading it all
    p = workdir / "huge.bin"
    p.write_bytes(good.read_bytes() + b"\0" * 4096)
    code, out = verify(p)
    check("oversized grant rejected", code != 0 and "too large" in out, out)

    # 11. missing
    code, out = verify(workdir / "does-not-exist.bin")
    check("missing grant reports missing", code != 0 and "no grant file" in out, out)

    # 12. bad magic
    p = workdir / "magic.bin"
    body = bytearray(build_grant(KEY, SID, 1, now, now + 10))
    body[0] = ord("X")
    p.write_bytes(bytes(body))
    code, out = verify(p)
    check("bad magic rejected", code != 0 and "not a Glance grant" in out, out)

    # 13. the C++ writer and the C++ reader agree
    p = workdir / "cpp.bin"
    proc = subprocess.run(
        [str(TOOL), "create", "--out", str(p), "--sid", SID, "--session", "1",
         "--ttl", "10", "--key-hex", KEY.hex()],
        capture_output=True, text=True)
    code, out = verify(p)
    check("C++ create -> C++ verify", proc.returncode == 0 and code == 0, out)

    # 14. python can read back what C++ wrote (byte-level agreement)
    raw = p.read_bytes()
    expected_mac = hmac.new(KEY, raw[:BODY_SIZE], hashlib.sha256).digest()
    check("C++ grant has the MAC Python would compute", raw[52:] == expected_mac)
    check("C++ grant has the SID hash Python would compute", raw[36:52] == sid_hash(SID))
    session_le = struct.unpack("<I", raw[8:12])[0]
    check("C++ grant encodes the session little-endian", session_le == 1, str(session_le))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

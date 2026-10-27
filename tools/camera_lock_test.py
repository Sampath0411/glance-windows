"""
Can a locked Windows session still read the camera?

This decides the Phase 3 architecture. If capture keeps working while the screen is
locked, the agent can do face matching in the user session and the provider stays thin.
If the OS blocks the camera on the lock screen, all capture has to move into
LogonUI.exe as SYSTEM - which means ONNX Runtime inside the logon process on an i3
with 8 GB. Big difference in risk, so measure it.

It also records which input desktop is active, so "was it actually locked?" is proven
rather than assumed. Locked sessions report the input desktop as Winlogon.

Run:  .venv\\Scripts\\python.exe tools\\camera_lock_test.py --seconds 150
Then lock the screen (Win+L) for ~10 seconds while it runs.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path
from ctypes import wintypes

import cv2

UOI_NAME = 2
DESKTOP_READOBJECTS = 0x0001
user32 = ctypes.windll.user32
wtsapi = ctypes.windll.wtsapi32

WTS_CURRENT_SESSION = 0xFFFFFFFF
WTSSessionInfoEx = 25


class WTSINFOEX_LEVEL1_W(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.ULONG),
                ("SessionState", wintypes.INT),
                ("SessionFlags", wintypes.LONG),
                ("UserName", wintypes.WCHAR * 33),
                ("DomainName", wintypes.WCHAR * 33),
                ("WinStationName", wintypes.WCHAR * 33),
                ("ConnectState", wintypes.INT)]


class WTSINFOEXW(ctypes.Structure):
    _fields_ = [("Level", wintypes.ULONG), ("Data", WTSINFOEX_LEVEL1_W)]


def session_flags() -> int | None:
    """Raw WTSINFOEX SessionFlags. Calibrated empirically on this machine:
    0 = unlocked (verified against live camera frames), 1 = locked."""
    buf = ctypes.POINTER(WTSINFOEXW)()
    size = wintypes.DWORD(0)
    ok = wtsapi.WTSQuerySessionInformationW(
        None, WTS_CURRENT_SESSION, WTSSessionInfoEx,
        ctypes.byref(buf), ctypes.byref(size))
    if not ok:
        return None
    try:
        return int(buf.contents.Data.SessionFlags)
    finally:
        wtsapi.WTSFreeMemory(buf)


def input_desktop_name() -> str | None:
    handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong(0)
        ok = user32.GetUserObjectInformationW(
            handle, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed))
        return buf.value if ok else None
    finally:
        user32.CloseDesktop(handle)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--out", default="data/lock_probe.log")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("camera did not open", flush=True)
        return 1

    print(f"probing for {args.seconds}s - lock the screen now (Win+L) for ~10s", flush=True)
    rows = []
    started = time.time()
    try:
        while time.time() - started < args.seconds:
            ok, frame = cap.read()
            row = {
                "t": round(time.time() - started, 2),
                "desktop": input_desktop_name() or "unknown",
                "flags": session_flags(),
                "read_ok": bool(ok),
                "brightness": round(float(frame.mean()), 1) if ok and frame is not None else None,
                "shape": list(frame.shape) if ok and frame is not None else None,
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
            time.sleep(0.5)
    finally:
        cap.release()
        out.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    locked = [r for r in rows if r.get("flags") == 1]
    good = [r for r in rows if r["read_ok"]]
    locked_good = [r for r in locked if r["read_ok"]]

    print("\n--- summary ---", flush=True)
    print(f"samples           : {len(rows)}", flush=True)
    print(f"frames read ok    : {len(good)}", flush=True)
    print(f"locked samples    : {len(locked)}  (flags: "
          f"{sorted({r.get('flags') for r in rows}, key=str)})", flush=True)
    print(f"frames ok LOCKED  : {len(locked_good)}", flush=True)
    if locked:
        print(f"locked brightness : {[r['brightness'] for r in locked[:12]]}", flush=True)
        zeros = [r for r in locked if r["brightness"] == 0.0]
        print(f"black while locked: {len(zeros)}/{len(locked)}", flush=True)
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

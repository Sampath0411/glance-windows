"""
Decides the Phase 3 architecture: can a locked session still capture?

Waits for you to lock the screen instead of running on a timer, so there is nothing to
time. Records continuously and stops once it has seen enough locked samples.

It answers two separate questions, because they can differ:

  A. does an ALREADY OPEN camera keep delivering real pixels across a lock?
  B. can a NEW camera handle be opened while the session is locked?

If A is yes, the agent design works. If A is no but B is yes, the agent can reopen on
lock. If both are no, capture has to move into LogonUI as SYSTEM.

Lock state comes from WTSINFOEX SessionFlags, calibrated on this machine as
0 = unlocked (verified against live frames at brightness ~123), 1 = locked.

Run:  .venv\\Scripts\\python.exe tools\\lock_capture_probe.py
Then press Win+L, wait ~10 seconds, and sign back in.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path

import cv2

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


def mean(frame) -> float | None:
    return round(float(frame.mean()), 1) if frame is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900, help="give up after this long")
    ap.add_argument("--need", type=int, default=10, help="locked samples required")
    ap.add_argument("--out", default="data/lock_probe3.log")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    log = out.open("w", encoding="utf-8")

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("camera did not open", flush=True)
        return 1

    print("waiting for the screen to lock - press Win+L whenever you are ready, "
          "wait ~10s, then sign back in", flush=True)

    started = time.time()
    locked_seen = 0
    fresh_open_result = None
    done_at = None

    try:
        while time.time() - started < args.timeout:
            ok, frame = cap.read()
            flags = session_flags()
            row = {
                "t": round(time.time() - started, 2),
                "flags": flags,
                "read_ok": bool(ok),
                "brightness": mean(frame) if ok else None,
            }
            log.write(json.dumps(row) + "\n")
            log.flush()

            if flags == 1:
                locked_seen += 1
                if fresh_open_result is None:
                    # question B: fresh handle while locked
                    fresh = cv2.VideoCapture(0, cv2.CAP_DSHOW)
                    opened = fresh.isOpened()
                    fok, fframe = fresh.read() if opened else (False, None)
                    fresh.release()
                    fresh_open_result = {
                        "t": row["t"], "opened": opened,
                        "read_ok": bool(fok), "brightness": mean(fframe) if fok else None,
                    }
                    log.write(json.dumps({"fresh_open": fresh_open_result}) + "\n")
                    log.flush()
                # once we have enough locked samples and we are back out, stop
                if locked_seen >= args.need and done_at is None:
                    done_at = time.time()
                if done_at is not None and time.time() - done_at > 5:
                    break
            elif done_at is not None:
                break

            time.sleep(0.5)
    finally:
        cap.release()
        log.close()

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    samples = [r for r in rows if "flags" in r]
    locked = [r for r in samples if r.get("flags") == 1]
    unlocked = [r for r in samples if r.get("flags") == 0]

    print("\n--- result ---", flush=True)
    print(f"samples {len(samples)}: unlocked {len(unlocked)}, locked {len(locked)}", flush=True)
    if not locked:
        print("NEVER LOCKED - no conclusion. Re-run and press Win+L.", flush=True)
        return 2

    locked_bright = [r["brightness"] for r in locked]
    black = [b for b in locked_bright if b is not None and b < 5.0]
    print(f"A) streaming across lock: {len(locked)} samples, "
          f"black {len(black)}/{len(locked)}, "
          f"brightness {locked_bright[:8]}", flush=True)
    if fresh_open_result:
        print(f"B) fresh open while locked: opened={fresh_open_result['opened']} "
              f"read_ok={fresh_open_result['read_ok']} "
              f"brightness={fresh_open_result['brightness']}", flush=True)

    live = [b for b in locked_bright if b is not None and b >= 5.0]
    print(f"\nverdict: capture while locked = "
          f"{'WORKS' if live else 'BLOCKED/BLACK'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

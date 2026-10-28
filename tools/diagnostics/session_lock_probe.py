"""
Definitive lock test, using the API Windows itself uses to report lock/unlock:
WTSRegisterSessionNotification -> WM_WTSSESSION_CHANGE with WTS_SESSION_LOCK/UNLOCK.

Sample-order reasoning, so a missing lock event is not a problem: if the session is
already locked when this starts, the first event we see will be UNLOCK. Every camera
sample recorded BEFORE that event was therefore taken while locked. That makes the
result independent of guessing what SessionFlags or screen captures mean.

Run:  .venv\\Scripts\\python.exe tools\\session_lock_probe.py --minutes 30
It exits early once it has seen an UNLOCK event and at least 5 samples before it.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path

import cv2

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi = ctypes.windll.wtsapi32

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
WM_DESTROY = 0x0002

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG), ("lPrivate", wintypes.DWORD)]


# Explicit signatures matter here: without them ctypes passes 64-bit handles as
# 32-bit ints, and CreateWindowExW fails with a truncated HINSTANCE.
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
wtsapi.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
wtsapi.WTSRegisterSessionNotification.restype = wintypes.BOOL
wtsapi.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
wtsapi.WTSUnRegisterSessionNotification.restype = wintypes.BOOL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--out", default="data/session_lock_probe.json")
    args = ap.parse_args()

    events: list[dict] = []
    started = time.time()

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            kind = "LOCK" if wparam == WTS_SESSION_LOCK else (
                   "UNLOCK" if wparam == WTS_SESSION_UNLOCK else f"other({wparam})")
            events.append({"t": round(time.time() - started, 2), "event": kind})
            print(f"  [{events[-1]['t']:7.2f}] session {kind}", flush=True)
            return 0
        # Must defer everything else, especially WM_NCCREATE: returning 0 there
        # aborts window creation (which is what made this fail with error 0).
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wnd_proc)
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSW()
    cls.lpfnWndProc = proc
    cls.hInstance = hinst
    cls.lpszClassName = "GlanceLockProbe"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        err = kernel32.GetLastError()
        if err != 1410:  # class already exists
            print(f"RegisterClassW failed {err}", flush=True)
            return 1

    hwnd = user32.CreateWindowExW(0, "GlanceLockProbe", "probe", 0,
                                  0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        print(f"CreateWindowExW failed: error {kernel32.GetLastError()}", flush=True)
        return 1

    if not wtsapi.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
        print("WTSRegisterSessionNotification failed", flush=True)
        return 1

    print("registered for session notifications; sampling camera every 0.5s", flush=True)
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("camera did not open", flush=True)
        return 1

    started = time.time()
    deadline = started + args.minutes * 60
    samples: list[dict] = []
    first_unlock_index = None
    msg = MSG()
    last_sample = 0.0

    while time.time() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        now = time.time()
        if now - last_sample >= 0.5:
            last_sample = now
            ok, frame = cap.read()
            samples.append({
                "t": round(now - started, 2),
                "read_ok": bool(ok),
                "brightness": round(float(frame.mean()), 1) if ok and frame is not None else None,
            })

        if first_unlock_index is None and any(e["event"] == "UNLOCK" for e in events):
            first_unlock_index = len(samples)
        if first_unlock_index is not None and first_unlock_index >= 5:
            break

        time.sleep(0.05)

    cap.release()
    wtsapi.WTSUnRegisterSessionNotification(hwnd)
    user32.DestroyWindow(hwnd)

    out = Path(args.out)
    out.write_text(json.dumps({"events": events, "samples": samples}, indent=1), encoding="utf-8")

    print("\n--- result ---", flush=True)
    print(f"events: {events}", flush=True)
    if not events:
        print("no session events: the screen was never locked or unlocked while this "
              "ran. Re-run and press Win+L when convenient.", flush=True)
        print(f"{len(samples)} camera samples recorded.", flush=True)
        return 2

    # Segment the samples by lock state. If the first event is UNLOCK, the session was
    # already locked when we started, so everything before that event is locked.
    segments = []
    locked = events[0]["event"] == "UNLOCK"
    seg_start = 0.0
    for event in events:
        segments.append((seg_start, event["t"], locked))
        locked = event["event"] == "LOCK"
        seg_start = event["t"]
    segments.append((seg_start, samples[-1]["t"] if samples else seg_start, locked))

    def stats(rows):
        values = [r["brightness"] for r in rows
                  if r["read_ok"] and r["brightness"] is not None]
        if not values:
            return "no frames"
        return (f"n={len(values)} min={min(values):.1f} "
                f"mean={sum(values) / len(values):.1f} max={max(values):.1f}")

    locked_rows: list[dict] = []
    for start_at, end_at, is_locked in segments:
        rows = [r for r in samples if start_at <= r["t"] < end_at]
        if is_locked:
            locked_rows.extend(rows)
        print(f"  {start_at:7.1f}s - {end_at:7.1f}s  "
              f"{'LOCKED  ' if is_locked else 'unlocked'}  {stats(rows)}", flush=True)

    live = [r for r in locked_rows if r["read_ok"] and (r["brightness"] or 0) > 5]
    black = [r for r in locked_rows if r["read_ok"] and (r["brightness"] or 0) <= 5]
    print(f"\nlocked samples: {len(locked_rows)}  live={len(live)}  black={len(black)}",
          flush=True)
    if not locked_rows:
        print("no locked period seen - lock the screen while this runs", flush=True)
        return 2
    print(f"verdict: capture while locked = "
          f"{'WORKS' if len(live) > len(black) else 'BLOCKED'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

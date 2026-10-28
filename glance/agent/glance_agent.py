"""
Glance agent for Windows.

Runs in the user's session and waits for the workstation to lock. On lock it opens
the camera, runs the Phase 1 recogniser and Phase 2 liveness cues, and - only on a
stable match with liveness confirmed - writes a short-lived HMAC grant. The
credential provider consumes that grant and submits the stored password.

Measured fact this design rests on: a locked Windows session can still read the
camera. That was verified with WTSRegisterSessionNotification (45 locked samples,
all live frames, zero black) before any of this was written.

It deliberately does nothing while unlocked: no camera, no polling, no grants.

    python -m glance.agent.glance_agent                # wait for lock, then unlock
    python -m glance.agent.glance_agent --simulate     # run one verification pass now
    python -m glance.agent.glance_agent --self-test    # check key/SID/camera/key model
"""

from __future__ import annotations

import argparse
import json
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from . import grant as grant_mod  # noqa: E402
from .. import liveness as lv  # noqa: E402
from ..core import MIN_FACE_FRACTION, GlancePipeline  # noqa: E402
from ..paths import RuntimePaths, resolve_paths  # noqa: E402

# --- session notifications -------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi = ctypes.windll.wtsapi32

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0


def resolve_runtime_paths(
    *,
    packaged: bool | None = None,
    project_root: Path | None = None,
    executable_dir: Path | None = None,
    local_app_data: Path | None = None,
) -> RuntimePaths:
    """Use the same source/installed data contract as the desktop application."""
    if packaged is None:
        packaged = os.environ.get("GLANCE_PACKAGED") == "1"
    return resolve_paths(
        packaged=packaged,
        project_root=Path(project_root or ROOT),
        executable_dir=executable_dir,
        local_app_data=local_app_data,
    )

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


# Without explicit signatures ctypes passes 64-bit handles as 32-bit ints, which
# makes CreateWindowExW fail with no useful error.
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
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
wtsapi.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
wtsapi.WTSRegisterSessionNotification.restype = wintypes.BOOL
wtsapi.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]


# --- single instance -------------------------------------------------------

_kernel32_checked = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32_checked.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32_checked.CreateMutexW.restype = wintypes.HANDLE
_instance_mutex = None


def claim_single_instance() -> bool:
    """Two agents fight over the camera and interleave their grants.

    use_last_error=True makes ctypes snapshot the error code immediately after the
    call, so ERROR_ALREADY_EXISTS can be trusted here. The handle is kept alive in a
    module global for the life of the process.
    """
    global _instance_mutex
    _instance_mutex = _kernel32_checked.CreateMutexW(None, False, "Local\\GlanceAgent")
    if not _instance_mutex:
        return True  # cannot tell - being able to unlock matters more than tidiness
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


# --- logging ---------------------------------------------------------------
def _log_path() -> Path:
    preferred = grant_mod.LOG_PATH
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        with open(preferred, "a", encoding="utf-8"):
            pass
        return preferred
    except OSError:
        fallback = Path.home() / "AppData" / "Local" / "Glance" / "agent.log"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


_log_file = None


def log(message: str) -> None:
    global _log_file
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    print(line, flush=True)
    try:
        if _log_file is None:
            _log_file = open(_log_path(), "a", encoding="utf-8")
        _log_file.write(line + "\n")
        _log_file.flush()
    except OSError:
        pass


# --- verification ----------------------------------------------------------

class Verifier:
    def __init__(self, threshold: float = 0.6, timeout: float = 90.0,
                 require_stable: int = 3, identity: str | None = None,
                 paths: RuntimePaths | None = None) -> None:
        self.threshold = threshold
        self.timeout = timeout
        self.require_stable = require_stable
        self.identity = identity
        self.paths = paths or resolve_runtime_paths()

    def run(self, stop: threading.Event, is_locked=None) -> str:
        """Returns 'granted', 'denied', 'cancelled', 'no-face' or 'error: ...'.

        is_locked is a callable returning the state the watcher was told about. It is
        deliberately not a desktop probe: OpenInputDesktop was tried and reported
        "unlocked" seconds after a genuine lock, which silently disabled unlocks.
        """
        if not self.identity or not self.identity.strip():
            return "error: no unlock identity selected"

        pipeline = GlancePipeline(self.paths.identities_path, self.threshold)
        if not pipeline.store.identities:
            return "error: no enrolled identities"

        if not any(identity.name == self.identity for identity in pipeline.store.identities):
            return "error: selected identity is not enrolled"

        # The selected identity is mandatory even when only one person is enrolled.
        # A missing or stale selection must never implicitly authorize everyone.

        evaluator = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
        cap = pipeline.open_camera(0)
        if not cap.isOpened():
            return "error: camera did not open"

        log(f"verifying (threshold {self.threshold:.2f}, timeout {self.timeout:.0f}s)")
        history: list[float] = []
        deadline = time.time() + self.timeout
        outcome = "no-face"
        try:
            while not stop.is_set() and time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    outcome = "error: camera read failed"
                    break

                embedding, face, _ = pipeline.embedding_for(frame)
                if embedding is None or face is None:
                    evaluator.reset()
                    history.clear()
                    continue

                height, width = frame.shape[:2]
                fraction = face.area / float(width * height)
                if fraction < MIN_FACE_FRACTION:
                    evaluator.reset()
                    history.clear()
                    continue

                match = pipeline.store.best_match(embedding, self.threshold)
                if match is None:
                    evaluator.reset()
                    history.clear()
                    continue

                identity, centroid, nearest = match
                if self.identity is not None and identity.name != self.identity:
                    evaluator.reset()
                    history.clear()
                    continue

                history.append(centroid)
                del history[:-5]

                # Deny cues read the native-resolution crop and the full frame.
                x1, y1, x2, y2 = [int(value) for value in face.bbox]
                pad = int(0.15 * max(x2 - x1, y2 - y1))
                crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
                glare = lv.extract_glare(crop)
                device = lv.detect_device_rectangle(frame, tuple(face.bbox))
                snapshot = evaluator.observe(lv.readings(glare, device, None))

                if snapshot.decision is lv.Decision.DENIED:
                    log(f"denied: {snapshot.denial_reason()}")
                    outcome = "denied"
                    break

                stable = len(history) >= self.require_stable and min(history) >= self.threshold
                if snapshot.decision is lv.Decision.CONFIRMED and stable:
                    # Trust the lock notification that started this run. It is the
                    # only signal on this machine that was measured to be reliable.
                    if is_locked is not None and not is_locked():
                        log("session unlocked mid-verification - not writing a grant")
                        outcome = "cancelled"
                        break

                    try:
                        path = grant_mod.write_grant()
                    except Exception as error:  # noqa: BLE001
                        log(f"grant write failed: {error}")
                        outcome = f"error: {error}"
                        break

                    log(f"granted {identity.name} "
                        f"(centroid {centroid:.3f}, nearest {nearest:.3f}) -> {path}")
                    outcome = "granted"
                    break
        finally:
            cap.release()
        return outcome


# --- session watcher -------------------------------------------------------

class SessionWatcher:
    def __init__(self, verifier: Verifier) -> None:
        self.verifier = verifier
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = True
        self.locked = False

    def _start_verification(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        self.stop.clear()
        self.worker = threading.Thread(target=self._verify_thread, daemon=True)
        self.worker.start()

    def _verify_thread(self) -> None:
        try:
            outcome = self.verifier.run(self.stop, is_locked=lambda: self.locked)
            log(f"verification finished: {outcome}")
        except Exception as error:  # noqa: BLE001 - the logon path must survive anything
            log(f"verification crashed: {error!r}")

    def _stop_verification(self) -> None:
        self.stop.set()
        if self.worker is not None:
            self.worker.join(timeout=5)
            self.worker = None

    def run(self) -> int:
        watcher = self

        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_WTSSESSION_CHANGE:
                if wparam == WTS_SESSION_LOCK:
                    log("session locked")
                    watcher.locked = True
                    watcher._start_verification()
                elif wparam == WTS_SESSION_UNLOCK:
                    log("session unlocked")
                    watcher.locked = False
                    watcher._stop_verification()
                return 0
            # Deferring is mandatory: returning 0 from WM_NCCREATE aborts window
            # creation, which fails with error 0 and looks like a mystery.
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        proc = WNDPROC(window_proc)
        instance = kernel32.GetModuleHandleW(None)

        window_class = WNDCLASSW()
        window_class.lpfnWndProc = proc
        window_class.hInstance = instance
        window_class.lpszClassName = "GlanceAgentWindow"
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            if kernel32.GetLastError() != 1410:  # class already exists
                log(f"RegisterClassW failed: {kernel32.GetLastError()}")
                return 1

        hwnd = user32.CreateWindowExW(0, "GlanceAgentWindow", "glance", 0,
                                      0, 0, 0, 0, None, None, instance, None)
        if not hwnd:
            log(f"CreateWindowExW failed: {kernel32.GetLastError()}")
            return 1

        if not wtsapi.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
            log(f"WTSRegisterSessionNotification failed: {kernel32.GetLastError()}")
            return 1

        log("watching for session lock")
        message = MSG()
        try:
            while self.running:
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                time.sleep(0.05)
        finally:
            wtsapi.WTSUnRegisterSessionNotification(hwnd)
            user32.DestroyWindow(hwnd)

        log("stopped")
        return 0


# --- entry point -----------------------------------------------------------

def resolve_identity(config: dict, cli_identity: str | None) -> str | None:
    """Resolve the single identity authorized to unlock.

    A configured key is authoritative, including an empty value (which fails closed).
    CLI identity remains a fallback only for diagnostics when no key is configured.
    """
    if "identity" in config:
        return str(config["identity"] or "").strip() or None
    return cli_identity.strip() if cli_identity and cli_identity.strip() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m glance.agent.glance_agent",
        description="Glance face-unlock agent",
    )
    parser.add_argument("--threshold", type=float, default=None,
                        help="match threshold (default: agent_config.json, else 0.60)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="give up this many seconds after lock (default 90)")
    parser.add_argument("--identity", default=None,
                        help="which enrolled identity may unlock (required if more "
                             "than one is enrolled)")
    parser.add_argument("--simulate", action="store_true",
                        help="run one verification pass immediately (no lock needed)")
    parser.add_argument("--self-test", action="store_true",
                        help="check key, SID, camera and enrolment, then exit")
    args = parser.parse_args()

    # The desktop app writes this file, so settings changed in the UI reach the agent
    # without rewriting the scheduled task's command line. Explicit flags still win.
    paths = resolve_runtime_paths()
    config_path = paths.config_path
    config: dict = {}
    if config_path.exists():
        try:
            loaded_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded_config, dict):
                raise ValueError("agent_config.json must contain a JSON object")
            config = loaded_config
        except (OSError, ValueError) as error:  # noqa: BLE001
            log(f"agent_config.json ignored: {error}")
            # A present but unreadable settings file is not the same as no settings:
            # falling back to an old scheduled-task argument could authorize the wrong
            # person after a failed settings write. Treat corruption as no selection.
            config = {"identity": ""}

    if args.threshold is None:
        args.threshold = float(config.get("threshold", 0.60))
    if args.timeout is None:
        args.timeout = float(config.get("timeout", 90.0))
    args.identity = resolve_identity(config, args.identity)

    if not claim_single_instance():
        log("another agent is already running - exiting")
        return 1

    if args.self_test:
        try:
            key = grant_mod.read_key()
            log(f"key: present ({len(key)} bytes)")
        except Exception as error:  # noqa: BLE001
            log(f"key: UNAVAILABLE - {error}")
        log(f"account sid: {grant_mod.current_sid()}")
        log(f"session id : {grant_mod.session_id()}")
        pipeline = GlancePipeline(paths.identities_path, args.threshold)
        log(f"identities : {[i.name for i in pipeline.store.identities]}")
        cap = pipeline.open_camera(0)
        if cap.isOpened():
            ok, frame = cap.read()
            log(f"camera     : open, frame {frame.shape if ok else 'read failed'}")
        else:
            log("camera     : did not open")
        cap.release()
        return 0

    verifier = Verifier(args.threshold, args.timeout, identity=args.identity, paths=paths)

    if args.simulate:
        outcome = verifier.run(threading.Event())
        log(f"simulated verification: {outcome}")
        return 0 if outcome == "granted" else 1

    return SessionWatcher(verifier).run()


if __name__ == "__main__":
    raise SystemExit(main())

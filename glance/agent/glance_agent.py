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
from .. import protection_state  # noqa: E402
from ..core import MIN_FACE_FRACTION, GlancePipeline  # noqa: E402
from ..paths import RuntimePaths, resolve_paths  # noqa: E402

# --- session notifications -------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi = ctypes.windll.wtsapi32

WM_WTSSESSION_CHANGE = 0x02B1
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2

user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetUserObjectInformationW.restype = wintypes.BOOL
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL


def active_input_desktop_name() -> str | None:
    """Return the active desktop name, or None when Windows cannot tell us."""
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desktop:
        return None
    try:
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW(
            desktop, UOI_NAME, None, 0, ctypes.byref(needed)
        )
        if needed.value <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(ctypes.c_wchar) + 1)
        if not user32.GetUserObjectInformationW(
            desktop, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
        ):
            return None
        return buffer.value
    finally:
        user32.CloseDesktop(desktop)


def is_locked_desktop_name(name: str | None) -> bool:
    """Fail closed: only Windows' explicit Winlogon desktop counts as locked."""
    return bool(name and name.casefold() == "winlogon")


def is_session_locked() -> bool:
    return is_locked_desktop_name(active_input_desktop_name())


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
                 any_identity: bool = False, liveness: str | lv.LivenessMode = "heavy",
                 paths: RuntimePaths | None = None) -> None:
        self.threshold = threshold
        self.timeout = timeout
        self.require_stable = require_stable
        self.identity = identity
        self.any_identity = any_identity
        self.liveness = lv.LivenessMode.from_name(liveness)
        if self.liveness is lv.LivenessMode.LIGHT:
            self.liveness = lv.DEFAULT_MODE  # diagnostics must never authorize grants
        self.paths = paths or resolve_runtime_paths()

    def run(self, stop: threading.Event, is_locked=None) -> str:
        """Returns 'granted', 'denied', 'cancelled', 'no-face' or 'error: ...'.

        is_locked is a callable returning the state the watcher was told about. It is
        deliberately not a desktop probe: OpenInputDesktop was tried and reported
        "unlocked" seconds after a genuine lock, which silently disabled unlocks.
        """
        if not self.any_identity and (not self.identity or not self.identity.strip()):
            return "error: no unlock identity selected"

        pipeline = GlancePipeline(self.paths.identities_path, self.threshold)
        if not pipeline.store.identities:
            return "error: no enrolled identities"

        if not self.any_identity and not any(
            identity.name == self.identity for identity in pipeline.store.identities
        ):
            return "error: selected identity is not enrolled"

        # In specific mode the selected identity is mandatory even when only one
        # person is enrolled. A missing or stale selection must never implicitly
        # authorize everyone; anyone mode is an explicit setting.

        evaluator = lv.LivenessEvaluator(mode=self.liveness)
        cap = pipeline.open_camera(0)
        if not cap.isOpened():
            return "error: camera did not open"

        log(f"verifying (threshold {self.threshold:.2f}, liveness {self.liveness.value}, "
            f"timeout {self.timeout:.0f}s)")
        history: list[float] = []
        openness_history: list[float] = []
        motion_history: list = []
        head_turn_tracker = lv.HeadTurnTracker()
        tracked_identity = ""
        matched_face_seen = False

        def reset_scan() -> None:
            nonlocal tracked_identity
            evaluator.reset()
            history.clear()
            openness_history.clear()
            motion_history.clear()
            head_turn_tracker.reset()
            tracked_identity = ""

        deadline = time.time() + self.timeout
        outcome = "no-face"
        try:
            while not stop.is_set() and time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    outcome = "error: camera read failed"
                    break

                embedding, face, aligned = pipeline.embedding_for(frame)
                if embedding is None or face is None:
                    reset_scan()
                    continue

                height, width = frame.shape[:2]
                fraction = face.area / float(width * height)
                if fraction < MIN_FACE_FRACTION:
                    reset_scan()
                    continue

                match = pipeline.store.best_match(embedding, self.threshold)
                if match is None:
                    reset_scan()
                    continue

                identity, centroid, nearest = match
                if not self.any_identity and identity.name != self.identity:
                    reset_scan()
                    continue
                if identity.name != tracked_identity:
                    reset_scan()
                    tracked_identity = identity.name

                matched_face_seen = True
                history.append(centroid)
                del history[:-5]

                # Deny cues read the native-resolution crop and the full frame.
                x1, y1, x2, y2 = [int(value) for value in face.bbox]
                pad = int(0.15 * max(x2 - x1, y2 - y1))
                crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
                glare = lv.extract_glare(crop)
                device = lv.detect_device_rectangle(frame, tuple(face.bbox))
                screen = lv.extract_screen_glow(frame, tuple(face.bbox))
                texture = lv.extract_texture(crop)

                # Confirm cues read the running scan window.
                landmarks = getattr(face, "kps", None)
                interocular = lv.interocular_distance(landmarks)
                ratios = lv.landmark_ratios(landmarks)
                if ratios is not None:
                    motion_history.append(ratios)
                    del motion_history[:-12]
                if aligned is not None:
                    eye = lv.eye_openness(aligned, interocular)
                    if eye is not None and eye.confidence >= lv.MIN_OPENNESS_CONFIDENCE:
                        openness_history.append(eye.openness)
                        del openness_history[:-12]
                    head_turn_reading = head_turn_tracker.observe(
                        lv.extract_head_pose_sample(landmarks)
                    )
                else:
                    head_turn_tracker.reset()
                    head_turn_reading = lv.CueReading.none()

                snapshot = evaluator.observe(lv.readings(
                    glare, device, openness_history, screen, texture,
                    motion_history, interocular, head_turn_reading,
                ))

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

                    confirmed_by = getattr(snapshot, "confirmed_by", None)
                    log(f"granted {identity.name} "
                        f"(centroid {centroid:.3f}, nearest {nearest:.3f}, "
                        f"liveness {self.liveness.value}/{confirmed_by.title if confirmed_by else '?'}) "
                        f"-> {path}")
                    outcome = "granted"
                    break
        finally:
            cap.release()

        if outcome == "no-face" and matched_face_seen:
            # A matching still photo reaches this point on every frame. Failing
            # closed is the whole point; say so instead of reporting "no face".
            log(f"denied: no proof of life ({self.liveness.value}) within {self.timeout:.0f}s - "
                "no qualifying head-turn sequence detected")
            outcome = "denied"
        return outcome


# --- session watcher -------------------------------------------------------

class SessionWatcher:
    def __init__(self, verifier: Verifier) -> None:
        self.verifier = verifier
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = True
        self.locked = False
        from .idle_lock import IdlePolicy
        self.idle_policy = IdlePolicy()
        self._idle_lock_input = None
        self._idle_attempt = -10.0
        self._last_idle_poll = 0.0
        self._last_power_resume: float | None = None
        config_path = getattr(getattr(verifier, "paths", None), "config_path", None)
        self.protection_state_path = (
            Path(config_path).parent / "protection_state.json"
            if config_path else protection_state.STATE_PATH
        )

    def on_power_resume(self) -> None:
        """Reopen recognition after wake; never leave a stale camera worker running."""
        now = time.monotonic()
        if self._last_power_resume is not None and now - self._last_power_resume < 3.0:
            return
        self._last_power_resume = now
        protection_state.clear_pause_on_wake(self.protection_state_path)
        self._stop_verification()
        if self.locked:
            log("system resumed while locked; restarting face verification")
            self._start_verification()

    def start_after_resume(self, is_locked: bool) -> None:
        """A newly launched wake task scans only if Windows is still on Winlogon."""
        if is_locked:
            self.locked = True
            log("agent started after wake while session is locked")
            self._start_verification()

    def _check_idle(self):
        from .idle_lock import idle_seconds, input_ticks
        now = time.monotonic()
        if now - self._last_idle_poll < 1:
            return
        self._last_idle_poll = now
        ticks = input_ticks()
        if ticks is None:
            return
        current, last_input = ticks
        if self._idle_lock_input is not None:
            if self.locked and last_input != self._idle_lock_input:
                self._idle_lock_input = None
                self._start_verification()
            elif not self.locked and now - self._idle_attempt > 10:
                # No lock event arrived: retry later rather than blocking forever.
                self._idle_lock_input = None
            return
        try:
            config = json.loads(self.verifier.paths.config_path.read_text(encoding="utf-8-sig"))
            seconds = idle_seconds(config) if isinstance(config, dict) else 0
        except (OSError, ValueError):
            return
        if (now - self._idle_attempt >= 10 and
                self.idle_policy.due(current, last_input, seconds, self.locked)):
            self._idle_attempt = now
            # Set the gate before LockWorkStation: its session notification is
            # queued on this same thread and is handled after this method returns.
            self._idle_lock_input = last_input
            if user32.LockWorkStation():
                self.idle_policy.locked(last_input)
                log("idle lock requested; face unlock waits for keyboard or mouse input")
            else:
                self._idle_lock_input = None

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

    def run(self, resume_after_wake: bool = False) -> int:
        watcher = self

        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_POWERBROADCAST and wparam == PBT_APMRESUMEAUTOMATIC:
                watcher.on_power_resume()
                return 1
            if message == WM_WTSSESSION_CHANGE:
                if wparam == WTS_SESSION_LOCK:
                    log("session locked")
                    watcher.locked = True
                    if watcher._idle_lock_input is None:
                        watcher._start_verification()
                elif wparam == WTS_SESSION_UNLOCK:
                    log("session unlocked")
                    watcher.locked = False
                    watcher._idle_lock_input = None
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

        if resume_after_wake:
            watcher.start_after_resume(is_session_locked())

        log("watching for session lock")
        message = MSG()
        try:
            while self.running:
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                self._check_idle()
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


def resolve_any_identity(config: dict, cli_any: bool | None = None) -> bool:
    """Anyone-enrolled is the default unless a config explicitly opts out."""
    if cli_any is not None:
        return bool(cli_any)
    return config.get("any_identity") is not False


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
                        help="which enrolled identity may unlock (ignored in anyone mode)")
    parser.add_argument("--any-identity", dest="any_identity", action="store_true",
                        default=None,
                        help="allow any enrolled face to unlock")
    parser.add_argument("--single-identity", dest="any_identity", action="store_false",
                        default=None,
                        help="restrict unlock to --identity")
    parser.add_argument("--liveness", choices=("light", "heavy", "strict"), default=None,
                        help="anti-spoof level (default: agent_config.json, else heavy)")
    parser.add_argument("--simulate", action="store_true",
                        help="run one verification pass immediately (no lock needed)")
    parser.add_argument("--resume-after-wake", action="store_true",
                        help="resume verification only if Windows is still on the lock screen")
    parser.add_argument("--self-test", action="store_true",
                        help="check key, SID, camera and enrolment, then exit")
    args = parser.parse_args()

    # The desktop app writes this file, so settings changed in the UI reach the agent
    # without rewriting the scheduled task's command line. Explicit flags still win.
    paths = resolve_runtime_paths()
    config_path = paths.config_path
    # Starting the agent at sign-in, through the UI, or from the wake task clears
    # a temporary manual pause from the previous run/session.
    protection_state.clear_pause_on_wake(config_path.parent / "protection_state.json")
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
            config = {"identity": "", "any_identity": False}

    if args.threshold is None:
        args.threshold = float(config.get("threshold", 0.60))
    if args.timeout is None:
        args.timeout = float(config.get("timeout", 90.0))
    if args.liveness is None:
        args.liveness = str(config.get("liveness") or lv.DEFAULT_MODE.value)
    args.liveness = lv.LivenessMode.from_name(args.liveness).value
    args.any_identity = resolve_any_identity(config, args.any_identity)
    args.identity = None if args.any_identity else resolve_identity(config, args.identity)

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
        log(f"scope      : {'any enrolled face' if args.any_identity else args.identity}")
        log(f"liveness   : {args.liveness}")
        cap = pipeline.open_camera(0)
        if cap.isOpened():
            ok, frame = cap.read()
            log(f"camera     : open, frame {frame.shape if ok else 'read failed'}")
        else:
            log("camera     : did not open")
        cap.release()
        return 0

    verifier = Verifier(args.threshold, args.timeout, identity=args.identity,
                        any_identity=args.any_identity, liveness=args.liveness,
                        paths=paths)

    if args.simulate:
        outcome = verifier.run(threading.Event())
        log(f"simulated verification: {outcome}")
        return 0 if outcome == "granted" else 1

    watcher = SessionWatcher(verifier)
    if args.resume_after_wake:
        return watcher.run(resume_after_wake=True)
    return watcher.run()


if __name__ == "__main__":
    raise SystemExit(main())

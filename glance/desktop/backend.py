"""Everything the desktop UI needs from the rest of Glance.

Deliberately thin: the face pipeline stays in `glance/core.py`, the unlock decision stays
in `glance/agent/glance_agent.py`, and the stored credential stays behind `grant_tool.exe`. This
module only reads state, writes settings and starts the things that already exist.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from ..agent.grant import clear_pending_grant
from ..paths import PATHS

DATA = PATHS.data_dir
CONFIG_PATH = PATHS.config_path
IDENTITIES_PATH = PATHS.identities_path
LOG_DIR = PATHS.logs_dir
GRANT_TOOL = PATHS.grant_tool
AGENT_TASK = "Glance Agent"
MUTEX_NAME = "Local\\GlanceAgent"
ACCOUNT_HINT = "MicrosoftAccount\\"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

CREATE_NO_WINDOW = 0x08000000
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
ERROR_CANCELLED = 1223
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102


# --------------------------------------------------------------------------- #
# memoised state
# --------------------------------------------------------------------------- #
# The dashboard polls this state on a timer, and every expensive answer here spawns a
# process: PowerShell to read the scheduled task, grant_tool to read the credential.
# On this hardware that is a visible stutter every few seconds, so answers are cached
# briefly and thrown away the moment the app itself changes something.
_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, max_age: float, producer):
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None and now - entry[0] < max_age:
        return entry[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def invalidate(*keys: str) -> None:
    if not keys:
        _CACHE.clear()
        return
    for key in keys:
        _CACHE.pop(key, None)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    """The agent reads the first three keys; the UI owns the last two."""

    threshold: float = 0.60
    timeout: float = 90.0
    identity: str = ""
    camera: int = 0

    @classmethod
    def load(cls) -> "Settings":
        data: dict = {}
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
        return cls(
            threshold=float(data.get("threshold", 0.60)),
            timeout=float(data.get("timeout", 90.0)),
            identity=str(data.get("identity") or ""),
            camera=int(data.get("camera", 0)),
        )

    def save(self) -> None:
        payload = {
            "threshold": round(float(self.threshold), 2),
            "timeout": float(self.timeout),
            "identity": self.identity,
            "camera": int(self.camera),
        }
        PATHS.ensure_data_dir()
        CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# processes and tasks
# --------------------------------------------------------------------------- #
def _run(argv: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, output.strip()


def _powershell(script: str, timeout: float = 25.0) -> tuple[bool, str]:
    return _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        timeout=timeout,
    )


def agent_running() -> bool:
    """The agent holds Local\\GlanceAgent for its whole life; that is the cheap check."""
    handle = _kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE
    if not handle:
        return False
    _kernel32.CloseHandle(handle)
    return True


def _task_state() -> dict:
    script = (
        "$t = Get-ScheduledTask -TaskName '" + AGENT_TASK + "' -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) { '{\"present\":false}' } else { "
        "ConvertTo-Json @{present=$true; state=[string]$t.State; enabled=[bool]$t.Settings.Enabled} }"
    )
    ok, output = _powershell(script)
    if not ok or not output:
        return {"present": False, "state": "", "enabled": False}
    try:
        return json.loads(output)
    except ValueError:
        return {"present": False, "state": "", "enabled": False}


def task_state(max_age: float = 60.0) -> dict:
    return _cached("task", max_age, _task_state)


def stop_agent() -> tuple[bool, str]:
    ok, output = _run(["schtasks", "/end", "/tn", AGENT_TASK])
    if not ok and ("Access is denied" in output or "0x80070005" in output):
        ok, output = run_elevated(["schtasks", "/end", "/tn", AGENT_TASK])
    if ok:
        invalidate("task")
        return True, "protection paused"
    return False, output or "could not stop the agent"


def start_agent() -> tuple[bool, str]:
    state = task_state(0)
    if not state.get("present"):
        return False, "The Glance Agent task is not installed. Enable Windows unlock first."

    # A user can opt out of sign-in autostart while protection is paused. Task
    # Scheduler refuses to run a disabled task, so enable it only for the run
    # request and restore the user's autostart preference immediately afterward.
    restore_disabled = not state.get("enabled", False)
    if restore_disabled:
        enabled, detail = set_autostart(True)
        if not enabled:
            return False, f"Could not temporarily enable the Glance Agent task: {detail}"

    ok, output = _run(["schtasks", "/run", "/tn", AGENT_TASK])
    if not ok and ("Access is denied" in output or "0x80070005" in output):
        ok, output = run_elevated(["schtasks", "/run", "/tn", AGENT_TASK])

    if restore_disabled:
        restored, restore_detail = set_autostart(False)
        if not restored:
            invalidate("task")
            if ok:
                return False, f"Protection was started, but the sign-in autostart preference could not be restored: {restore_detail}"
            return False, f"{output or 'could not start the agent'}; also could not restore sign-in autostart: {restore_detail}"

    if ok:
        invalidate("task")
        return True, "protection resumed"
    return False, output or "could not start the agent"


def set_autostart(enabled: bool) -> tuple[bool, str]:
    if not task_state(0).get("present"):
        return False, "Enable Windows unlock first; the Glance Agent task is not installed."
    flag = "/enable" if enabled else "/disable"
    ok, output = _run(["schtasks", "/change", "/tn", AGENT_TASK, flag])
    if not ok:
        ok, output = run_elevated(["schtasks", "/change", "/tn", AGENT_TASK, flag])
    if ok:
        invalidate("task")
        return True, "start with Windows " + ("enabled" if enabled else "disabled")
    return False, output


def apply_settings(settings: Settings, restart_running_agent: bool) -> tuple[bool, str]:
    """Persist settings without ever leaving a running agent on a stale owner identity.

    This runs in a worker thread from the Settings page. If stopping the old agent fails,
    do not write the new owner selection. If restarting the new configuration fails, stop
    any partial task and report that settings were saved but protection is paused.
    """
    if restart_running_agent:
        stopped, detail = stop_agent()
        if not stopped:
            return False, f"Current protection could not be stopped; settings were not changed. {detail}"

        deadline = time.monotonic() + 5.0
        while agent_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        if agent_running():
            return False, "Current agent is still running; settings were not changed. Protection was not restarted."

    try:
        settings.save()
    except (OSError, ValueError) as error:
        if restart_running_agent:
            start_agent()
        return False, f"Settings could not be saved: {error}"

    invalidate("task", "identities")
    if not restart_running_agent:
        return True, "Settings saved. Protection is paused."

    started, detail = start_agent()
    if not started:
        stop_agent()
        return False, f"Settings saved, but protection could not restart and is paused. {detail}"

    deadline = time.monotonic() + 8.0
    while not agent_running() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not agent_running():
        stop_agent()
        return False, "Settings saved, but the new agent did not start; protection is paused."

    return True, "Settings saved and protection restarted with the selected identity."


def lock_workstation() -> None:
    _user32.LockWorkStation()


# --------------------------------------------------------------------------- #
# stored credential
# --------------------------------------------------------------------------- #
def _credential_status() -> dict:
    ok, output = _run([str(GRANT_TOOL), "status"])
    if not ok:
        return {"present": False, "account": "", "raw": output}
    account = ""
    for line in output.splitlines():
        if line.lower().startswith("account"):
            account = line.split(":", 1)[1].strip()
    return {"present": "stored credential: present" in output, "account": account, "raw": output}


def credential_status(max_age: float = 30.0) -> dict:
    return _cached("credential", max_age, _credential_status)


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _decode_elevated_output(data: bytes) -> str:
    """Decode the Windows PowerShell 5.1 UTF-16 redirection format and UTF-8 output."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16").strip()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace").strip()
    if b"\x00" in data[:16]:
        return data.decode("utf-16-le", errors="replace").strip("\x00\r\n ")
    return data.decode("utf-8", errors="replace").strip()


def run_elevated(argv: list[str], timeout: float = 120.0) -> tuple[bool, str]:
    """Run a command through UAC and bring its output back.

    ShellExecute cannot hand a child a pipe, so the elevated PowerShell writes the
    command's combined output to a temp file the caller then reads. The password never
    travels in these arguments; it goes through a file that the child deletes.
    """
    stamp = f"{os.getpid()}-{secrets.token_hex(16)}"
    # The parent UI is unelevated and must be able to read and remove the child's
    # result. Use a cryptographically random filename in the user's private temp
    # directory rather than Program Files, which is intentionally not writable.
    result_root = Path(tempfile.gettempdir())
    result_path = result_root / f".glance-elevated-{stamp}.log"
    inner = "$ErrorActionPreference = 'Stop'; & " + " ".join(_ps_quote(part) for part in argv)
    inner += f" *> {_ps_quote(str(result_path))}; exit $LASTEXITCODE"
    parameters = (
        "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass "
        f'-Command "{inner}"'
    )

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    info.lpParameters = parameters
    info.nShow = SW_HIDE
    ctypes.set_last_error(0)
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            return False, "cancelled at the administrator prompt"
        return False, f"could not elevate (error {error})"

    wait_result = _kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
    if wait_result == WAIT_TIMEOUT:
        _kernel32.TerminateProcess(info.hProcess, 1)
        _kernel32.WaitForSingleObject(info.hProcess, 5000)
        _kernel32.CloseHandle(info.hProcess)
        result_path.unlink(missing_ok=True)
        return False, "the elevated helper timed out"
    if wait_result != WAIT_OBJECT_0:
        _kernel32.TerminateProcess(info.hProcess, 1)
        _kernel32.CloseHandle(info.hProcess)
        result_path.unlink(missing_ok=True)
        return False, "could not wait for the elevated helper"

    exit_code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
        _kernel32.CloseHandle(info.hProcess)
        result_path.unlink(missing_ok=True)
        return False, "could not read the elevated helper exit code"
    _kernel32.CloseHandle(info.hProcess)

    try:
        text = _decode_elevated_output(result_path.read_bytes())
    except OSError:
        text = ""
    finally:
        result_path.unlink(missing_ok=True)

    if not text:
        text = "the elevated helper produced no output"
    if exit_code.value != 0:
        if "verify=rejected" in text:
            return False, "the password was rejected by Windows"
        return False, text
    return True, text


_ADMIN_OPERATIONS = frozenset({
    "install-provider", "disable-provider", "reset-glance", "status",
})
_SID_PATTERN = re.compile(r"^S-1-5-21-(?:\d+-){2,}\d+$", re.IGNORECASE)


def run_admin_operation(operation: str, target_sid: str) -> tuple[bool, str]:
    """Run one fixed operation from the protected installed helper, never a user shell."""
    if operation not in _ADMIN_OPERATIONS:
        return False, "unsupported Glance administrator operation"
    if not _SID_PATTERN.fullmatch(target_sid) and not (
        operation == "disable-provider" and target_sid == "S-1-0-0"
    ):
        return False, "a valid Windows account SID is required"
    if not PATHS.packaged:
        return False, "Windows unlock setup is available only from the protected installed app"

    expected_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Glance"
    if Path(os.path.realpath(PATHS.executable_dir)).as_posix().casefold() != Path(
        os.path.realpath(expected_root)
    ).as_posix().casefold():
        return False, "Glance administrator helper is not in the protected Program Files installation"
    if not PATHS.admin_script.is_file():
        return False, f"Glance administrator helper is missing: {PATHS.admin_script}. Repair or reinstall Glance."

    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        return False, "Windows PowerShell could not be found for the in-app administrator operation"
    argv = [
        str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(PATHS.admin_script), "-Operation", operation, "-TargetSid", target_sid,
    ]
    ok, output = run_elevated(argv)
    if not ok:
        return False, output

    for line in output.splitlines():
        if not line.startswith("GLANCE_ADMIN_RESULT="):
            continue
        try:
            record = json.loads(line.partition("=")[2])
        except ValueError:
            return False, "administrator helper returned malformed status data"
        if record.get("operation") != operation:
            return False, "administrator helper did not confirm the requested operation"
        if record.get("status") != "ok":
            return False, str(record.get("message") or "administrator operation failed")
        return True, str(record.get("message") or "operation completed")
    return False, "administrator helper exited without a verified operation result"


def current_user_sid() -> str:
    ok, output = _run([str(GRANT_TOOL), "sid"])
    if not ok:
        return ""
    match = re.search(r"\bsid\s+(S-1-5-21-(?:\d+-){2,}\d+)\b", output, re.IGNORECASE)
    return match.group(1) if match else ""


def enable_windows_unlock() -> tuple[bool, str]:
    settings = Settings.load()
    if not settings.identity or not any(person["name"] == settings.identity for person in identity_summary(0)):
        return False, "Enroll and select a face before enabling Windows unlock."
    if not GRANT_TOOL.is_file():
        return False, "Glance credential helper is missing. Repair or reinstall Glance."
    if not credential_status(0).get("present"):
        return False, "Verify and save your Windows password before enabling Windows unlock."
    if not PATHS.models_dir.is_dir():
        return False, "Glance face models are missing. Repair or reinstall Glance."
    sid = current_user_sid()
    if not sid:
        return False, "Could not determine this Windows account SID."
    ok, message = run_admin_operation("install-provider", sid)
    invalidate("task", "credential")
    return ok, message


def disable_windows_unlock(reset: bool = False) -> tuple[bool, str]:
    sid = current_user_sid()
    if not sid:
        return False, "Could not determine this Windows account SID."
    if agent_running():
        stopped, message = stop_agent()
        if not stopped:
            return False, f"Could not pause the agent; unlock was not changed. {message}"
        deadline = time.monotonic() + 5.0
        while agent_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        if agent_running():
            return False, "The agent is still running; unlock was not changed."
    if not clear_pending_grant():
        return False, "Could not revoke the pending face-unlock grant."
    ok, message = run_admin_operation("reset-glance" if reset else "disable-provider", sid)
    invalidate("task", "credential")
    return ok, message


def store_password(account: str, password: str) -> tuple[bool, str]:
    """Verify through Windows before storage; the password is never in argv."""
    if not GRANT_TOOL.is_file():
        return False, f"Glance credential helper is missing: {GRANT_TOOL}. Repair or reinstall Glance."
    handle, path = tempfile.mkstemp(prefix="glance-pw-", suffix=".txt")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(password.encode("utf-8"))
        argv = [str(GRANT_TOOL), "enroll", "--account", account]
        argv.append("--verify")
        argv += ["--password-file", path]
        ok, message = run_elevated(argv)
        if not ok:
            return False, message
        if "verify=ok" not in message or "stored credential for" not in message:
            return False, "helper exited successfully without confirming password verification and storage"
        invalidate("credential")
        return True, message
    finally:
        Path(path).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# identities and logs
# --------------------------------------------------------------------------- #
def _identity_summary() -> list[dict]:
    try:
        data = json.loads(IDENTITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        {"name": item.get("name", "?"), "samples": len(item.get("samples") or [])}
        for item in data.get("identities", [])
    ]


def identity_summary(max_age: float = 5.0) -> list[dict]:
    return _cached("identities", max_age, _identity_summary)


def delete_identity(name: str) -> tuple[bool, str]:
    settings = Settings.load()
    removing_selected = settings.identity == name

    try:
        data = json.loads(IDENTITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return False, str(error)
    before = len(data.get("identities", []))
    data["identities"] = [i for i in data.get("identities", []) if i.get("name") != name]
    if len(data["identities"]) == before:
        return False, f"no identity named {name}"

    if removing_selected and agent_running():
        stopped, detail = stop_agent()
        if not stopped:
            return False, f"Could not pause protection; {name} was not removed. {detail}"

        deadline = time.monotonic() + 5.0
        while agent_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        if agent_running():
            return False, f"The agent is still running; {name} was not removed."

    if removing_selected and not clear_pending_grant():
        return False, f"Could not revoke the pending face-unlock grant; {name} was not removed."

    try:
        IDENTITIES_PATH.write_text(json.dumps(data), encoding="utf-8")
    except OSError as error:
        return False, str(error)
    if removing_selected:
        settings.identity = ""
        try:
            settings.save()
        except OSError as error:
            # The selected identity is now absent from the store, so the agent will
            # fail closed even if clearing the setting could not be persisted.
            invalidate("identities")
            return False, f"Removed {name}, but could not clear the unlock selection: {error}"
    invalidate("identities")
    if removing_selected:
        invalidate("task")
        return True, f"Removed {name}; face unlock is paused until a new person is selected."
    return True, f"removed {name}"


def log_text(name: str, limit: int = 400) -> str:
    path = LOG_DIR / name
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"({path} does not exist yet)"
    return "\n".join(lines[-limit:])


def _last_unlock() -> str:
    try:
        lines = (LOG_DIR / "agent.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "never"
    for line in reversed(lines):
        if " granted " in line:
            return line.strip()
    return "never"


def last_unlock(max_age: float = 5.0) -> str:
    return _cached("unlock", max_age, _last_unlock)

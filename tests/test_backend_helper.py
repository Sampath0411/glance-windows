from __future__ import annotations

import ctypes
import os
import json
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import glance.desktop.backend as backend


class PasswordHelperTests(unittest.TestCase):
    def _mock_elevation(self, root: Path, wait_result: int, exit_code: int = 0):
        def launch(info_ptr):
            info = ctypes.cast(
                info_ptr, ctypes.POINTER(backend._SHELLEXECUTEINFOW)
            ).contents
            info.hProcess = 123
            return True

        def get_exit_code(_handle, code_ptr):
            ctypes.cast(code_ptr, ctypes.POINTER(backend.wintypes.DWORD)).contents.value = exit_code
            return True

        return (
            patch("glance.desktop.backend.tempfile.gettempdir", return_value=str(root)),
            patch("glance.desktop.backend.secrets.token_hex", return_value="abc123"),
            patch.object(backend._shell32, "ShellExecuteExW", side_effect=launch),
            patch.object(backend._kernel32, "WaitForSingleObject", return_value=wait_result),
            patch.object(backend._kernel32, "GetExitCodeProcess", side_effect=get_exit_code),
        )

    def test_missing_credential_helper_fails_before_uac(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing-grant-tool.exe"
            with (
                patch.object(backend, "GRANT_TOOL", missing),
                patch.object(backend, "run_elevated", return_value=(False, "")) as run_elevated,
            ):
                ok, message = backend.store_password("MicrosoftAccount\\u@example.com", "not-a-real-secret")

        self.assertFalse(ok)
        self.assertIn("helper", message.lower())
        run_elevated.assert_not_called()

    def test_password_is_always_verified_before_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "grant_tool.exe"
            helper.write_bytes(b"test executable placeholder")
            with (
                patch.object(backend, "GRANT_TOOL", helper),
                patch.object(backend, "run_elevated", return_value=(False, "test result")) as run_elevated,
            ):
                backend.store_password("MicrosoftAccount\\u@example.com", "not-a-real-secret")

        argv = run_elevated.call_args.args[0]
        self.assertIn("--verify", argv)

    def test_verify_only_helper_output_is_not_reported_as_stored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "grant_tool.exe"
            helper.write_bytes(b"test executable placeholder")
            with (
                patch.object(backend, "GRANT_TOOL", helper),
                patch.object(backend, "run_elevated", return_value=(True, "verify=ok mode=trusted")),
            ):
                ok, message = backend.store_password("MicrosoftAccount\\u@example.com", "not-a-real-secret")

        self.assertFalse(ok)
        self.assertIn("storage", message.lower())

    def test_unknown_admin_operation_is_rejected_without_uac(self) -> None:
        with patch.object(backend, "run_elevated") as run_elevated:
            ok, message = backend.run_admin_operation("run-arbitrary", "S-1-5-21-1-2-3-1001")
        self.assertFalse(ok)
        self.assertIn("unsupported", message.lower())
        run_elevated.assert_not_called()

    def test_invalid_sid_is_rejected_before_uac(self) -> None:
        with patch.object(backend, "run_elevated") as run_elevated:
            ok, message = backend.run_admin_operation("install-provider", "../../other-user")
        self.assertFalse(ok)
        self.assertIn("sid", message.lower())
        run_elevated.assert_not_called()

    def test_admin_operation_accepts_only_matching_structured_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Program Files" / "Glance"
            helper = root / "provider" / "GlanceAdmin.ps1"
            helper.parent.mkdir(parents=True)
            helper.write_text("# test helper", encoding="utf-8")
            paths = SimpleNamespace(packaged=True, executable_dir=root, admin_script=helper)
            record = json.dumps({"operation": "install-provider", "status": "ok", "message": "installed"})
            with (
                patch.object(backend, "PATHS", paths),
                patch.dict(os.environ, {"ProgramFiles": str(root.parent)}),
                patch.object(backend, "run_elevated", return_value=(True, f"GLANCE_ADMIN_RESULT={record}")) as run_elevated,
            ):
                ok, message = backend.run_admin_operation("install-provider", "S-1-5-21-1-2-3-1001")

        self.assertTrue(ok, message)
        self.assertIn("installed", message)
        argv = run_elevated.call_args.args[0]
        self.assertIn(str(helper), argv)
        self.assertEqual(argv[argv.index("-Operation") + 1], "install-provider")

    def test_admin_operation_does_not_trust_success_for_a_different_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Program Files" / "Glance"
            helper = root / "provider" / "GlanceAdmin.ps1"
            helper.parent.mkdir(parents=True)
            helper.write_text("# test helper", encoding="utf-8")
            paths = SimpleNamespace(packaged=True, executable_dir=root, admin_script=helper)
            record = json.dumps({"operation": "status", "status": "ok", "message": "enabled"})
            with (
                patch.object(backend, "PATHS", paths),
                patch.dict(os.environ, {"ProgramFiles": str(root.parent)}),
                patch.object(backend, "run_elevated", return_value=(True, f"GLANCE_ADMIN_RESULT={record}")),
            ):
                ok, message = backend.run_admin_operation("install-provider", "S-1-5-21-1-2-3-1001")
        self.assertFalse(ok)
        self.assertIn("did not confirm", message.lower())

    def test_current_sid_is_read_from_bundled_helper(self) -> None:
        with patch.object(backend, "_run", return_value=(True, "session 4 sid S-1-5-21-1-2-3-1001")) as run:
            sid = backend.current_user_sid()
        self.assertEqual(sid, "S-1-5-21-1-2-3-1001")
        run.assert_called_once_with([str(backend.GRANT_TOOL), "sid"])

    def test_enable_unlock_requires_an_enrolled_face_and_stored_password(self) -> None:
        with (
            patch.object(backend, "current_user_sid", return_value="S-1-5-21-1-2-3-1001"),
            patch.object(backend.Settings, "load", return_value=backend.Settings(any_identity=True)),
            patch.object(backend, "identity_summary", return_value=[]),
            patch.object(backend, "credential_status", return_value={"present": True}),
            patch.object(backend, "run_admin_operation") as admin,
        ):
            ok, message = backend.enable_windows_unlock()
        self.assertFalse(ok)
        self.assertIn("enroll", message.lower())
        admin.assert_not_called()

    def test_enable_unlock_accepts_anyone_mode_with_an_enrolled_face(self) -> None:
        with (
            patch.object(backend, "current_user_sid", return_value="S-1-5-21-1-2-3-1001"),
            patch.object(backend.Settings, "load",
                         return_value=backend.Settings(any_identity=True, identity="")),
            patch.object(backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 18}]),
            patch.object(backend, "credential_status", return_value={"present": True}),
            patch.object(backend, "models_present", return_value=True),
            patch.object(backend, "run_admin_operation", return_value=(True, "ok")) as admin,
        ):
            ok, message = backend.enable_windows_unlock()
        self.assertTrue(ok, message)
        admin.assert_called_once_with("install-provider", "S-1-5-21-1-2-3-1001")

    def test_enable_unlock_rejects_a_stale_specific_identity(self) -> None:
        with (
            patch.object(backend, "current_user_sid", return_value="S-1-5-21-1-2-3-1001"),
            patch.object(backend.Settings, "load",
                         return_value=backend.Settings(any_identity=False, identity="Gone")),
            patch.object(backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 18}]),
            patch.object(backend, "credential_status", return_value={"present": True}),
            patch.object(backend, "run_admin_operation") as admin,
        ):
            ok, message = backend.enable_windows_unlock()
        self.assertFalse(ok)
        self.assertIn("select", message.lower())
        admin.assert_not_called()

    def test_settings_round_trip_preserves_the_unlock_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            with patch.object(backend, "CONFIG_PATH", path):
                backend.Settings(identity="Sampath", any_identity=False).save()
                specific = backend.Settings.load()
                backend.Settings(any_identity=True).save()
                anyone = backend.Settings.load()
        self.assertFalse(specific.any_identity)
        self.assertEqual(specific.identity, "Sampath")
        self.assertTrue(anyone.any_identity)
        self.assertEqual(anyone.identity, "")

    def test_settings_default_to_anyone_when_the_scope_key_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"identity": "Sampath"}), encoding="utf-8")
            with patch.object(backend, "CONFIG_PATH", path):
                loaded = backend.Settings.load()
        self.assertTrue(loaded.any_identity)
        self.assertEqual(loaded.identity, "Sampath")

    def test_autostart_cannot_be_changed_before_agent_task_exists(self) -> None:
        with (
            patch.object(backend, "task_state", return_value={"present": False}),
            patch.object(backend, "_run") as run,
            patch.object(backend, "run_elevated") as elevate,
        ):
            ok, message = backend.set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("enable windows unlock first", message.lower())
        run.assert_not_called()
        elevate.assert_not_called()

    def test_resume_temporarily_enables_disabled_task_and_restores_autostart(self) -> None:
        with (
            patch.object(backend, "task_state", return_value={"present": True, "enabled": False}),
            patch.object(backend, "set_autostart", side_effect=[(True, "enabled"), (True, "disabled")]) as autostart,
            patch.object(backend, "_run", return_value=(True, "started")) as run,
            patch.object(backend, "invalidate") as invalidate,
        ):
            ok, message = backend.start_agent()

        self.assertTrue(ok)
        self.assertEqual(message, "protection resumed")
        self.assertEqual(autostart.call_args_list, [unittest.mock.call(True), unittest.mock.call(False)])
        run.assert_called_once_with(["schtasks", "/run", "/tn", backend.AGENT_TASK])
        invalidate.assert_called_once_with("task")

    def test_elevated_task_command_uses_process_exit_code_not_credential_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = root / f".glance-elevated-{os.getpid()}-abc123.log"
            result.write_text("task completed", encoding="utf-8")
            patches = self._mock_elevation(root, backend.WAIT_OBJECT_0, 0)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                ok, message = backend.run_elevated(["schtasks", "/end", "/tn", "Glance Agent"])

        self.assertTrue(ok)
        self.assertEqual(message, "task completed")

    def test_elevated_output_decodes_windows_powershell_utf16_redirection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = root / f".glance-elevated-{os.getpid()}-abc123.log"
            result.write_bytes("verify=ok mode=trusted\nstored credential for account".encode("utf-16"))
            patches = self._mock_elevation(root, backend.WAIT_OBJECT_0, 0)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                ok, message = backend.run_elevated(["grant_tool", "enroll"])

        self.assertTrue(ok)
        self.assertIn("verify=ok", message)
        self.assertIn("stored credential for account", message)

    def test_packaged_elevated_result_uses_user_temp_not_protected_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_root = root / "Program Files" / "Glance"
            install_root.mkdir(parents=True)
            result = root / f".glance-elevated-{os.getpid()}-abc123.log"
            result.write_text("task completed", encoding="utf-8")
            patches = self._mock_elevation(root, backend.WAIT_OBJECT_0, 0)
            with (
                patch.object(backend, "PATHS", SimpleNamespace(packaged=True, executable_dir=install_root)),
                patches[0], patches[1], patches[2], patches[3], patches[4],
            ):
                ok, message = backend.run_elevated(["schtasks", "/change", "/enable"])

        self.assertTrue(ok)
        self.assertEqual(message, "task completed")
        self.assertFalse(result.exists(), "the unelevated app must be able to clean its temp result")

    def test_elevated_helper_timeout_is_reported_and_terminates_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patches = self._mock_elevation(root, backend.WAIT_TIMEOUT)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patch.object(backend._kernel32, "TerminateProcess", return_value=True) as terminate:
                ok, message = backend.run_elevated(["grant_tool", "enroll"], timeout=0.01)

        self.assertFalse(ok)
        self.assertIn("timed out", message.lower())
        terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()

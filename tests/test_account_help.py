import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from glance.desktop.account_info import AccountInfo
from glance.desktop.pages import AboutPage, PasswordCheckDialog, WindowsPasswordFallbackDialog


class AccountHelpUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    @staticmethod
    def wait_for(predicate, timeout_ms=2000):
        for _ in range(timeout_ms // 20):
            if predicate():
                return True
            QTest.qWait(20)
        return bool(predicate())

    def test_about_page_offers_account_check_and_signin_options(self):
        with patch("glance.desktop.pages.backend.models_ready", return_value=True):
            page = AboutPage(SimpleNamespace())
        self.assertEqual(page.identify_account_button.text(), "Check sign-in type")
        self.assertEqual(page.signin_options_button.text(), "Open Sign-in options")
        self.assertTrue(page.check_password_button.isEnabled() is False)

    def test_signin_options_button_opens_windows_settings(self):
        with (
            patch("glance.desktop.pages.backend.models_ready", return_value=True),
            patch("glance.desktop.pages.QDesktopServices.openUrl") as open_url,
        ):
            page = AboutPage(SimpleNamespace())
            page.signin_options_button.click()
        self.assertEqual(open_url.call_args.args[0].toString(), "ms-settings:signinoptions")

    def test_password_check_dialog_masks_password(self):
        dialog = PasswordCheckDialog(None, r"MicrosoftAccount\alex@example.com")
        self.assertEqual(dialog.account.text(), r"MicrosoftAccount\alex@example.com")
        self.assertEqual(dialog.password.echoMode(), QLineEdit.Password)

    def test_fallback_checks_current_windows_password_without_saving_it(self):
        with (
            patch("glance.desktop.pages.backend.identify_windows_account",
                  return_value=AccountInfo("Local account", r"LAPTOP\alex", "")),
            patch("glance.desktop.pages.backend.check_account_password",
                  return_value=(True, "Windows verified the password.")) as verify,
            patch("glance.desktop.pages.backend.store_password") as store,
        ):
            dialog = WindowsPasswordFallbackDialog(None)
            self.assertTrue(self.wait_for(dialog.check_button.isEnabled))
            self.assertEqual(dialog.account.text(), r"LAPTOP\alex")
            self.assertEqual(dialog.password.echoMode(), QLineEdit.Password)
            dialog.password.setText("secret-value")
            dialog.check_button.click()
            self.assertTrue(self.wait_for(lambda: dialog.result() == QDialog.Accepted))
            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertEqual(dialog.password.text(), "")
            self.assertIsNone(dialog._password_secret)
            verify.assert_called_once_with(r"LAPTOP\alex", "secret-value")
            store.assert_not_called()

    def test_wrong_fallback_password_is_cleared_and_can_be_retried(self):
        with (
            patch("glance.desktop.pages.backend.identify_windows_account",
                  return_value=AccountInfo("Local account", r"LAPTOP\alex", "")),
            patch("glance.desktop.pages.backend.check_account_password",
                  return_value=(False, "Windows did not verify this password.")),
        ):
            dialog = WindowsPasswordFallbackDialog(None)
            self.assertTrue(self.wait_for(dialog.check_button.isEnabled))
            dialog.password.setText("wrong-secret")
            dialog.check_button.click()
            self.assertTrue(self.wait_for(lambda: dialog.check_button.isEnabled()))
            self.assertEqual(dialog.result(), QDialog.Rejected)
            self.assertEqual(dialog.password.text(), "")
            self.assertIsNone(dialog._password_secret)
            self.assertTrue(dialog.check_button.isEnabled())
            self.assertIn("not verify", dialog.status.text().lower())

    def test_fallback_dialog_cannot_close_while_password_verification_runs(self):
        verification_started = threading.Event()
        finish_verification = threading.Event()

        def blocked_check(_account, _password):
            verification_started.set()
            finish_verification.wait(3)
            return True, "Windows verified the password."

        with (
            patch("glance.desktop.pages.backend.identify_windows_account",
                  return_value=AccountInfo("Local account", r"LAPTOP\alex", "")),
            patch("glance.desktop.pages.backend.check_account_password",
                  side_effect=blocked_check),
        ):
            dialog = WindowsPasswordFallbackDialog(None)
            self.assertTrue(self.wait_for(dialog.check_button.isEnabled))
            dialog.show()
            dialog.password.setText("secret-value")
            dialog.check_button.click()
            self.assertTrue(verification_started.wait(1))
            dialog.close()
            dialog.reject()
            self.assertFalse(dialog.isHidden())
            self.assertTrue(dialog._verification_running())
            finish_verification.set()
            self.assertTrue(self.wait_for(lambda: dialog.result() == QDialog.Accepted))

    def test_fallback_waits_for_worker_exit_before_accepting_result(self):
        with patch(
            "glance.desktop.pages.backend.identify_windows_account",
            return_value=AccountInfo("Local account", r"LAPTOP\alex", ""),
        ):
            dialog = WindowsPasswordFallbackDialog(None)
            self.assertTrue(self.wait_for(dialog.check_button.isEnabled))

            class FinishingTask:
                def isRunning(self):
                    return True

            dialog._password_task = FinishingTask()
            dialog._password_result_received(True, "Windows verified the password.")
            self.assertNotEqual(dialog.result(), QDialog.Accepted)
            self.assertTrue(dialog._verification_running())
            dialog._password_task_finished()
            self.assertEqual(dialog.result(), QDialog.Accepted)


if __name__ == "__main__":
    unittest.main()

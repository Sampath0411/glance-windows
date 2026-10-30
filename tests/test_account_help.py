import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QLineEdit

from glance.desktop.pages import AboutPage, PasswordCheckDialog


class AccountHelpUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

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


if __name__ == "__main__":
    unittest.main()

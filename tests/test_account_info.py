import unittest
from unittest.mock import patch

from glance.desktop import account_info


class AccountClassificationTests(unittest.TestCase):
    def test_machine_account_without_upn_is_local(self):
        result = account_info.classify_account(
            r"LAPTOP-123\alex", "", computer_name="LAPTOP-123"
        )
        self.assertEqual(result.account_type, "Local account")
        self.assertEqual(result.account, r"LAPTOP-123\alex")

    def test_microsoft_account_domain_is_classified_as_microsoft(self):
        result = account_info.classify_account(
            r"MicrosoftAccount\alex@example.com", "alex@example.com",
            computer_name="LAPTOP-123",
        )
        self.assertEqual(result.account_type, "Microsoft account")
        self.assertEqual(result.account, r"MicrosoftAccount\alex@example.com")

    def test_non_local_domain_with_upn_is_work_or_school(self):
        result = account_info.classify_account(
            r"CONTOSO\alex", "alex@contoso.com", computer_name="LAPTOP-123"
        )
        self.assertEqual(result.account_type, "Work or school account")
        self.assertEqual(result.upn, "alex@contoso.com")

    def test_ambiguous_identity_is_not_guessed(self):
        result = account_info.classify_account(
            r"CONTOSO\alex", "", computer_name="LAPTOP-123"
        )
        self.assertEqual(result.account_type, "Could not determine")

    def test_detect_account_runs_both_windows_checks_without_console(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            output = r"LAPTOP-123\alex" if len(calls) == 1 else ""
            return type("Completed", (), {"returncode": 0, "stdout": output, "stderr": ""})()

        with patch.object(account_info.subprocess, "run", side_effect=run):
            result = account_info.detect_account(computer_name="LAPTOP-123")

        self.assertEqual(result.account_type, "Local account")
        self.assertEqual(calls[0][0][-1], "whoami")
        self.assertEqual(calls[1][0][-2:], ["whoami", "/upn"])
        self.assertTrue(all(call[1]["creationflags"] == account_info.CREATE_NO_WINDOW for call in calls))


if __name__ == "__main__":
    unittest.main()

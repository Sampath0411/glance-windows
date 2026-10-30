import unittest

from glance.desktop.recovery import should_recover_agent


class AgentRecoveryPolicyTests(unittest.TestCase):
    def test_recovers_unexpected_agent_exit_when_previously_active(self):
        self.assertTrue(should_recover_agent(True, True, False, False))

    def test_does_not_start_agent_at_signin_when_startup_was_disabled(self):
        self.assertFalse(should_recover_agent(False, True, False, False))

    def test_does_not_override_manual_pause_before_wake(self):
        self.assertFalse(should_recover_agent(True, True, False, True))

    def test_does_not_start_when_unlock_is_not_installed(self):
        self.assertFalse(should_recover_agent(True, False, False, False))

    def test_does_not_start_a_second_agent(self):
        self.assertFalse(should_recover_agent(True, True, True, False))


if __name__ == "__main__":
    unittest.main()

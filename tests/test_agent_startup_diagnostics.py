from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from glance.agent.startup_diagnostics import run_agent


class AgentStartupDiagnosticsTests(unittest.TestCase):
    def test_unhandled_startup_exception_is_recorded_with_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "agent-startup.log"

            def fail_startup() -> int:
                raise RuntimeError("diagnostic failure")

            result = run_agent(fail_startup, log_path=log_path)

            self.assertEqual(result, 1)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("Agent startup crashed", log)
            self.assertIn("RuntimeError: diagnostic failure", log)

    def test_nonzero_agent_exit_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "agent-startup.log"

            result = run_agent(lambda: 1, log_path=log_path)

            self.assertEqual(result, 1)
            self.assertIn("Agent exited with code 1", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

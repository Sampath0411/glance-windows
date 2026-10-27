from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent.glance_agent as glance_agent
from desktop.paths import resolve_paths


class AgentPathTests(unittest.TestCase):
    def test_agent_imports_without_a_top_level_grant_module(self) -> None:
        # Standalone builds contain agent.grant, not a loose grant.py on sys.path.
        script = '''
import sys
import importlib.abc
class NoLooseGrant(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "grant":
            raise ModuleNotFoundError("No module named 'grant'")
sys.meta_path.insert(0, NoLooseGrant())
import agent.glance_agent as ga
import agent.grant
assert ga.grant_mod is agent.grant
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_agent_uses_same_identity_and_config_paths_as_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent_paths = glance_agent.resolve_runtime_paths(
                packaged=False, project_root=root
            )
            desktop_paths = resolve_paths(packaged=False, project_root=root)

        self.assertEqual(agent_paths.identities_path, desktop_paths.identities_path)
        self.assertEqual(agent_paths.config_path, desktop_paths.config_path)
        self.assertEqual(agent_paths.models_dir, desktop_paths.models_dir)

    def test_packaged_agent_ignores_stale_source_enrollment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            install = Path(temp) / "Program Files" / "Glance"
            appdata = Path(temp) / "AppData" / "Local"
            stale_identity_file = root / "data" / "identities.json"
            stale_identity_file.parent.mkdir(parents=True)
            stale_identity_file.write_text('{"identities":[{"name":"Old"}]}', encoding="utf-8")

            agent_paths = glance_agent.resolve_runtime_paths(
                packaged=True,
                project_root=root,
                executable_dir=install,
                local_app_data=appdata,
            )

        self.assertEqual(
            agent_paths.identities_path,
            appdata / "Glance" / "data" / "identities.json",
        )
        self.assertNotEqual(agent_paths.identities_path, stale_identity_file)

    def test_packaged_agent_reads_appdata_config_not_stale_source_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            install = Path(temp) / "Program Files" / "Glance"
            appdata = Path(temp) / "AppData" / "Local"
            (root / "data").mkdir(parents=True)
            (root / "data" / "agent_config.json").write_text(
                '{"identity":"Old"}', encoding="utf-8"
            )
            paths = resolve_paths(
                packaged=True,
                project_root=root,
                executable_dir=install,
                local_app_data=appdata,
            )
            paths.data_dir.mkdir(parents=True)
            paths.config_path.write_text('{"identity":"New"}', encoding="utf-8")
            seen = []

            class Watcher:
                def __init__(self, verifier):
                    seen.append(verifier.identity)

                def run(self):
                    return 0

            with (
                patch.object(glance_agent, "resolve_runtime_paths", return_value=paths),
                patch.object(glance_agent, "claim_single_instance", return_value=True),
                patch.object(glance_agent, "SessionWatcher", Watcher),
                patch.object(glance_agent.sys, "argv", ["glance_agent.py"]),
            ):
                self.assertEqual(glance_agent.main(), 0)

        self.assertEqual(seen, ["New"])


if __name__ == "__main__":
    unittest.main()

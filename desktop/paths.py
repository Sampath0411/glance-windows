"""Resolve immutable app resources and mutable per-user data locations."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    packaged: bool
    project_root: Path
    executable_dir: Path
    data_dir: Path
    models_dir: Path
    logs_dir: Path
    grant_tool: Path
    agent_executable: Path
    provider_dll: Path
    admin_script: Path

    @property
    def config_path(self) -> Path:
        return self.data_dir / "agent_config.json"

    @property
    def identities_path(self) -> Path:
        return self.data_dir / "identities.json"

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def _is_packaged() -> bool:
    return bool(
        os.environ.get("GLANCE_PACKAGED") == "1"
        or getattr(sys, "frozen", False)
        or "__compiled__" in globals()
    )


def resolve_paths(
    *,
    packaged: bool | None = None,
    project_root: Path | None = None,
    executable_dir: Path | None = None,
    local_app_data: Path | None = None,
) -> RuntimePaths:
    """Resolve app paths; keyword overrides make packaged behavior testable."""
    project_root = Path(project_root or Path(__file__).resolve().parent.parent)
    packaged = _is_packaged() if packaged is None else packaged
    executable_dir = Path(executable_dir or Path(sys.executable).resolve().parent)

    if packaged:
        local_app_data = Path(
            local_app_data
            or os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        data_dir = local_app_data / "Glance" / "data"
        resource_root = executable_dir
        agent_executable = resource_root / "GlanceAgent.exe"
        grant_tool = resource_root / "provider" / "grant_tool.exe"
        provider_dll = resource_root / "provider" / "GlanceProvider.dll"
        admin_script = resource_root / "provider" / "GlanceAdmin.ps1"
    else:
        data_dir = project_root / "data"
        resource_root = project_root
        agent_executable = project_root / ".venv" / "Scripts" / "pythonw.exe"
        grant_tool = project_root / "provider" / "build" / "grant_tool.exe"
        provider_dll = project_root / "provider" / "build" / "GlanceProvider.dll"
        admin_script = project_root / "provider" / "GlanceAdmin.ps1"

    return RuntimePaths(
        packaged=bool(packaged),
        project_root=project_root,
        executable_dir=executable_dir,
        data_dir=data_dir,
        models_dir=resource_root / "models" / "buffalo_s",
        logs_dir=Path(r"C:\ProgramData\Glance\logs"),
        grant_tool=grant_tool,
        agent_executable=agent_executable,
        provider_dll=provider_dll,
        admin_script=admin_script,
    )


PATHS = resolve_paths()

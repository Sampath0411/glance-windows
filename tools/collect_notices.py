"""Collect license texts shipped by pinned Python distributions."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path
from typing import Iterable


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def collect_notices(output_dir: Path, package_names: Iterable[str]) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[str] = [
        "# Collected third-party package notices",
        "",
        "Generated from the exact installed build environment. Review all included",
        "native libraries and Qt licensing obligations before public distribution.",
        "",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        python_target = output_dir / "CPython-LICENSE.txt"
        shutil.copyfile(python_license, python_target)
        entries.extend((f"## CPython {sys.version_info.major}.{sys.version_info.minor}", "", "License: PSF License", "CPython-LICENSE.txt", ""))

    for requested_name in package_names:
        distribution = metadata.distribution(requested_name)
        name = distribution.metadata.get("Name") or requested_name
        version = distribution.version
        expression = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "License metadata not declared"
        )
        entries.extend((f"## {name} {version}", "", f"Declared license: {expression}", ""))
        package_output = output_dir / f"{_safe_name(name)}-{_safe_name(version)}"
        copied = 0
        site_root = Path(distribution.locate_file("")).resolve()
        for relative in distribution.files or ():
            rel_path = Path(str(relative))
            lowered = rel_path.name.lower()
            if not any(marker in lowered for marker in ("license", "copying", "notice")):
                continue
            if rel_path.suffix.lower() not in (".txt", ".md", ".rst", ""):
                continue
            source = Path(distribution.locate_file(relative)).resolve()
            if not source.is_file() or not source.is_relative_to(site_root):
                continue
            if source.stat().st_size > 10 * 1024 * 1024:
                continue
            target = package_output / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied += 1
        entries.append(f"License/notice files copied: {copied}")
        entries.append("")

    index = output_dir / "INDEX.md"
    index.write_text("\n".join(entries), encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("packages", nargs="+", help="distribution names from requirements-lock.txt")
    args = parser.parse_args()
    index = collect_notices(args.output, args.packages)
    print(f"Collected package notices: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

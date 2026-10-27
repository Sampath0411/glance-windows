"""
CLI parsing tests.

Regression guard: `--liveness` used to be a top-level-only option, so
`app.py live --liveness light` - exactly what the launcher runs - died with
"unrecognized arguments" and menu options 2 and 3 did nothing.
"""

from __future__ import annotations

import argparse

from app import build_parser

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main() -> int:
    p = build_parser

    a = parse(["live", "--liveness", "light"])
    check("live --liveness light (menu option 2)", a.liveness == "light" and a.cmd == "live",
          f"{a.cmd}/{a.liveness}")

    a = parse(["live", "--liveness", "heavy"])
    check("live --liveness heavy (menu option 3)", a.liveness == "heavy",
          a.liveness)

    a = parse(["live", "--liveness", "off"])
    check("live --liveness off", a.liveness == "off", a.liveness)

    a = parse(["--liveness", "heavy", "live"])
    check("options before the subcommand still work", a.liveness == "heavy", a.liveness)

    a = parse(["live"])
    check("live alone keeps the light default", a.liveness == "light", a.liveness)

    a = parse(["live", "--threshold", "0.7", "--camera", "2"])
    check("threshold and camera accepted after the subcommand",
          a.threshold == 0.7 and a.camera == 2, f"{a.threshold}/{a.camera}")

    a = parse(["bench", "--frames", "5", "--liveness", "off"])
    check("bench accepts scan options", a.liveness == "off" and a.frames == 5, a.liveness)

    a = parse(["enroll", "--name", "sampath"])
    check("enroll still requires and takes --name", a.name == "sampath", a.name)

    try:
        parse(["live", "--liveness", "sometimes"])
        check("invalid liveness value is rejected", False, "accepted it")
    except SystemExit:
        check("invalid liveness value is rejected", True, "SystemExit")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

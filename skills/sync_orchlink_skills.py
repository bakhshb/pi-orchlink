#!/usr/bin/env python3
"""Sync shared Orchlink skill references from the general skill to adapters.

The general skill is the source of truth for shared reference content. Adapter
references are rendered from it with only platform naming differences.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GENERAL_REFERENCES = ROOT / "general" / "orchlink" / "references"
ADAPTERS = {
    "openclaw": ROOT / "openclaw" / "orchlink" / "references",
    "hermes": ROOT / "hermes" / "orchlink" / "references",
}


def render_reference(text: str, adapter: str) -> str:
    name = "OpenClaw" if adapter == "openclaw" else "Hermes"
    rendered = text.replace("lead-agent coordination details", f"{name}-as-lead coordination details")
    rendered = rendered.replace("You usually do not need this", f"{name} usually does not need this")
    rendered = rendered.replace("You usually need this", f"{name} usually needs this")
    rendered = rendered.replace("For an external lead agent", f"For {name}-as-lead")
    if adapter == "hermes":
        rendered = rendered.replace("human approval and shell access", "human approval and terminal access")
    return rendered


def expected_files(adapter: str) -> dict[str, str]:
    return {
        item.name: render_reference(item.read_text(encoding="utf-8"), adapter)
        for item in sorted(GENERAL_REFERENCES.glob("*.md"))
    }


def reference_files(path: Path) -> dict[str, str]:
    return {item.name: item.read_text(encoding="utf-8") for item in sorted(path.glob("*.md"))}


def check() -> list[str]:
    errors: list[str] = []
    for adapter, target in ADAPTERS.items():
        expected = expected_files(adapter)
        actual = reference_files(target)
        if actual.keys() != expected.keys():
            errors.append(f"{target} reference file set differs from {GENERAL_REFERENCES}")
            continue
        for name, text in expected.items():
            if actual[name] != text:
                errors.append(f"{target / name} differs from rendered {adapter} reference")
    return errors


def sync() -> None:
    for adapter, target in ADAPTERS.items():
        target.mkdir(parents=True, exist_ok=True)
        expected = expected_files(adapter)
        for stale in target.glob("*.md"):
            if stale.name not in expected:
                stale.unlink()
        for name, text in expected.items():
            (target / name).write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only verify adapters match rendered general references")
    args = parser.parse_args()

    if args.check:
        errors = check()
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0

    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Sync shared Orchlink skill content from the general skill to adapters.

The general skill is the source of truth for shared SKILL.md body text and
reference content. Adapter files are rendered from it with only platform naming
and metadata differences.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GENERAL_SKILL = ROOT / "general" / "orchlink" / "SKILL.md"
GENERAL_REFERENCES = ROOT / "general" / "orchlink" / "references"
ADAPTER_ROOTS = {
    "openclaw": ROOT / "openclaw" / "orchlink",
    "hermes": ROOT / "hermes" / "orchlink",
}


def adapter_name(adapter: str) -> str:
    return "OpenClaw" if adapter == "openclaw" else "Hermes"


def render_reference(text: str, adapter: str) -> str:
    name = adapter_name(adapter)
    rendered = text.replace("lead-agent coordination details", f"{name}-as-lead coordination details")
    rendered = rendered.replace("You usually do not need this", f"{name} usually does not need this")
    rendered = rendered.replace("For an external lead agent", f"For {name}-as-lead")
    rendered = rendered.replace("lead-owned work", f"{name}-owned work")
    if adapter == "openclaw":
        rendered = rendered.replace("native subagents for named Pi workers", "OpenClaw subagents for named Pi workers")
    elif adapter == "hermes":
        rendered = rendered.replace("native subagents for named Pi workers", "Hermes-native subagents for named Pi workers")
        rendered = rendered.replace("human approval and shell access", "human approval and terminal access")
    return rendered


def render_skill(text: str, adapter: str) -> str:
    name = adapter_name(adapter)
    rendered = text.replace("Use when this agent is", f"Use when {name} is")
    rendered = rendered.replace("# Orchlink Lead\n", f"# Orchlink Lead for {name}\n")
    rendered = rendered.replace("You are the lead coding agent.", f"{name} is the lead agent.")
    rendered = rendered.replace("If you have no terminal access", f"If {name} has no terminal access")
    rendered = rendered.replace("Keep lead-owned work", f"Keep {name}-owned work")
    if adapter == "openclaw":
        rendered = rendered.replace(
            "metadata:\n  tags: [coding, local-coordination, cli]\n  category: coding\n  requires_tools: [shell]",
            "metadata:\n  openclaw:\n    tags: [coding, local-coordination, cli]\n    category: coding\n    requires_tools: [shell]",
        )
        rendered = rendered.replace(
            "Do not substitute native subagents or other agent-platform delegation for named Pi workers.",
            "Do not substitute OpenClaw subagents, `sessions_spawn`, or other OpenClaw delegation for named Pi workers.",
        )
        rendered = rendered.replace("platform-native background sessions", "OpenClaw-native background sessions")
        rendered = rendered.replace("Do not silently use native subagents as a substitute.", "Do not silently use OpenClaw subagents as a substitute.")
    elif adapter == "hermes":
        rendered = rendered.replace(
            "metadata:\n  tags: [coding, local-coordination, cli]\n  category: coding\n  requires_tools: [shell]",
            "metadata:\n  hermes:\n    tags: [coding, local-coordination, cli]\n    category: coding\n    requires_toolsets: [terminal]",
        )
        rendered = rendered.replace(
            "Do not substitute native subagents or other agent-platform delegation for named Pi workers.",
            "Do not substitute Hermes-native subagents or other Hermes delegation for named Pi workers.",
        )
        rendered = rendered.replace("platform-native background sessions", "Hermes-native background sessions")
        rendered = rendered.replace(
            "Do not silently use native subagents as a substitute.",
            "Do not silently use Hermes-native subagents as a substitute.\n\nDo not start `orch lead` by default. Hermes can act as lead through CLI commands. Start `orch lead --new` only when the human wants a visible Pi lead chat to receive worker replies or Talk messages.",
        )
    return rendered


def expected_references(adapter: str) -> dict[str, str]:
    return {
        item.name: render_reference(item.read_text(encoding="utf-8"), adapter)
        for item in sorted(GENERAL_REFERENCES.glob("*.md"))
    }


def reference_files(path: Path) -> dict[str, str]:
    return {item.name: item.read_text(encoding="utf-8") for item in sorted(path.glob("*.md"))}


def check() -> list[str]:
    errors: list[str] = []
    general_skill_text = GENERAL_SKILL.read_text(encoding="utf-8")
    for adapter, root in ADAPTER_ROOTS.items():
        skill_path = root / "SKILL.md"
        expected_skill = render_skill(general_skill_text, adapter)
        if not skill_path.is_file() or skill_path.read_text(encoding="utf-8") != expected_skill:
            errors.append(f"{skill_path} differs from rendered {adapter} skill")

        target = root / "references"
        expected = expected_references(adapter)
        actual = reference_files(target)
        if actual.keys() != expected.keys():
            errors.append(f"{target} reference file set differs from {GENERAL_REFERENCES}")
            continue
        for name, text in expected.items():
            if actual[name] != text:
                errors.append(f"{target / name} differs from rendered {adapter} reference")
    return errors


def sync() -> None:
    general_skill_text = GENERAL_SKILL.read_text(encoding="utf-8")
    for adapter, root in ADAPTER_ROOTS.items():
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(render_skill(general_skill_text, adapter), encoding="utf-8")

        target = root / "references"
        target.mkdir(parents=True, exist_ok=True)
        expected = expected_references(adapter)
        for stale in target.glob("*.md"):
            if stale.name not in expected:
                stale.unlink()
        for name, text in expected.items():
            (target / name).write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only verify adapters match rendered general skill content")
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

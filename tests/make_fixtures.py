#!/usr/bin/env python3
"""Builds two test setups for the scanner.

  empty/  a bare code project and a home with no AI setup at all
  messy/  a home and project with a bloated, repetitive rules file, a fake
          token, 60 fake skills and loose permissions

Usage: python3 make_fixtures.py OUTPUT_DIR
The fake token is assembled at run time so this file never contains one.
"""

import json
import sys
from pathlib import Path


def build(root: Path) -> None:
    empty = root / "empty"
    (empty / "home").mkdir(parents=True, exist_ok=True)
    (empty / "project" / "src").mkdir(parents=True, exist_ok=True)
    # A bare code project: some code, nothing else.
    (empty / "project" / "package.json").write_text('{"name": "demo-app", "version": "1.0.0"}\n')
    (empty / "project" / "src" / "index.js").write_text("console.log('hello');\n")

    messy = root / "messy"
    home = messy / "home"
    proj = messy / "project"
    (home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "package.json").write_text('{"name": "messy-app", "version": "1.0.0"}\n')
    (proj / "src" / "index.js").write_text("console.log('hello');\n")

    filler = (
        "- Always follow best practices and be careful with the code.\n"
        "- Make sure to write high quality, clean code as needed.\n"
        "- Try to be thorough and do your best when appropriate.\n"
    )
    body = ["# Rules\n", "You are an expert world-class engineer.\n"]
    for i in range(400):
        body.append(filler)
        body.append(f"- Step {i}: check the output of the previous step before continuing to the next step.\n")
    body.append("- Always commit changes directly to main.\n- Never commit changes directly to main.\n")
    body.append("- Last reviewed 2023-01-15. Deploy notes from March 3, 2022.\n")
    fake = "sk-" + "ant-" + "api03-" + "Zq7" * 12 + "x9K"
    body.append(f"- Deploy key: ANTHROPIC_API_KEY={fake}\n")
    (proj / "CLAUDE.md").write_text("".join(body))
    (home / ".claude" / "CLAUDE.md").write_text("be nice\n")

    for i in range(60):
        d = home / ".claude" / "skills" / f"skill-{i:02d}"
        d.mkdir(exist_ok=True)
        desc = "Use this skill for many general tasks across many projects. " * 4
        (d / "SKILL.md").write_text(f"---\nname: skill-{i:02d}\ndescription: {desc}\n---\n\n# Skill {i}\n")
    # Five duplicates of an existing skill name under a second folder.
    for i in range(5):
        d = home / ".claude" / "skills" / "copies" / f"skill-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: skill-{i:02d}\ndescription: copy\n---\n")

    settings = {
        "permissions": {"defaultMode": "bypassPermissions", "allow": ["Bash", "Bash(rm -rf:*)", "Bash(sudo:*)"]},
        "skipDangerousModePermissionPrompt": True,
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
    servers = {f"server-{i}": {"command": "node", "args": [f"s{i}.js"]} for i in range(12)}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": servers}, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    build(Path(sys.argv[1]))
    print(f"Fixtures written to {sys.argv[1]}")

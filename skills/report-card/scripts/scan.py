#!/usr/bin/env python3
"""Agent Report Card scanner.

Reads a Claude Code and/or Codex setup and grades it. Read-only on your
setup: it only writes the report it makes. It never opens a network
connection. Python 3 standard library only.

Usage:
  python3 scan.py [--home DIR] [--project DIR] [--out DIR] [--json]
                  [--adjust -1|0|1] [--note TEXT ...] [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover - older Python
    tomllib = None

VERSION = "0.1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
MAX_READ = 2_000_000  # bytes; larger files are only measured, not read in full

WEIGHTS = {"rules": 30, "context": 20, "memory": 10, "safety": 25, "structure": 15}
CATEGORY_NAMES = {
    "rules": "Rules files",
    "context": "Context load",
    "memory": "Memory",
    "safety": "Safety",
    "structure": "Project setup",
}
LETTERS = ["A", "B", "C", "D", "F"]


# ---------------------------------------------------------------- helpers

def letter_for(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def shift_letter(letter: str, steps: int) -> str:
    """steps > 0 makes the grade better (toward A)."""
    i = LETTERS.index(letter) - steps
    return LETTERS[max(0, min(len(LETTERS) - 1, i))]


def worse_of(a: str, b: str) -> str:
    return a if LETTERS.index(a) >= LETTERS.index(b) else b


def read_text(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_READ)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def load_json(path: Path):
    try:
        return json.loads(read_text(path)) if path.is_file() else None
    except (ValueError, OSError):
        return None


def _mini_toml(text: str) -> dict:
    """Small fallback for Python < 3.11: reads only the keys this scanner needs."""
    out: dict = {}
    section: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("[[") else raw.strip()
        if not line:
            continue
        if line.startswith("[") and not line.startswith("[["):
            name = line.strip("[] ")
            section = [p.strip().strip('"') for p in re.findall(r'"[^"]*"|[^.]+', name)]
            node = out
            for p in section:
                node = node.setdefault(p, {})
            continue
        if line.startswith("[["):
            section = ["__skip__"]
            continue
        m = re.match(r'^([A-Za-z0-9_\-"]+)\s*=\s*(.+)$', line)
        if not m:
            continue
        key, val = m.group(1).strip('"'), m.group(2).strip()
        if val in ("true", "false"):
            v = val == "true"
        elif val.startswith('"') and val.endswith('"'):
            v = val[1:-1]
        else:
            v = val
        node = out
        for p in section:
            node = node.setdefault(p, {})
        if isinstance(node, dict):
            node[key] = v
    out.pop("__skip__", None)
    return out


def load_toml(path: Path):
    if not path.is_file():
        return None
    text = read_text(path)
    if tomllib is None:
        return _mini_toml(text)
    try:
        return tomllib.loads(text)
    except Exception:
        return _mini_toml(text)


def realpath(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def est_tokens(chars: int) -> int:
    return (chars + 3) // 4


class Pretty:
    """Turns real paths into display paths with ~ for the home folder."""

    def __init__(self, home: Path):
        self.home = str(home)

    def __call__(self, path) -> str:
        p = str(path)
        if self.home and (p == self.home or p.startswith(self.home + os.sep)):
            return "~" + p[len(self.home):]
        return p


# ---------------------------------------------------------------- secrets

SECRET_PATTERNS = [
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("1Password token", re.compile(r"\bops_[A-Za-z0-9_\-]{40,}")),
]
GENERIC_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_\-]*(?:api[_\-]?key|secret|token|passwd|password|auth)[A-Z0-9_\-]*)"
    r"\b[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-\.\/\+=]{20,})"
)
PLACEHOLDER_HINTS = ("your", "example", "xxxx", "placeholder", "changeme", "dummy", "redacted", "<", "${", "$(")


def looks_real(value: str) -> bool:
    low = value.lower()
    if any(h in low for h in PLACEHOLDER_HINTS):
        return False
    if len(set(value)) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    if not (has_digit and has_alpha):
        return False
    if "/" in value and value.count("/") >= 2:  # looks like a path
        return False
    return True


def count_secrets(text: str) -> dict:
    """Returns {kind: count}. Never returns the values."""
    found: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    for kind, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in spans):
                continue
            if kind != "Private key" and not looks_real(m.group(0)):
                continue
            spans.append((m.start(), m.end()))
            found[kind] = found.get(kind, 0) + 1
    for m in GENERIC_SECRET.finditer(text):
        if any(a <= m.start(2) < b for a, b in spans):
            continue
        if looks_real(m.group(2)):
            spans.append((m.start(2), m.end(2)))
            found["Password or token"] = found.get("Password or token", 0) + 1
    return found


# ---------------------------------------------------------------- rules files

FILLER_PHRASES = [
    "best practices", "be careful", "make sure to", "as needed", "if possible",
    "when appropriate", "high quality", "high-quality", "be thorough", "do your best",
    "try to", "clean code", "you are an expert", "world-class", "it is important to",
    "please note", "as appropriate", "where necessary", "be helpful", "think step by step",
]
DATE_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
DATE_LONG = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (20\d\d)\b"
)
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
IMPORT_RX = re.compile(r"(?:^|\s)@((?:~|\.{1,2})?/?[\w\-./ ]+\.md)\b")
RULE_VERBS = re.compile(r"\b(always|never|do not|don't|must not|must)\s+([a-z][a-z\-]+)\s+([a-z][a-z\-]+)", re.I)


def normalize_line(line: str) -> str:
    s = line.strip().lower()
    s = re.sub(r"^[\-\*\+>#\d\.\)\s]+", "", s)
    s = re.sub(r"[`*_\[\]]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def analyze_rules_text(text: str, today: dt.date) -> dict:
    lines = text.splitlines()
    words = len(re.findall(r"\b\w+\b", text))
    normalized: list[str] = []
    in_code = False
    headings = 0
    code_blocks = 0
    for raw in lines:
        st = raw.strip()
        if st.startswith("```"):
            in_code = not in_code
            if in_code:
                code_blocks += 1
            continue
        if in_code:
            continue
        if st.startswith("#"):
            headings += 1
            continue
        if st.startswith("|") and set(st) <= set("|-: "):
            continue
        n = normalize_line(st)
        if len(n) >= 25:
            normalized.append(n)
    seen: dict[str, int] = {}
    for n in normalized:
        seen[n] = seen.get(n, 0) + 1
    dup_lines = sum(c - 1 for c in seen.values() if c > 1)

    low = text.lower()
    filler = sum(low.count(p) for p in FILLER_PHRASES)

    stale = 0
    newest = None
    for m in DATE_ISO.finditer(text):
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        newest = max(newest, d) if newest else d
        if (today - d).days > 365:
            stale += 1
    for m in DATE_LONG.finditer(text):
        try:
            d = dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        except (ValueError, KeyError):
            continue
        newest = max(newest, d) if newest else d
        if (today - d).days > 365:
            stale += 1

    always: set[str] = set()
    never: set[str] = set()
    for m in RULE_VERBS.finditer(text):
        verb = m.group(1).lower()
        stem = f"{m.group(2).lower()} {m.group(3).lower()}"
        if verb in ("always", "must"):
            always.add(stem)
        else:
            never.add(stem)

    return {
        "bytes": len(text.encode("utf-8")),
        "lines": len(lines),
        "words": words,
        "tokens": est_tokens(len(text)),
        "headings": headings,
        "code_blocks": code_blocks,
        "dup_lines": dup_lines,
        "filler": filler,
        "stale_dates": stale,
        "newest_date": newest.isoformat() if newest else None,
        "_normalized": sorted(set(normalized)),
        "_always": always,
        "_never": never,
    }


def find_rules_files(home: Path, project: Path) -> list[dict]:
    """Returns rules files the two tools would load, de-duplicated by real path."""
    cands: list[tuple[str, str, Path]] = []  # (label, host, path)
    cands.append(("Global CLAUDE.md", "claude", home / ".claude" / "CLAUDE.md"))
    cands.append(("Global AGENTS.md (Codex)", "codex", home / ".codex" / "AGENTS.md"))
    cands.append(("Global AGENTS.override.md (Codex)", "codex", home / ".codex" / "AGENTS.override.md"))
    if realpath(project) != realpath(home):
        cands.append(("Project CLAUDE.md", "claude", project / "CLAUDE.md"))
        cands.append(("Project .claude/CLAUDE.md", "claude", project / ".claude" / "CLAUDE.md"))
        cands.append(("Project CLAUDE.local.md", "claude", project / "CLAUDE.local.md"))
        cands.append(("Project AGENTS.md", "codex", project / "AGENTS.md"))
        # Parent folders between the project and home are also loaded by both tools.
        parent = project.parent
        hops = 0
        while hops < 6 and realpath(parent) != realpath(home) and parent != parent.parent:
            cands.append(("Parent folder CLAUDE.md", "claude", parent / "CLAUDE.md"))
            cands.append(("Parent folder AGENTS.md", "codex", parent / "AGENTS.md"))
            parent = parent.parent
            hops += 1

    files: dict[str, dict] = {}
    order: list[str] = []
    for label, host, path in cands:
        if not is_file(path):
            continue
        rp = realpath(path)
        if rp in files:
            files[rp]["hosts"].add(host)
            files[rp]["labels"].append(label)
            continue
        files[rp] = {"label": label, "labels": [label], "hosts": {host}, "path": path, "real": rp,
                     "scope": "global" if label.startswith("Global") else ("parent" if label.startswith("Parent") else "project")}
        order.append(rp)

    # One level of @imports (Claude feature).
    for rp in list(order):
        info = files[rp]
        if "claude" not in info["hosts"]:
            continue
        for m in IMPORT_RX.finditer(read_text(info["path"])):
            raw = m.group(1).strip()
            target = Path(os.path.expanduser(raw)) if raw.startswith("~") else (info["path"].parent / raw)
            if is_file(target):
                trp = realpath(target)
                if trp not in files:
                    files[trp] = {"label": "Imported rules file", "labels": ["Imported rules file"], "hosts": {"claude"},
                                  "path": target, "real": trp, "scope": info["scope"]}
                    order.append(trp)
    return [files[rp] for rp in order]


# ---------------------------------------------------------------- skills, plugins, MCP

def skill_description_chars(skill_md: Path) -> int:
    text = read_text(skill_md)[:20000]
    if not text.startswith("---"):
        return min(len(text), 300)
    end = text.find("\n---", 3)
    front = text[3:end if end > 0 else 2000]
    out: list[str] = []
    grab = False
    for line in front.splitlines():
        if line.startswith("description:"):
            out.append(line[len("description:"):].strip().strip(">|").strip())
            grab = True
            continue
        if grab:
            if line.startswith((" ", "\t")):
                out.append(line.strip())
            else:
                break
    return len(" ".join(out)) + 30  # plus the skill name and list overhead


def find_skill_files(root: Path, max_depth: int = 4, skip_hidden: bool = True) -> list[Path]:
    found: list[Path] = []
    if not is_dir(root):
        return found
    seen: set[str] = set()
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        rp = realpath(d)
        if rp in seen:
            continue
        seen.add(rp)
        sk = d / "SKILL.md"
        if is_file(sk):
            found.append(sk)
            continue  # a skill folder does not hold more skills
        if depth >= max_depth:
            continue
        try:
            kids = sorted(os.listdir(d))
        except OSError:
            continue
        for k in kids:
            if skip_hidden and k.startswith("."):
                continue
            if k in ("node_modules", "__pycache__"):
                continue
            p = d / k
            if is_dir(p):
                stack.append((p, depth + 1))
    return sorted(found, key=str)


def count_md(dirpath: Path) -> int:
    if not is_dir(dirpath):
        return 0
    try:
        return sum(1 for k in os.listdir(dirpath) if k.endswith(".md"))
    except OSError:
        return 0


def mcp_names_from(obj) -> list[str]:
    if isinstance(obj, dict):
        return sorted(str(k) for k in obj.keys())
    return []


def collect_claude(home: Path, project: Path, pretty: Pretty) -> dict:
    cdir = home / ".claude"
    present = is_dir(cdir) or is_file(home / ".claude.json")
    skills: list[dict] = []

    def add_skills(root: Path, source: str):
        for sk in find_skill_files(root, max_depth=2):
            skills.append({"name": sk.parent.name, "source": source, "chars": skill_description_chars(sk)})

    add_skills(cdir / "skills", "personal")
    if realpath(project) != realpath(home):
        add_skills(project / ".claude" / "skills", "project")

    commands = count_md(cdir / "commands") + (count_md(project / ".claude" / "commands") if realpath(project) != realpath(home) else 0)
    agents = count_md(cdir / "agents") + (count_md(project / ".claude" / "agents") if realpath(project) != realpath(home) else 0)

    # Settings (global, then project) - merged for permission checks.
    settings_files = []
    for label, p in (("Global settings.json", cdir / "settings.json"),
                     ("Global settings.local.json", cdir / "settings.local.json"),
                     ("Project .claude/settings.json", project / ".claude" / "settings.json"),
                     ("Project .claude/settings.local.json", project / ".claude" / "settings.local.json")):
        if realpath(project) == realpath(home) and label.startswith("Project"):
            continue
        data = load_json(p)
        if isinstance(data, dict):
            settings_files.append((label, p, data))

    enabled_plugins: list[str] = []
    for _, _, data in settings_files:
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for k, v in ep.items():
                if v and k not in enabled_plugins:
                    enabled_plugins.append(k)
    enabled_plugins.sort()

    installed = load_json(cdir / "plugins" / "installed_plugins.json") or {}
    inst_map = installed.get("plugins", {}) if isinstance(installed, dict) else {}
    plugin_mcp: list[str] = []
    plugins_found = 0
    for key in enabled_plugins:
        entries = inst_map.get(key) or []
        if not isinstance(entries, list) or not entries:
            continue
        chosen = None
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("scope") == "user" or (e.get("scope") in ("project", "local") and realpath(Path(str(e.get("projectPath", "")))) == realpath(project)):
                chosen = e
                break
        if not chosen:
            continue
        ip = Path(str(chosen.get("installPath", "")))
        if not is_dir(ip):
            continue
        plugins_found += 1
        pname = key.split("@")[0]
        for sk in find_skill_files(ip / "skills", max_depth=2):
            skills.append({"name": sk.parent.name, "source": f"plugin:{pname}", "chars": skill_description_chars(sk)})
        commands += count_md(ip / "commands")
        agents += count_md(ip / "agents")
        pm = load_json(ip / ".mcp.json")
        if isinstance(pm, dict):
            plugin_mcp += [f"{pname}:{n}" for n in mcp_names_from(pm.get("mcpServers", pm))]

    # MCP servers: user config, project entry in user config, project .mcp.json.
    cj = load_json(home / ".claude.json") or {}
    mcp_blobs = []
    mcp: list[str] = []
    if isinstance(cj, dict):
        mcp += mcp_names_from(cj.get("mcpServers"))
        mcp_blobs.append(("~/.claude.json (MCP section)", cj.get("mcpServers") or {}))
        projs = cj.get("projects")
        if isinstance(projs, dict):
            pe = projs.get(str(project)) or projs.get(realpath(project))
            if isinstance(pe, dict):
                mcp += mcp_names_from(pe.get("mcpServers"))
                mcp_blobs.append(("~/.claude.json (project MCP section)", pe.get("mcpServers") or {}))
    pmj = load_json(project / ".mcp.json") if realpath(project) != realpath(home) else None
    if isinstance(pmj, dict):
        mcp += mcp_names_from(pmj.get("mcpServers"))
        mcp_blobs.append(("Project .mcp.json", pmj.get("mcpServers") or {}))
    for _, _, data in settings_files:
        mcp += mcp_names_from(data.get("mcpServers"))

    # Memory
    mem_dirs = []
    proj_root = cdir / "projects"
    if is_dir(proj_root):
        try:
            for k in sorted(os.listdir(proj_root)):
                md = proj_root / k / "memory"
                if is_dir(md):
                    mem_dirs.append(md)
        except OSError:
            pass
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project))
    proj_mem = proj_root / slug / "memory"
    mem_files = 0
    for md in mem_dirs:
        mem_files += count_md(md)
    index = proj_mem / "MEMORY.md"
    index_lines = len(read_text(index).splitlines()) if is_file(index) else 0

    return {
        "present": present,
        "skills": skills,
        "commands": commands,
        "agents": agents,
        "plugins": plugins_found,
        "mcp": sorted(set(mcp)),
        "plugin_mcp": sorted(set(plugin_mcp)),
        "mcp_blobs": mcp_blobs,
        "settings_files": settings_files,
        "memory_dirs": len(mem_dirs),
        "memory_files": mem_files,
        "project_memory": is_dir(proj_mem) and count_md(proj_mem) > 0,
        "project_memory_index_lines": index_lines,
    }


def collect_codex(home: Path, project: Path) -> dict:
    xdir = home / ".codex"
    present = is_dir(xdir)
    cfg_path = xdir / "config.toml"
    cfg = load_toml(cfg_path) or {}
    cfg_text = read_text(cfg_path) if is_file(cfg_path) else ""

    disabled: set[str] = set()
    sk_cfg = cfg.get("skills") if isinstance(cfg, dict) else None
    if isinstance(sk_cfg, dict):
        for item in sk_cfg.get("config", []) or []:
            if isinstance(item, dict) and item.get("enabled") is False and item.get("path"):
                disabled.add(realpath(Path(str(item["path"]))))

    skills: list[dict] = []

    def add(root: Path, source: str):
        for sk in find_skill_files(root, max_depth=4):
            if realpath(sk) in disabled:
                continue
            skills.append({"name": sk.parent.name, "source": source, "chars": skill_description_chars(sk)})

    add(xdir / "skills", "personal")
    add(home / ".agents" / "skills", "personal")
    if realpath(project) != realpath(home):
        add(project / ".agents" / "skills", "project")
        add(project / ".codex" / "skills", "project")

    plugins_found = 0
    plugins = cfg.get("plugins") if isinstance(cfg, dict) else None
    if isinstance(plugins, dict):
        for key in sorted(plugins):
            val = plugins[key]
            if not (isinstance(val, dict) and val.get("enabled", True)):
                continue
            name, _, market = key.partition("@")
            base = xdir / "plugins" / "cache" / market / name
            if not is_dir(base):
                continue
            try:
                versions = sorted(v for v in os.listdir(base) if not v.startswith("."))
            except OSError:
                versions = []
            if not versions:
                continue
            plugins_found += 1
            add(base / versions[-1] / "skills", f"plugin:{name}")

    mcp = []
    servers = cfg.get("mcp_servers") if isinstance(cfg, dict) else None
    if isinstance(servers, dict):
        for k in sorted(servers):
            v = servers[k]
            if isinstance(v, dict) and v.get("enabled") is False:
                continue
            mcp.append(k)

    mem_dir = xdir / "memories"
    mem_files = count_md(mem_dir)

    return {
        "present": present,
        "config_found": bool(cfg),
        "config_text": cfg_text,
        "sandbox_mode": cfg.get("sandbox_mode") if isinstance(cfg, dict) else None,
        "approval_policy": cfg.get("approval_policy") if isinstance(cfg, dict) else None,
        "skills": skills,
        "plugins": plugins_found,
        "mcp": mcp,
        "memory_files": mem_files,
    }


# ---------------------------------------------------------------- project

CODE_MARKERS = ["package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml", "go.mod",
                "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "Package.swift",
                "deno.json", "mix.exs", "Makefile", "CMakeLists.txt"]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__", ".next", "target", "vendor", "Pods"}
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
TEST_FILE_RX = re.compile(r"(^test_.*\.py$|.*_test\.(py|go|rb|exs)$|.*\.(test|spec)\.[jt]sx?$|.*Tests?\.swift$)")


def collect_project(project: Path, home: Path) -> dict:
    same_as_home = realpath(project) == realpath(home)
    out = {"same_as_home": same_as_home}

    def exists_any(names):
        return any(is_file(project / n) or is_dir(project / n) for n in names)

    out["readme"] = exists_any(["README.md", "README", "README.txt", "readme.md", "README.rst"])
    out["agent_docs"] = exists_any(["docs", "doc", ".agent", "ARCHITECTURE.md", "CONTRIBUTING.md", ".github/copilot-instructions.md"])
    out["git"] = is_dir(project / ".git") or is_file(project / ".git")
    out["gitignore"] = is_file(project / ".gitignore")
    out["ci"] = is_dir(project / ".github" / "workflows") or is_file(project / ".gitlab-ci.yml") or is_dir(project / ".circleci")
    code = exists_any(CODE_MARKERS) or any(
        str(k).endswith((".xcodeproj", ".sln", ".csproj")) for k in (os.listdir(project) if is_dir(project) else []))
    out["code_project"] = bool(code)

    tests = False
    source_files = 0
    if is_dir(project) and not same_as_home:
        budget = 6000
        stack = [(project, 0)]
        while stack and budget > 0 and not tests:
            d, depth = stack.pop()
            try:
                kids = sorted(os.listdir(d))
            except OSError:
                continue
            for k in kids:
                budget -= 1
                if k in SKIP_DIRS or k.startswith("."):
                    continue
                p = d / k
                if is_dir(p):
                    if k.lower() in TEST_DIR_NAMES:
                        tests = True
                        break
                    if depth < 3:
                        stack.append((p, depth + 1))
                else:
                    if TEST_FILE_RX.match(k):
                        tests = True
                        break
                    if k.endswith((".py", ".js", ".ts", ".tsx", ".go", ".rb", ".rs", ".swift", ".java", ".kt")):
                        source_files += 1
    out["tests"] = tests
    if source_files >= 5:
        out["code_project"] = True

    env_exposed = False
    if out["git"] and is_file(project / ".env"):
        gi = read_text(project / ".gitignore") if out["gitignore"] else ""
        env_exposed = not re.search(r"(?m)^\s*\.env(\*|\.\*)?\s*$|^\s*\*\.env\s*$|^\s*\.env\*", gi)
    out["env_not_ignored"] = env_exposed
    return out


# ---------------------------------------------------------------- scoring

class Findings:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, cat: str, fid: str, points: float, fact: str, fix: str | None = None, good: bool = False):
        self.items.append({"category": cat, "id": fid, "points": round(points, 1), "fact": fact, "fix": fix, "good": good})

    def for_cat(self, cat: str):
        return [f for f in self.items if f["category"] == cat]


def score_rules(rules: list[dict], hosts: list[str], analyses: dict, f: Findings) -> float:
    if not rules:
        f.add("rules", "no-rules", 100, "No rules files found (no CLAUDE.md or AGENTS.md).",
              "Write a short rules file for your AI: what this project is, how to run and test it, and what it must never do.")
        return 0.0

    coverage = 0.0
    per_host = 20.0 / max(1, len(hosts))
    for h in hosts:
        if any(r["scope"] == "global" and h in r["hosts"] for r in rules):
            coverage += per_host
        else:
            name = "~/.claude/CLAUDE.md" if h == "claude" else "~/.codex/AGENTS.md"
            tool = "Claude Code" if h == "claude" else "Codex"
            f.add("rules", f"no-global-{h}", per_host, f"No global rules file for {tool}.",
                  f"Add a short global file at {name} with how you like to work (tone, testing, what needs your OK).")
    proj = [r for r in rules if r["scope"] in ("project", "parent")]
    if proj:
        coverage += 20
        missing = [h for h in hosts if not any(h in r["hosts"] for r in proj)]
        if missing and len(hosts) > 1:
            tool = "Codex" if missing[0] == "codex" else "Claude Code"
            fname = "AGENTS.md" if missing[0] == "codex" else "CLAUDE.md"
            coverage -= 5
            f.add("rules", "one-tool-only", 5, f"This project has rules for one tool only. {tool} will not see them.",
                  f"Add a {fname} in the project, or make it a link to the file you already have, so both tools read the same rules.")
    else:
        f.add("rules", "no-project-rules", 20, "This project has no rules file.",
              "Add a CLAUDE.md or AGENTS.md to the project with how to build, test and check work here.")

    quality = 60.0
    total_bytes = 0
    pen_dup = pen_size = pen_struct = 0.0
    for r in rules:
        a = analyses[r["real"]]
        total_bytes += a["bytes"]
        if a["bytes"] < 200:
            pen_size += 10
            f.add("rules", f"tiny:{r['label']}", 10, f"{r['label']} is almost empty ({a['words']} words).",
                  f"Fill in {r['label']} with the few facts your AI keeps getting wrong.")
        elif a["bytes"] > 20000:
            pen_size += 15
            f.add("rules", f"bloated:{r['label']}", 15, f"{r['label']} is very long (about {a['tokens']:,} tokens). Long files get skimmed and cost money on every message.",
                  f"Cut {r['label']} down to the rules that change what the AI does. Move rare how-to steps into separate skills or docs.")
        if a["bytes"] > 1000 and a["headings"] == 0:
            pen_struct += 3
            f.add("rules", f"noheadings:{r['label']}", 3, f"{r['label']} has no section headings.",
                  f"Add a few headings to {r['label']} so rules are easy to find and update.")
        if a["dup_lines"]:
            pen_dup += 2 * a["dup_lines"]
    pen_dup = min(15.0, pen_dup)
    dup_total = sum(analyses[r["real"]]["dup_lines"] for r in rules)
    if dup_total:
        f.add("rules", "dup-lines", pen_dup, f"{dup_total} repeated line(s) inside rules files.",
              "Delete the repeated lines. Saying a rule twice does not make it stronger; it makes the file harder to keep right.")
    quality -= min(25.0, pen_size) + pen_dup + min(9.0, pen_struct)

    if total_bytes > 40000:
        quality -= 10
        f.add("rules", "total-bloat", 10, f"All rules files together are about {est_tokens(total_bytes):,} tokens, loaded before you type anything.",
              "Trim your rules files so they add up to well under 10,000 tokens.")

    # Lines repeated across different files.
    owners: dict[str, set] = {}
    for r in rules:
        for n in analyses[r["real"]]["_normalized"]:
            owners.setdefault(n, set()).add(r["real"])
    cross = sum(1 for v in owners.values() if len(v) > 1)
    if cross:
        p = min(10.0, float(cross))
        quality -= p
        f.add("rules", "cross-dup", p, f"{cross} line(s) appear in more than one rules file.",
              "Keep each rule in one place: shared habits in the global file, project facts in the project file.")

    words = sum(analyses[r["real"]]["words"] for r in rules)
    filler = sum(analyses[r["real"]]["filler"] for r in rules)
    density = (filler * 1000.0 / words) if words else 0.0
    if density > 10:
        quality -= 10
        f.add("rules", "filler", 10, f"Lots of vague wording ({filler} phrases like \"best practices\" or \"be careful\").",
              "Swap vague lines for exact ones. Instead of \"write good tests\", say which command to run and what must pass.")
    elif density > 5:
        quality -= 5
        f.add("rules", "filler", 5, f"Some vague wording ({filler} phrases like \"best practices\" or \"be careful\").",
              "Swap vague lines for exact ones the AI can check.")

    stale = sum(analyses[r["real"]]["stale_dates"] for r in rules)
    if stale:
        p = min(8.0, 2.0 * stale)
        quality -= p
        f.add("rules", "stale", p, f"{stale} date(s) in rules files are more than a year old.",
              "Re-read the rules that mention old dates. Remove anything that is no longer true.")

    always = set().union(*(analyses[r["real"]]["_always"] for r in rules))
    never = set().union(*(analyses[r["real"]]["_never"] for r in rules))
    clash = sorted(always & never)
    if clash:
        p = min(9.0, 3.0 * len(clash))
        quality -= p
        f.add("rules", "contradictions", p, f"{len(clash)} possible contradiction(s): the same action is both required and forbidden.",
              "Search your rules for lines that say \"always\" and \"never\" about the same thing, and keep one.")

    if quality >= 55 and coverage >= 35:
        f.add("rules", "rules-ok", 0, "Rules files are in place and mostly tidy.", good=True)
    return max(0.0, min(100.0, coverage + max(0.0, quality)))


def score_context(claude: dict, codex: dict, hosts: list[str], rules_tokens: dict, f: Findings) -> tuple[float, dict]:
    score = 100.0
    stats = {}
    worst_skill_pen = 0.0
    worst_mcp_pen = 0.0
    dup_pen = 0.0
    load_pen = 0.0
    for h, data in (("claude", claude), ("codex", codex)):
        if h not in hosts:
            continue
        tool = "Claude Code" if h == "claude" else "Codex"
        n_sk = len(data["skills"])
        n_mcp = len(data["mcp"]) + len(data.get("plugin_mcp", []))
        names: dict[str, int] = {}
        for s in data["skills"]:
            names[s["name"]] = names.get(s["name"], 0) + 1
        dups = sorted(n for n, c in names.items() if c > 1)
        desc_tokens = est_tokens(sum(s["chars"] for s in data["skills"]))
        load = rules_tokens.get(h, 0) + desc_tokens + 500 * n_mcp
        stats[h] = {"skills": n_sk, "mcp": n_mcp, "dup_skills": len(dups), "load_tokens": load,
                    "plugins": data["plugins"]}
        if n_sk > 30:
            p = min(35.0, (n_sk - 30) / 2.0)
            if p > worst_skill_pen:
                worst_skill_pen = p
            f.add("context", f"skills-{h}", p, f"{tool} has {n_sk} skills turned on. The AI reads every skill's summary before each task.",
                  f"Turn off the {tool} skills and plugins you have not used in the last month. Aim for 30 or fewer.")
        if n_mcp > 6:
            p = min(25.0, 4.0 * (n_mcp - 6))
            worst_mcp_pen = max(worst_mcp_pen, p)
            f.add("context", f"mcp-{h}", p, f"{tool} has {n_mcp} MCP tool servers connected. Each one adds tool lists the AI must read.",
                  f"Disconnect {tool} MCP servers you rarely use, or turn them on only in the projects that need them.")
        if dups:
            p = min(15.0, 2.0 * len(dups))
            dup_pen = max(dup_pen, p)
            f.add("context", f"dup-skills-{h}", p, f"{tool} has {len(dups)} skill name(s) installed more than once.",
                  "Remove the extra copies so the AI is not choosing between two versions of the same skill.")
        if load > 40000:
            lp = 30.0
        elif load > 20000:
            lp = 20.0
        elif load > 10000:
            lp = 10.0
        else:
            lp = 0.0
        if lp:
            load_pen = max(load_pen, lp)
            f.add("context", f"load-{h}", lp, f"{tool} loads roughly {load:,} tokens of rules, skill summaries and tool lists before you type.",
                  "Cut what loads every time. Less up-front text leaves more room for your actual work.")
    score -= worst_skill_pen + worst_mcp_pen + dup_pen + load_pen
    if score >= 90:
        f.add("context", "context-ok", 0, "Startup load is light.", good=True)
    return max(0.0, score), stats


def score_memory(claude: dict, codex: dict, project: dict, hosts: list[str], f: Findings) -> float:
    global_mem = claude["memory_files"] > 0 or codex["memory_files"] > 0
    proj_mem = claude["project_memory"] or project["agent_docs"]
    if not global_mem and not proj_mem:
        f.add("memory", "no-memory", 80, "No memory or notes system found. Your AI starts from zero every session.",
              "Turn on memory (Claude Code auto memory or Codex memories), or keep a short notes file the AI updates as it learns.")
        return 20.0
    score = 60.0 if global_mem and not proj_mem else (75.0 if proj_mem and not global_mem else 95.0)
    if not proj_mem:
        f.add("memory", "no-project-memory", 25, "Memory is on, but nothing is saved for this project.",
              "Give this project a docs or notes folder the AI can read, so lessons stick to the project.")
    if not global_mem:
        f.add("memory", "no-global-memory", 20, "No saved memory across projects.",
              "Turn on memory so the AI keeps facts about you between projects.")
    if claude["project_memory_index_lines"] > 200:
        score -= 15
        f.add("memory", "memory-index-long", 15, f"The memory index for this project is {claude['project_memory_index_lines']} lines. Only the first 200 lines load.",
              "Shorten the memory index to under 200 lines and move details into topic files.")
    if score >= 90:
        f.add("memory", "memory-ok", 0, "Memory is in use.", good=True)
    return max(0.0, min(100.0, score + (5 if score >= 95 else 0)))


DANGER_ALLOW = [
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r|^Bash\(rm(:|\s)\*\)$"), "delete any files (rm)"),
    (re.compile(r"\bsudo\b"), "run admin commands (sudo)"),
    (re.compile(r"(curl|wget)[^)]*\|\s*(ba|z)?sh"), "download and run scripts"),
    (re.compile(r"push\s+(--force|-f)\b"), "force-push to git"),
    (re.compile(r"chmod\s+777|mkfs|\bdd\s"), "change disks or open file permissions"),
]


def score_safety(claude: dict, codex: dict, rules: list[dict], project: dict, hosts: list[str], secrets: dict, f: Findings) -> float:
    score = 100.0
    # Claude permissions
    allow: list[str] = []
    deny: list[str] = []
    default_mode = None
    skip_prompt = False
    guard_hook = False
    env_secret = 0
    for label, _, data in claude["settings_files"]:
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
        allow += [str(x) for x in perms.get("allow", []) or []]
        deny += [str(x) for x in perms.get("deny", []) or []]
        if perms.get("defaultMode"):
            default_mode = perms.get("defaultMode")
        if data.get("skipDangerousModePermissionPrompt") or data.get("dangerouslySkipPermissions"):
            skip_prompt = True
        hooks = data.get("hooks")
        if isinstance(hooks, dict) and hooks.get("PreToolUse"):
            guard_hook = True
    if default_mode == "bypassPermissions":
        score -= 25
        f.add("safety", "bypass", 25, "Claude Code is set to skip all permission checks (bypass mode).",
              "Switch Claude Code's default mode from bypass to normal or accept-edits, and allow only the commands you trust.")
    if skip_prompt:
        score -= 5
        f.add("safety", "skip-prompt", 5, "The warning before entering bypass mode is turned off.",
              "Turn the bypass-mode warning back on.")
    bare_bash = any(a.strip() in ("Bash", "Bash(*)", "Bash(:*)", "Bash(**)") for a in allow)
    if bare_bash:
        score -= 15
        f.add("safety", "bash-all", 15, "Claude Code may run any shell command without asking.",
              "Replace the blanket shell permission with a short list of safe commands (tests, builds, git status).")
    hits = []
    for a in allow:
        for rx, what in DANGER_ALLOW:
            if rx.search(a) and what not in hits:
                hits.append(what)
    if hits:
        p = min(20.0, 10.0 * len(hits))
        score -= p
        f.add("safety", "danger-allow", p, "Claude Code is allowed to " + "; ".join(hits) + " without asking.",
              "Remove allow rules for risky commands. Let the AI ask first for anything that deletes, force-pushes or needs admin rights.")
    if bare_bash and not deny:
        score -= 5
        f.add("safety", "no-deny", 5, "Shell is fully allowed and there are no deny rules as a backstop.",
              "Add deny rules for the commands that should never run (for example dropping a database).")

    # Codex sandbox
    if "codex" in hosts:
        sm, ap = codex.get("sandbox_mode"), codex.get("approval_policy")
        if sm == "danger-full-access":
            score -= 20
            f.add("safety", "codex-sandbox", 20, "Codex runs with the sandbox off (full access to your computer).",
                  "Set Codex sandbox_mode to workspace-write so it can only change files in your project.")
            if ap == "never":
                score -= 10
                f.add("safety", "codex-never-ask", 10, "Codex never asks before acting, and the sandbox is off.",
                      "Set Codex approval_policy to on-request so it asks before risky steps.")

    # Secrets
    rules_hits = secrets.get("rules", {})
    cfg_hits = secrets.get("config", {})
    if rules_hits:
        n = sum(sum(v.values()) for v in rules_hits.values())
        score -= 40
        f.add("safety", "secret-in-rules", 40, f"{n} secret-looking value(s) found in rules files: " + ", ".join(sorted(rules_hits)) + ". The AI reads these files every session.",
              "Move keys and passwords out of rules files into a password manager or environment variables, then change those keys.")
    if cfg_hits:
        n = sum(sum(v.values()) for v in cfg_hits.values())
        score -= 15
        f.add("safety", "secret-in-config", 15, f"{n} secret-looking value(s) saved in plain text in tool settings: " + ", ".join(sorted(cfg_hits)) + ".",
              "Load these keys from environment variables or a password manager instead of pasting them into config files.")
    if project.get("env_not_ignored"):
        score -= 10
        f.add("safety", "env-not-ignored", 10, "This project has a .env file that git is not told to ignore.",
              "Add .env to .gitignore so secrets do not get committed.")
    if guard_hook and score < 100:
        score += 5
        f.add("safety", "guard-hook", 0, "A safety hook checks tool calls before they run.", good=True)
    if score >= 90:
        f.add("safety", "safety-ok", 0, "No risky permission settings or exposed secrets found.", good=True)
    return max(0.0, min(100.0, score))


def score_structure(project: dict, has_project_rules: bool, f: Findings) -> float:
    if project["same_as_home"]:
        f.add("structure", "no-project", 50, "The report was run from your home folder, not a project.",
              "Run the report from inside a project folder to grade that project.")
        return 50.0
    parts = [("readme", 25), ("agent_docs", 20), ("git", 15), ("gitignore", 5)]
    if project["code_project"]:
        parts += [("tests", 25), ("ci", 10)]
    total = sum(w for _, w in parts)
    got = 0
    msgs = {
        "readme": ("No README.", "Add a README that says what the project is and how to run it. Your AI reads it too."),
        "agent_docs": ("No docs folder or guide for the AI.", "Add a docs/ folder with how the project fits together, so the AI does not guess."),
        "git": ("Not a git repository.", "Put the project in git so you can undo AI changes safely."),
        "gitignore": ("No .gitignore file.", "Add a .gitignore so build files and secrets stay out of git."),
        "tests": ("No tests found.", "Add even a few tests. Tests are how your AI checks its own work."),
        "ci": ("No automatic checks (CI).", "Add a simple CI check that runs your tests on every change."),
    }
    for key, w in parts:
        if project.get(key):
            got += w
        else:
            fact, fix = msgs[key]
            f.add("structure", f"missing-{key}", w * 100.0 / total, fact, fix)
    if not project["code_project"]:
        f.add("structure", "not-code", 0, "No code found here, so tests and CI were not graded.", good=True)
    if got == total:
        f.add("structure", "structure-ok", 0, "README, docs, git and tests are all in place.", good=True)
    return got * 100.0 / total


# ---------------------------------------------------------------- main scan

def top_three(fixes: list) -> list:
    """Best three fixes, counting the same fix for two tools only once."""
    out, seen = [], set()
    for x in fixes:
        family = re.sub(r"-(claude|codex)$", "", x["id"])
        if family in seen:
            continue
        seen.add(family)
        out.append({"category": x["category"], "fix": x["fix"], "why": x["fact"]})
        if len(out) == 3:
            break
    return out


def scan(home: Path, project: Path, today: dt.date, adjust: int = 0, notes: list[str] | None = None) -> dict:
    pretty = Pretty(home)
    claude = collect_claude(home, project, pretty)
    codex = collect_codex(home, project)
    hosts = [h for h, d in (("claude", claude), ("codex", codex)) if d["present"]]
    hosts_assumed = False
    if not hosts:
        hosts = ["claude", "codex"]
        hosts_assumed = True

    rules = find_rules_files(home, project)
    analyses = {r["real"]: analyze_rules_text(read_text(r["path"]), today) for r in rules}
    rules_tokens = {"claude": 0, "codex": 0}
    for r in rules:
        for h in r["hosts"]:
            rules_tokens[h] += analyses[r["real"]]["tokens"]

    secrets = {"rules": {}, "config": {}}
    for r in rules:
        c = count_secrets(read_text(r["path"]))
        if c:
            secrets["rules"][r["label"]] = c
    for label, _, data in claude["settings_files"]:
        c = count_secrets(json.dumps(data))
        if c:
            secrets["config"][f"Claude {label}"] = c
    for label, blob in claude["mcp_blobs"]:
        c = count_secrets(json.dumps(blob))
        if c:
            secrets["config"][label] = c
    if codex["config_text"]:
        c = count_secrets(codex["config_text"])
        if c:
            secrets["config"]["Codex config.toml"] = c

    project_info = collect_project(project, home)
    findings = Findings()
    scores = {
        "rules": score_rules(rules, hosts, analyses, findings),
    }
    ctx_score, ctx_stats = score_context(claude, codex, hosts, rules_tokens, findings)
    scores["context"] = ctx_score
    scores["memory"] = score_memory(claude, codex, project_info, hosts, findings)
    scores["safety"] = score_safety(claude, codex, rules, project_info, hosts, secrets, findings)
    has_proj_rules = any(r["scope"] in ("project", "parent") for r in rules)
    scores["structure"] = score_structure(project_info, has_proj_rules, findings)

    weighted = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS) / sum(WEIGHTS.values())
    base_letter = letter_for(weighted)
    caps = []
    cap_letter = "A"
    if not rules:
        cap_letter = worse_of(cap_letter, "D")
        caps.append("No rules files, so the best possible grade is D.")
    if secrets["rules"]:
        cap_letter = worse_of(cap_letter, "C")
        caps.append("A secret was found in a rules file, so the best possible grade is C.")
    adjust = max(-1, min(1, int(adjust or 0)))
    letter = shift_letter(base_letter, adjust)
    letter = worse_of(letter, cap_letter)

    fixes = sorted((x for x in findings.items if x["fix"] and not x["good"]),
                   key=lambda x: (-x["points"] * WEIGHTS[x["category"]], list(WEIGHTS).index(x["category"]), x["id"]))

    total_skills = sum(len(d["skills"]) for h, d in (("claude", claude), ("codex", codex)) if h in hosts)
    total_mcp = sum(ctx_stats[h]["mcp"] for h in ctx_stats)
    secret_count = sum(sum(v.values()) for grp in secrets.values() for v in grp.values())
    safety_flags = len([x for x in findings.for_cat("safety") if not x["good"]])

    clean_notes = []
    for n in (notes or [])[:3]:
        n = re.sub(r"\s+", " ", str(n)).strip()[:240]
        if n and not any(sum(c.values()) for c in [count_secrets(n)]):
            clean_notes.append(n)

    return {
        "version": VERSION,
        "date": today.isoformat(),
        "hosts": hosts,
        "hosts_assumed": hosts_assumed,
        "overall": {"score": round(weighted, 1), "base_letter": base_letter, "letter": letter,
                    "adjust": adjust, "caps": caps},
        "categories": {k: {"name": CATEGORY_NAMES[k], "score": round(scores[k], 1), "letter": letter_for(scores[k]),
                           "weight": WEIGHTS[k]} for k in WEIGHTS},
        "rules_files": [{"label": r["label"], "also": r["labels"][1:], "path": pretty(r["path"]),
                         "tools": sorted(r["hosts"]),
                         **{k: v for k, v in analyses[r["real"]].items() if not k.startswith("_")}} for r in rules],
        "context": ctx_stats,
        "claude": {"present": claude["present"], "skills": len(claude["skills"]), "commands": claude["commands"],
                   "agents": claude["agents"], "plugins": claude["plugins"], "mcp": len(claude["mcp"]) + len(claude["plugin_mcp"]),
                   "memory_files": claude["memory_files"], "project_memory": claude["project_memory"]},
        "codex": {"present": codex["present"], "skills": len(codex["skills"]), "plugins": codex["plugins"],
                  "mcp": len(codex["mcp"]), "memory_files": codex["memory_files"],
                  "sandbox_mode": codex["sandbox_mode"], "approval_policy": codex["approval_policy"]},
        "project": {k: v for k, v in project_info.items()},
        "project_path": pretty(project),
        "secrets": {grp: {label: sum(c.values()) for label, c in v.items()} for grp, v in secrets.items()},
        "findings": findings.items,
        "top_fixes": top_three(fixes),
        "notes": clean_notes,
        "share": {
            "overall": letter,
            "grades": {CATEGORY_NAMES[k]: letter_for(scores[k]) for k in WEIGHTS},
            "counts": {
                "Rules files found": len(rules),
                "Words in rules files": sum(a["words"] for a in analyses.values()),
                "Skills turned on": total_skills,
                "MCP servers": total_mcp,
                "Secrets found in plain text": secret_count,
                "Safety flags": safety_flags,
            },
        },
    }


# ---------------------------------------------------------------- output

def load_config() -> dict:
    cfg = load_json(SKILL_DIR / "config.json") or {}
    return {
        "setup_day_url": cfg.get("setup_day_url") or "https://withmana.ai",
        "bootcamp_url": cfg.get("bootcamp_url") or cfg.get("setup_day_url") or "https://withmana.ai",
    }


GRADE_COLOR = {"A": "#15803d", "B": "#4d7c0f", "C": "#b45309", "D": "#c2410c", "F": "#b91c1c"}


def share_card_html(share: dict) -> str:
    e = html.escape
    rows = "".join(f"<div class='sc-row'><span>{e(k)}</span><b style='color:{GRADE_COLOR[v]}'>{e(v)}</b></div>"
                   for k, v in share["grades"].items())
    counts = "".join(f"<div class='sc-row'><span>{e(k)}</span><b>{int(v):,}</b></div>" for k, v in share["counts"].items())
    return (f"<div class='share' id='share-card'><div class='sc-head'>My AI setup report card</div>"
            f"<div class='sc-grade' style='color:{GRADE_COLOR[share['overall']]}'>{e(share['overall'])}</div>"
            f"{rows}<div class='sc-sep'></div>{counts}<div class='sc-foot'>Agent Report Card</div></div>")


def render_html(r: dict, cfg: dict) -> str:
    e = html.escape
    o = r["overall"]
    cats = r["categories"]
    tools = " and ".join("Claude Code" if h == "claude" else "Codex" for h in r["hosts"])
    if r["hosts_assumed"]:
        tools = "no AI tool settings found"

    cat_cards = []
    for k, c in cats.items():
        items = [x for x in r["findings"] if x["category"] == k]
        bad = [x for x in items if not x["good"]]
        good = [x for x in items if x["good"]]
        lis = "".join(f"<li>{e(x['fact'])}</li>" for x in bad[:6]) + "".join(f"<li class='ok'>{e(x['fact'])}</li>" for x in good[:2])
        cat_cards.append(
            f"<div class='card'><div class='card-top'><div><div class='cat'>{e(c['name'])}</div>"
            f"<div class='muted'>Score {c['score']:.0f} of 100 · counts for {c['weight']}%</div></div>"
            f"<div class='grade' style='color:{GRADE_COLOR[c['letter']]}'>{e(c['letter'])}</div></div>"
            f"<ul>{lis or '<li class=ok>Nothing to flag.</li>'}</ul></div>")

    fixes = "".join(
        f"<li><b>{e(x['fix'])}</b><div class='muted'>Why: {e(x['why'])}</div></li>" for x in r["top_fixes"]
    ) or "<li>Nothing major to fix. Nice work.</li>"

    rf_rows = "".join(
        f"<tr><td>{e(x['label'])}{(' <span class=muted>(also ' + e(', '.join(x['also'])) + ')</span>') if x['also'] else ''}</td>"
        f"<td>{e(', '.join('Claude' if t == 'claude' else 'Codex' for t in x['tools']))}</td>"
        f"<td>{x['words']:,}</td><td>{x['tokens']:,}</td><td>{x['dup_lines']}</td></tr>"
        for x in r["rules_files"]) or "<tr><td colspan=5>None found.</td></tr>"
    rf_paths = "".join(f"<li><code>{e(x['path'])}</code></li>" for x in r["rules_files"])

    ctx_rows = "".join(
        f"<tr><td>{'Claude Code' if h == 'claude' else 'Codex'}</td><td>{s['skills']}</td><td>{s['plugins']}</td>"
        f"<td>{s['mcp']}</td><td>{s['load_tokens']:,}</td></tr>" for h, s in r["context"].items())

    notes = ""
    if r["notes"]:
        sign = {1: "raised by one letter", -1: "lowered by one letter", 0: "not changed"}[o["adjust"]]
        notes = ("<section><h2>Reading your rules</h2><p class='muted'>Your AI read your rules files and added these notes. "
                 f"This step can move the overall grade by one letter at most. It was {sign}.</p><ul>"
                 + "".join(f"<li>{e(n)}</li>" for n in r["notes"]) + "</ul></section>")
    caps = "".join(f"<p class='muted'>{e(c)}</p>" for c in o["caps"])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>Agent Report Card: {e(o['letter'])}</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f6f6f4; color: #1c1c1a; font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
header > div {{ min-width: 0; flex: 1 1 220px; }}
li, p, td {{ overflow-wrap: anywhere; }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }}
header {{ display: flex; gap: 20px; align-items: center; flex-wrap: wrap; background: #fff; border: 1px solid #e4e4e0; border-radius: 14px; padding: 20px; }}
.big {{ font-size: 88px; font-weight: 800; line-height: 1; min-width: 90px; text-align: center; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
h2 {{ font-size: 19px; margin: 32px 0 10px; }}
.muted {{ color: #66665f; font-size: 14px; }}
.privacy {{ margin-top: 8px; font-size: 14px; background: #eef6ee; color: #1f4d2a; border-radius: 8px; padding: 6px 10px; display: inline-block; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }}
.card {{ background: #fff; border: 1px solid #e4e4e0; border-radius: 12px; padding: 14px 16px; }}
.card-top {{ display: flex; justify-content: space-between; align-items: start; gap: 8px; }}
.cat {{ font-weight: 700; }}
.grade {{ font-size: 40px; font-weight: 800; line-height: 1; }}
.card ul {{ margin: 10px 0 0; padding-left: 18px; font-size: 14px; }}
.card li {{ margin-bottom: 4px; }}
li.ok {{ color: #2f6b3a; }}
ol.fixes {{ background: #fff; border: 1px solid #e4e4e0; border-radius: 12px; padding: 14px 16px 14px 36px; margin: 0; }}
ol.fixes li {{ margin-bottom: 10px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 14px; border: 1px solid #e4e4e0; border-radius: 12px; overflow: hidden; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; }}
th {{ background: #fafaf8; font-weight: 600; }}
.scroll {{ overflow-x: auto; }}
code {{ font-size: 13px; word-break: break-all; }}
details {{ margin-top: 8px; font-size: 14px; }}
.share {{ max-width: 360px; background: #fff; border: 2px solid #1c1c1a; border-radius: 16px; padding: 18px; }}
.sc-head {{ font-weight: 700; }}
.sc-grade {{ font-size: 64px; font-weight: 800; line-height: 1.1; }}
.sc-row {{ display: flex; justify-content: space-between; font-size: 15px; padding: 2px 0; }}
.sc-sep {{ border-top: 1px solid #e4e4e0; margin: 8px 0; }}
.sc-foot {{ margin-top: 10px; font-size: 12px; color: #66665f; }}
.cta {{ background: #1c1c1a; color: #fff; border-radius: 14px; padding: 20px; margin-top: 32px; }}
.cta a {{ color: #fff; font-weight: 700; }}
.cta p {{ margin: 6px 0; }}
footer {{ margin-top: 24px; }}
</style></head>
<body><main>
<header>
  <div class="big" style="color:{GRADE_COLOR[o['letter']]}">{e(o['letter'])}</div>
  <div>
    <h1>Your AI coding setup report card</h1>
    <div class="muted">Checked {e(r['date'])} · Tools found: {e(tools)} · Score {o['score']:.0f} of 100</div>
    <div class="privacy">Made on your computer. Nothing was uploaded or sent anywhere.</div>
    {caps}
  </div>
</header>

<h2>Fix these first</h2>
<ol class="fixes">{fixes}</ol>

<h2>Grades by area</h2>
<div class="grid">{''.join(cat_cards)}</div>

{notes}

<h2>What we looked at</h2>
<div class="scroll"><table><tr><th>Rules file</th><th>Read by</th><th>Words</th><th>Tokens (about)</th><th>Repeated lines</th></tr>{rf_rows}</table></div>
<details><summary>Show file locations</summary><ul>{rf_paths or '<li>None</li>'}</ul><p class="muted">Project checked: <code>{e(r['project_path'])}</code></p></details>
<div class="scroll" style="margin-top:12px"><table><tr><th>Tool</th><th>Skills on</th><th>Plugins</th><th>MCP servers</th><th>Loaded before you type (tokens, about)</th></tr>{ctx_rows or '<tr><td colspan=5>No tool settings found.</td></tr>'}</table></div>

<h2>Share your grade</h2>
<p class="muted">This card shows only grades and counts. No file names, folder names, project names or file contents. Screenshot it if you want to share.</p>
{share_card_html(r['share'])}

<div class="cta">
  <p><a href="{e(cfg['setup_day_url'])}">Want this fixed for you in a day? Setup Day</a></p>
  <p>Setting up a whole team? Ask about the <a href="{e(cfg['bootcamp_url'])}">Mana AI Bootcamp</a>.</p>
</div>

<footer class="muted">
  <p>How grades work: five areas are scored from 0 to 100 and weighted (rules files 30%, safety 25%, context load 20%, project setup 15%, memory 10%).
  A is 90 or more, B 80, C 70, D 60, below that F. The same setup always gets the same scores. Your AI's read of your rules can move the overall grade by one letter at most.</p>
  <p>Agent Report Card {e(r['version'])}. Secrets are counted, never shown.</p>
</footer>
</main></body></html>
"""


def terminal_summary(r: dict, out_file: Path | None) -> str:
    o = r["overall"]
    lines = [f"Agent Report Card: overall {o['letter']} ({o['score']:.0f}/100)"]
    for c in r["categories"].values():
        lines.append(f"  {c['name']:<14} {c['letter']}  ({c['score']:.0f})")
    for c in o["caps"]:
        lines.append(f"  Note: {c}")
    if r["top_fixes"]:
        lines.append("Fix these first:")
        for i, x in enumerate(r["top_fixes"], 1):
            lines.append(f"  {i}. {x['fix']}")
    if out_file:
        lines.append(f"Report: {out_file}")
    lines.append("Everything ran on this computer. Nothing was sent anywhere.")
    return "\n".join(lines)


def default_out_dir() -> Path:
    cwd = Path.cwd() / "agent-report-card"
    try:
        cwd.mkdir(exist_ok=True)
        probe = cwd / ".write-test"
        probe.write_text("")
        probe.unlink()
        return cwd
    except OSError:
        return Path(tempfile.gettempdir()) / "agent-report-card"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Grade your Claude Code / Codex setup. Runs locally, sends nothing.")
    ap.add_argument("--home", default=os.environ.get("ARC_HOME") or str(Path.home()), help="Home folder to check (default: yours)")
    ap.add_argument("--project", default=os.environ.get("ARC_PROJECT") or os.getcwd(), help="Project folder to check (default: current folder)")
    ap.add_argument("--out", default=os.environ.get("ARC_OUT"), help="Folder for the report (default: ./agent-report-card)")
    ap.add_argument("--today", help="Date to grade against, YYYY-MM-DD (default: today)")
    ap.add_argument("--adjust", type=int, default=0, choices=[-1, 0, 1], help="Qualitative adjustment from reading the rules: -1, 0 or 1 letter")
    ap.add_argument("--note", action="append", default=[], help="Short qualitative note (up to 3)")
    ap.add_argument("--json", action="store_true", help="Print the facts as JSON")
    ap.add_argument("--no-html", action="store_true", help="Do not write the HTML report")
    args = ap.parse_args(argv)

    home = Path(os.path.expanduser(args.home)).absolute()
    project = Path(os.path.expanduser(args.project)).absolute()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    result = scan(home, project, today, args.adjust, args.note)

    out_file = None
    if not args.no_html:
        out_dir = Path(os.path.expanduser(args.out)) if args.out else default_out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = (out_dir / "report-card.html").absolute()
        out_file.write_text(render_html(result, load_config()), encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2, default=list))
    else:
        print(terminal_summary(result, out_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())

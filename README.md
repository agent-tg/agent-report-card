# Agent Report Card

A free report card for your AI coding setup. It checks how you have set up Claude Code or Codex and gives you a letter grade, a grade for each area, and the three fixes that will help most. The report is a single web page saved on your computer.

Most people never open their `CLAUDE.md` or `AGENTS.md`. Those files, plus your skills, tools and permission settings, decide how well your AI works. This plugin shows you where yours stand in about two minutes.

## What it grades

| Area | What it looks at |
| --- | --- |
| Rules files | Do your global and project `CLAUDE.md` / `AGENTS.md` exist? Are they too empty or too long? Repeated lines, old dates, vague wording, rules that contradict each other. |
| Context load | Mostly how much text loads before you type (rules, skill summaries, tool servers), using your own skill-text limits when it can read them. Very large skill counts and duplicates cost a little. |
| Memory | Whether your AI keeps notes between sessions, globally and for this project. |
| Safety | Permission settings, risky "allow" rules, and passwords or keys sitting in plain text. Secrets are counted, never shown. Safety hooks that check shell commands lower the penalty for wide permissions. |
| Project setup | README, docs, git, tests and automatic checks in the current project. Not graded if the folder is not a project (no code and no git). |

See [RUBRIC.md](RUBRIC.md) for exactly how points are given.

## Install

### Claude Code

```bash
claude plugin marketplace add <this repository URL or local folder>
claude plugin install agent-report-card@agent-report-card
```

(or the same two steps inside Claude Code: `/plugin marketplace add ...` and `/plugin install ...`)

Then, inside any project, run:

```text
/agent-report-card:report-card
```

or just ask: "Give my setup a report card."

### Codex

```bash
codex plugin marketplace add <this repository URL or local folder>
codex plugin add agent-report-card@agent-report-card
```

Then ask Codex: "Give my AI coding setup a report card." (or use `$report-card`).

### No plugin system? Run it directly

You only need Python 3.9 or newer. From inside your project:

```bash
python3 skills/report-card/scripts/scan.py
```

You can also copy the `skills/report-card` folder into `~/.claude/skills/` or `~/.codex/skills/`. It works on its own.

Options: `--project DIR` and `--home DIR` pick what to check, `--out DIR` (or `ARC_OUT`) picks where the report goes, `--json` prints the raw facts.

## What it reads

Read-only. It never changes your files.

- `~/.claude/` : `CLAUDE.md`, `settings.json`, skills, commands, agents, installed plugin list, memory folders
- `~/.claude.json` : only the list of MCP servers
- `~/.codex/` : `AGENTS.md`, `config.toml`, skills, plugin cache, memories
- `~/.agents/skills/`
- Your project: `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.mcp.json`, README, docs, tests, `.gitignore`, CI folder, and rules files in parent folders

It writes one file, in a folder in your home, never inside your project: `~/agent-report-card/report-card-<project-folder-name>.html` (or your system temp folder if it cannot write there). It prints the full path and opens nothing by itself.

## Privacy

- Everything runs on your computer. Nothing is uploaded or sent anywhere.
- The scanner uses only the Python standard library and makes no network calls. You can check: it does not import any networking module.
- Secret-looking values are counted by file name only. The value is never stored, printed or put in the report.
- The "share card" in the report shows only grades and counts: no file names, folder names, project names or file contents.
- If your AI adds its own short read of your rules files, that happens inside your own Claude Code or Codex session, the same as any other request you make to it.

## Same setup, same grade

The scanner's grades are fixed rules on things it can count, so the same setup gets the same scores every time (dates are checked against today's date). Your AI may then read your rules files for clarity and contradictions. That read can move the overall grade by one letter at most, and can never lift it above a hard cap.

## Want it fixed for you?

The report links to **Setup Day**: we fix your setup for you in a day. For teams, ask about the **Mana AI Bootcamp**. Links are set in `skills/report-card/config.json`.

## Setup Day page

`docs/index.html` is a plain one-page description of Setup Day, ready for GitHub Pages. The booking link is the `BOOK_CALL_URL` value at the top of that file.

## Tests

```bash
python3 -m unittest discover tests
```

## License

MIT. See [LICENSE](LICENSE).

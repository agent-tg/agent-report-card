---
name: report-card
description: Grade the user's Claude Code or Codex setup (rules files, context load, memory, safety, project setup) with a letter grade and a local HTML report. Use when the user asks for a report card, setup grade, or setup check-up. Runs locally; sends nothing.
---

# Agent Report Card

Give the user a letter grade for their AI coding setup in under two minutes. Everything runs on their computer. Do not send anything anywhere.

## 1. Run the scanner

The scanner is `scripts/scan.py` in this skill's folder. Run it from the user's current project folder:

```bash
python3 "<this skill folder>/scripts/scan.py" --json --no-html
```

If `python3` is missing, tell the user the report needs Python 3.9 or newer and stop.

The JSON lists the grades, every finding, and the rules files it found (`rules_files[].path`). It never contains secret values. Do not change any of the user's files.

## 2. Read the rules files (bounded)

Read each file in `rules_files` (skip any over 3,000 lines; read the first 3,000). Judge only two things:

- **Clarity:** could a new teammate follow these rules without asking questions?
- **Contradictions:** do two rules tell the AI to do opposite things?

Pick one adjustment:

- `1` if the rules are unusually clear, specific, and free of contradictions.
- `-1` if they are confusing, mostly vague, or contradict each other in ways that matter.
- `0` otherwise. Use `0` when unsure, and always when no rules files exist.

Write up to three short notes (one sentence each) that explain your adjustment. Notes must not quote file contents, secrets, names of people, clients or projects, or paths.

## 3. Make the report

```bash
python3 "<this skill folder>/scripts/scan.py" --adjust <-1|0|1> --note "<note 1>" --note "<note 2>"
```

The scanner clamps the adjustment to one letter and keeps any hard caps (for example, a secret in a rules file caps the grade at C). It writes the report to `~/agent-report-card/` in the user's home folder, never inside the project (or the system temp folder if home is not writable), and prints a short summary with the full path.

## 4. Tell the user

Reply in plain words, in this order:

1. The overall grade and the grade for each area.
2. The top three fixes from the summary, as written.
3. The full path to the HTML report. Do not open it yourself; offer to open it (`open` on macOS, `xdg-open` on Linux, `start` on Windows) and wait for a yes.
4. One line: "Everything ran on your computer. Nothing was sent anywhere."

Do not fix anything unless the user asks. If they ask, fix one item at a time and show the change first.

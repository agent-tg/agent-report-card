# Scoring Rubric

Each area gets a score from 0 to 100. The overall score is a weighted average.

| Area | Weight |
| --- | --- |
| Rules files | 30% |
| Safety | 25% |
| Context load | 20% |
| Project setup | 15% |
| Memory | 10% |

Letters: A is 90 or more, B is 80 to 89, C is 70 to 79, D is 60 to 69, F is below 60.

"Tools in use" means Claude Code if `~/.claude` exists and Codex if `~/.codex` exists. If neither exists, both are assumed.

## Rules files (0 to 100)

**Coverage, up to 40 points**
- Up to 20 for a global rules file for each tool in use (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`), split evenly between tools.
- 20 for a rules file in the project or a parent folder. Minus 5 if two tools are in use but only one can read the project rules.

**Quality, up to 60 points, minus:**
- 10 for each file under 200 bytes; 15 for each file over 20 KB (size losses capped at 25).
- 3 for each file over 1 KB with no headings (capped at 9).
- 2 for each repeated line inside a file (capped at 15).
- 10 if all rules files together are over 40 KB.
- 1 for each line found in more than one file (capped at 10).
- 5 if vague phrases ("best practices", "be careful", "try to", and similar) pass 5 per 1,000 words; 10 if they pass 10.
- 2 for each date more than a year old (capped at 8).
- 3 for each possible contradiction: the same two-word action after both "always/must" and "never/do not" (capped at 9).

No rules files at all scores 0.

## Context load (starts at 100)

Checked per tool; the worst tool counts.
- Skills over 30: minus 1 point per 2 extra skills (capped at 35).
- MCP servers over 6: minus 4 each (capped at 25).
- Skill names installed more than once: minus 2 each (capped at 15).
- Text loaded before you type (rules + skill summaries + about 500 tokens per MCP server): minus 10 over 10,000 tokens, 20 over 20,000, 30 over 40,000.

## Memory

- 20: no memory or notes found.
- 60: memory across projects only.
- 75: project notes or docs only.
- 100: both.
- Minus 15 if this project's memory index is over 200 lines (only the first 200 load).

## Safety (starts at 100)

- Minus 25: Claude Code default mode is bypass.
- Minus 5: bypass-mode warning turned off.
- Minus 15: any shell command allowed without asking. Minus 5 more if there are no deny rules.
- Minus 10 per risky allow rule type (delete files, sudo, download-and-run, force-push, disk or permission changes), capped at 20.
- Minus 20: Codex sandbox is `danger-full-access`. Minus 10 more if `approval_policy` is `never`.
- Minus 40: a secret-looking value in a rules file.
- Minus 15: secret-looking values in tool settings or MCP config.
- Minus 10: a project `.env` that git does not ignore.
- Plus 5 (up to 100): a hook that checks tool calls before they run.

## Project setup

Points out of the parts that apply: README 25, docs folder 20, git 15, `.gitignore` 5. For code projects also tests 25 and CI 10. Run from your home folder, it scores 50.

## Hard caps

- No rules files: overall grade is at most D.
- A secret in a rules file: overall grade is at most C.

## The AI's read

After the scan, your AI may read your rules files for clarity and contradictions and move the overall grade up or down by one letter. It cannot move it more than one letter or past a hard cap. Area grades never change.

## Top 3 fixes

Each finding has a point loss. Fixes are ranked by point loss times the area weight. Ties go to the area listed first above, then by name.

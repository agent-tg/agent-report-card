"""Tests for the Agent Report Card scanner. Run: python3 -m unittest discover tests"""

import datetime as dt
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "report-card" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixtures  # noqa: E402
import scan  # noqa: E402

TODAY = dt.date(2026, 11, 18)


class ScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        make_fixtures.build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_scan(self, name, **kw):
        base = self.root / name
        return scan.scan(base / "home", base / "project", TODAY, **kw)

    def test_empty_grades_poorly(self):
        r = self.run_scan("empty")
        self.assertIn(r["overall"]["letter"], ("D", "F"))
        self.assertEqual(r["categories"]["rules"]["score"], 0)
        self.assertTrue(any("best possible grade is D" in c for c in r["overall"]["caps"]))

    def test_messy_grades_poorly_for_other_reasons(self):
        r = self.run_scan("messy")
        self.assertIn(r["overall"]["letter"], ("D", "F"))
        ids = {f["id"] for f in r["findings"]}
        self.assertIn("secret-in-rules", ids)
        self.assertIn("load-claude", ids)
        self.assertIn("dup-lines", ids)
        self.assertIn("bypass", ids)
        self.assertIn("contradictions", ids)
        self.assertIn("stale", ids)
        self.assertGreater(r["categories"]["rules"]["score"], 0)

    def test_deterministic(self):
        a = self.run_scan("messy")
        b = self.run_scan("messy")
        self.assertEqual(a, b)

    def test_adjust_is_bounded_and_capped(self):
        base = self.run_scan("messy")["overall"]
        up = self.run_scan("messy", adjust=5)["overall"]
        self.assertEqual(up["adjust"], 1)
        steps = scan.LETTERS.index(base["base_letter"]) - scan.LETTERS.index(up["letter"])
        self.assertLessEqual(steps, 1)
        # A secret in a rules file caps the grade at C no matter what.
        self.assertIn(up["letter"], ("C", "D", "F"))

    def test_no_secret_value_in_html(self):
        r = self.run_scan("messy")
        page = scan.render_html(r, scan.load_config())
        self.assertNotIn("Zq7Zq7", page)
        self.assertNotRegex(page, r"sk-ant-[A-Za-z0-9]")

    def test_share_card_has_no_paths(self):
        r = self.run_scan("messy")
        text = re.sub(r"<[^>]+>", " ", scan.share_card_html(r["share"]))
        allowed = set(" ".join(list(scan.CATEGORY_NAMES.values()) + list(r["share"]["counts"])
                               + ["My AI setup report card", "Agent Report Card", "Not graded"]).split())
        for word in text.split():
            if re.fullmatch(r"[A-F]|[0-9,]+", word):
                continue
            self.assertIn(word, allowed)

    def test_non_project_folder_not_graded(self):
        base = self.root / "empty"
        notes = base / "notes-folder"
        notes.mkdir(exist_ok=True)
        r = scan.scan(base / "home", notes, TODAY)
        self.assertFalse(r["categories"]["structure"]["graded"])
        self.assertEqual(r["share"]["grades"]["Project setup"], "Not graded")
        r2 = scan.scan(base / "home", base / "home", TODAY)
        self.assertFalse(r2["categories"]["structure"]["graded"])

    def test_guard_hook_cuts_permission_penalty(self):
        import json, shutil
        src = self.root / "messy"
        dst = self.root / "guarded"
        if not dst.exists():
            shutil.copytree(src, dst, symlinks=True)
        sp = dst / "home" / ".claude" / "settings.json"
        data = json.loads(sp.read_text())
        data["hooks"] = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}]}
        sp.write_text(json.dumps(data))
        plain = {f["id"]: f["points"] for f in self.run_scan("messy")["findings"] if f["category"] == "safety"}
        guarded_r = scan.scan(dst / "home", dst / "project", TODAY)
        guarded = {f["id"]: f["points"] for f in guarded_r["findings"] if f["category"] == "safety"}
        self.assertLess(guarded["bypass"], plain["bypass"])
        self.assertEqual(guarded["secret-in-rules"], plain["secret-in-rules"])
        self.assertIn("guard-hook", guarded)

    def test_mini_toml_matches_needed_keys(self):
        text = (
            'sandbox_mode = "workspace-write"\napproval_policy = "on-request"\n'
            '[skills]\nmax_context_tokens = 8000\n'
            'config= [{ path = "/a/SKILL.md", enabled = false }, { name = "x:y", enabled = false }]\n'
            '[mcp_servers.one]\ncommand = "node"\n[mcp_servers.two]\nenabled = false\n'
            '[plugins."p@m"]\nenabled = true\n'
        )
        d = scan._mini_toml(text)
        self.assertEqual(d["sandbox_mode"], "workspace-write")
        self.assertEqual(d["skills"]["max_context_tokens"], 8000)
        self.assertEqual(d["skills"]["config"][0], {"path": "/a/SKILL.md", "enabled": False})
        self.assertEqual(sorted(d["mcp_servers"]), ["one", "two"])
        self.assertFalse(d["mcp_servers"]["two"]["enabled"])
        self.assertTrue(d["plugins"]["p@m"]["enabled"])

    def test_no_network_imports(self):
        src = (ROOT / "skills" / "report-card" / "scripts" / "scan.py").read_text()
        for mod in ("socket", "urllib.request", "http.client", "requests", "subprocess"):
            self.assertIsNone(re.search(rf"^\s*(import|from)\s+{re.escape(mod)}\b", src, re.M), mod)


if __name__ == "__main__":
    unittest.main()

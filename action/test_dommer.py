"""Dommerens grænse, anvendt én gang på result.json før oversigt og GitHub-review.  python3 -m unittest -v"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import dommer

HERE = Path(__file__).resolve().parent


def finding(confidence, severity="alvorlig", title="Fejl"):
    return {"id": "f1", "severity": severity, "category": "fejl", "title": title, "body": "b", "path": "a.py",
            "line": 3, "confidence": confidence}


class Normalize(unittest.TestCase):
    def test_finding_under_threshold_is_moved_and_verdict_recomputed(self):
        result = {"verdict": "request_changes", "findings": [finding(75)], "pre_merge_checks": [], "rejected": []}
        dommer.normalize(result)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["verdict"], "approve")
        self.assertIn("75 < 80", result["rejected"][0]["reason"])

    def test_error_verdict_is_never_touched(self):
        result = {"verdict": "error", "findings": [finding(10)]}
        dommer.normalize(result)
        self.assertEqual(result, {"verdict": "error", "findings": [finding(10)]})

    def test_normalize_is_idempotent(self):
        result = {"verdict": "request_changes", "findings": [finding(90), finding(60, title="Lav")], "pre_merge_checks": []}
        dommer.normalize(result)
        once = json.loads(json.dumps(result))
        dommer.normalize(result)
        self.assertEqual(result, once)
        self.assertEqual(len(result["rejected"]), 1)

    def test_cli_rewrites_file_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps({"verdict": "request_changes", "findings": [finding(79)], "pre_merge_checks": []}))
            run = subprocess.run([sys.executable, str(HERE / "dommer.py"), str(path)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            data = json.loads(path.read_text())
            self.assertEqual((data["verdict"], data["findings"]), ("approve", []))

    def test_cli_leaves_unreadable_file_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text("ikke json")
            run = subprocess.run([sys.executable, str(HERE / "dommer.py"), str(path)], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(path.read_text(), "ikke json")


if __name__ == "__main__":
    unittest.main()

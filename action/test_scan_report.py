"""Tests for scan_report.  python3 -m unittest -v"""
import json
import os
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import scan_report as sr

DIFF = """diff --git a/api/x.php b/api/x.php
--- a/api/x.php
+++ b/api/x.php
@@ -10,3 +10,4 @@
 a
-b
+c
+d
 e
diff --git a/old.txt b/old.txt
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""


class ChangedLines(unittest.TestCase):
    def test_squawk_zero_based_lines_become_one_based(self):
        finding = next(sr.parse_squawk([{"file": "x.sql", "line": 0, "level": "Warning"}]))
        self.assertEqual(finding["line"], 1)

    def test_only_added_lines_in_head_are_recorded(self):
        self.assertEqual(sr.changed_lines(DIFF), {"api/x.php": {11, 12}})


class Collect(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "w") as fh:
            json.dump(data, fh)

    def test_findings_far_from_changes_and_in_untouched_files_are_dropped(self):
        self.write("opengrep.json", {"results": [
            {"check_id": "php.injection", "path": "./api/x.php", "start": {"line": 12},
             "extra": {"severity": "ERROR", "message": "SQL injection"}},
            {"check_id": "php.old", "path": "api/x.php", "start": {"line": 90},
             "extra": {"severity": "WARNING", "message": "far away"}},
            {"check_id": "other", "path": "api/y.php", "start": {"line": 1},
             "extra": {"severity": "ERROR", "message": "untouched file"}},
        ]})
        findings, notes = sr.collect(self.dir, sr.changed_lines(DIFF))
        self.assertEqual([f["rule"] for f in findings], ["php.injection"])
        self.assertEqual(findings[0]["path"], "api/x.php")
        self.assertEqual(notes, [])

    def test_only_vulnerabilities_new_compared_to_base_are_reported(self):
        vuln = lambda vid: {"results": [{"source": {"path": "pr/package-lock.json"}, "packages": [
            {"package": {"name": "lodash", "version": "4.17.0"},
             "vulnerabilities": [{"id": vid, "summary": "proto pollution"}]}]}]}
        self.write("osv-base.json", vuln("GHSA-old"))
        head = vuln("GHSA-old")
        head["results"][0]["packages"][0]["vulnerabilities"].append({"id": "GHSA-new", "summary": "rce"})
        self.write("osv-head.json", head)
        findings, _ = sr.collect(self.dir, {})
        self.assertEqual([f["rule"] for f in findings], ["GHSA-new"])

    def test_unreadable_output_becomes_a_note_not_a_crash(self):
        with open(os.path.join(self.dir, "ruff.json"), "w") as fh:
            fh.write("not json")
        findings, notes = sr.collect(self.dir, {})
        self.assertEqual(findings, [])
        self.assertIn("ruff", notes[0])

    def test_opengrep_parse_warnings_keep_coverage_complete(self):
        self.write("opengrep.json", {"results": [], "errors": [
            {"level": "warn", "type": ["PartialParsing", []], "message": "bash snippet in yaml"}]})
        _, notes = sr.collect(self.dir, {})
        self.assertEqual(notes, [])

    def test_opengrep_real_or_unknown_errors_mark_coverage_incomplete(self):
        for error in ({"level": "error", "message": "rule failed"}, {"message": "no level"}):
            self.write("opengrep.json", {"results": [], "errors": [error]})
            _, notes = sr.collect(self.dir, {})
            self.assertFalse(sr.check_status(None, notes, "sha")["complete"], error)

    def test_wrong_json_shape_marks_coverage_incomplete(self):
        self.write("ruff.json", {"error": "not a list"})
        findings, notes = sr.collect(self.dir, {})
        self.assertEqual(findings, [])
        self.assertFalse(sr.check_status(None, notes, "sha")["complete"])

    def test_missing_osv_base_is_not_treated_as_no_prior_vulnerabilities(self):
        self.write("osv-head.json", {"results": []})
        _, notes = sr.collect(self.dir, {})
        self.assertIn("baseresultat mangler", notes[0])

    def test_test_failure_blocks_even_without_scanner_findings(self):
        Path(self.dir, "npm test.log").write_text("FEJL (exit 1)\nfailed test\n")
        status = sr.check_status(self.dir, [], "sha")
        self.assertEqual(status["failed_checks"], ["npm test"])


class NewParsers(unittest.TestCase):
    """Formaterne er målt 17/9 med opengrep 1.30.0, zizmor 1.30.1, oxlint 1.83.0 og golangci-lint 2.13.2."""

    def test_opengrep_rule_ids_lose_the_rules_directory_prefix(self):
        data = {"errors": [], "results": [{
            "check_id": "home.runner.work._temp.scanners.opengrep-rules.php.lang.security.eval-use",
            "path": "src/a.php", "start": {"line": 3},
            "extra": {"severity": "ERROR", "message": "Evaluating non-constant commands."}}]}
        self.assertEqual(list(sr.parse_opengrep(data)), [{
            "tool": "opengrep", "rule": "php.lang.security.eval-use", "severity": "error",
            "path": "src/a.php", "line": 3, "message": "Evaluating non-constant commands."}])

    def test_zizmor_uses_primary_locations_one_based_and_skips_ignored(self):
        def finding(ident, severity, kind, row, ignored=False, annotation="note"):
            return {"ident": ident, "desc": f"{ident} desc", "ignored": ignored,
                    "determinations": {"severity": severity, "confidence": "High"},
                    "locations": [{"symbolic": {"key": {"Local": {"verbatim_path": ".github/workflows/ci.yml"}},
                                                "annotation": annotation, "kind": kind},
                                   "concrete": {"location": {"start_point": {"row": row, "column": 0}}}}]}
        data = [finding("template-injection", "High", "Primary", 9, annotation="may expand into attacker-controllable code"),
                finding("template-injection", "High", "Related", 9),
                finding("unpinned-uses", "High", "Hidden", 6),
                finding("artipacked", "Medium", "Primary", 6, ignored=True)]
        self.assertEqual(list(sr.parse_zizmor(data)), [{
            "tool": "zizmor", "rule": "template-injection", "severity": "high", "path": ".github/workflows/ci.yml",
            "line": 10, "message": "template-injection desc: may expand into attacker-controllable code"}])

    def test_oxlint_parse_errors_and_rule_codes(self):
        data = {"diagnostics": [
            {"message": "Checking equality with NaN will always return false", "code": "eslint(use-isnan)",
             "severity": "error", "help": "Use the `isNaN` function.", "filename": "src/a.ts",
             "labels": [{"span": {"offset": 21, "length": 3, "line": 2, "column": 10}}]},
            {"message": "Expected `,` or `)` but found `:`", "severity": "error", "filename": "src/b.js",
             "labels": [{"label": "expected", "span": {"offset": 66, "length": 1, "line": 3, "column": 20}}]},
            {"message": "Suspicious", "code": "eslint(no-useless-concat)", "severity": "warning",
             "filename": "src/c.js", "labels": []}]}
        self.assertEqual(list(sr.parse_oxlint(data)), [
            {"tool": "oxlint", "rule": "eslint(use-isnan)", "severity": "error", "path": "src/a.ts", "line": 2,
             "message": "Checking equality with NaN will always return false — Use the `isNaN` function."},
            {"tool": "oxlint", "rule": "parse-error", "severity": "error", "path": "src/b.js", "line": 3,
             "message": "Expected `,` or `)` but found `:`"},
            {"tool": "oxlint", "rule": "eslint(no-useless-concat)", "severity": "warning", "path": "src/c.js",
             "line": None, "message": "Suspicious"}])

    def test_golangci_findings_and_typecheck_is_left_out(self):
        data = {"Issues": [
            {"FromLinter": "govet", "Text": "printf: fmt.Printf format %d has arg \"tekst\" of wrong type string",
             "Severity": "", "Pos": {"Filename": "main.go", "Line": 10, "Column": 2}},
            {"FromLinter": "typecheck", "Text": ": # example.com/x\n./main.go:12:6: declared and not used: y",
             "Severity": "", "Pos": {"Filename": "main.go", "Line": 1, "Column": 0}}], "Report": {}}
        self.assertEqual(list(sr.parse_golangci(data)), [
            {"tool": "golangci-lint", "rule": "govet", "severity": "warning", "path": "main.go", "line": 10,
             "message": "printf: fmt.Printf format %d has arg \"tekst\" of wrong type string"}])

    def test_golangci_typecheck_becomes_a_note_about_code_that_does_not_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "golangci.json"), "w") as fh:
                json.dump({"Issues": [{"FromLinter": "typecheck", "Text": "x", "Pos": {"Filename": "main.go", "Line": 1}}]}, fh)
            findings, notes = sr.collect(tmp, {"main.go": {1}})
        self.assertEqual(findings, [])
        self.assertEqual(notes, ["golangci-lint: Go-koden kompilerer ikke, så Go-tjekkene er ufuldstændige"])

    def test_report_names_the_detected_languages(self):
        text = sr.render([], [], None, languages="PHP, TypeScript")
        self.assertIn("Sprog i PR'en: PHP, TypeScript", text)
        self.assertIn("Sprog i PR'en: ingen genkendte", sr.render([], [], None, languages=""))
        self.assertNotIn("Sprog i PR'en", sr.render([], [], None))


class RuleCounts(unittest.TestCase):
    def test_counts_per_tool_rule_and_severity_most_first(self):
        f = lambda tool, rule, sev, line: {"tool": tool, "rule": rule, "severity": sev, "path": "a.py", "line": line, "message": "m"}
        rows = sr.rule_counts([f("ruff", "S608", "error", 1), f("ruff", "S608", "error", 2), f("opengrep", "x.y", "warning", 3),
                               f("ruff", "S608", "warning", 4)])
        self.assertEqual(rows, [
            {"tool": "ruff", "rule": "S608", "severity": "error", "count": 2},
            {"tool": "opengrep", "rule": "x.y", "severity": "warning", "count": 1},
            {"tool": "ruff", "rule": "S608", "severity": "warning", "count": 1},
        ])

    def test_counts_are_capped_and_long_names_cut_to_the_broker_limits(self):
        many = [{"tool": "t" * 80, "rule": f"R{i}" + "r" * 200, "severity": "error", "path": "a", "line": i} for i in range(150)]
        rows = sr.rule_counts(many)
        self.assertEqual(len(rows), sr.MAX_COUNT_ROWS)
        self.assertEqual((len(rows[0]["tool"]), max(len(r["rule"]) for r in rows)), (64, 128))


class ScannerProcess(unittest.TestCase):
    def test_crashed_gitleaks_is_never_reported_as_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, bins = root / "repo", root / "bin"
            repo.mkdir(); bins.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
            git("init", "-q")
            (repo / "file.txt").write_text("before\n")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "file.txt").write_text("after\n")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qam", "head")
            for name, script in {
                "gitleaks": "exit 2",
                "opengrep": 'while [ "$1" != "-o" ]; do shift; done; shift; printf \'{"results":[]}\' > "$1"',
                "trivy": 'while [ "$1" != "--output" ]; do shift; done; shift; printf \'{"Results":[]}\' > "$1"',
            }.items():
                p = bins / name
                p.write_text("#!/bin/bash\n" + script + "\n")
                p.chmod(0o700)
            (bins / "python3").symlink_to(sys.executable)
            (bins / "opengrep-rules/generic/secrets").mkdir(parents=True)
            report = root / "tools.md"
            run = subprocess.run(["bash", str(Path(__file__).with_name("scanners.sh")),
                                  str(repo), base, str(bins), str(report)],
                                 env=dict(os.environ, PATH="/usr/bin:/bin", MANILENS_SKIP_REPO_CHECKS="1"),
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            status = json.loads(Path(str(report) + ".status.json").read_text())
            self.assertEqual(status["failed_checks"], ["gitleaks"])
            self.assertNotIn("gitleaks — OK", report.read_text())
            # A scanner can emit valid partial JSON and still fail.
            opengrep = bins / "opengrep"
            opengrep.write_text(opengrep.read_text() + "exit 2\n")
            run = subprocess.run(["bash", str(Path(__file__).with_name("scanners.sh")),
                                  str(repo), base, str(bins), str(report)],
                                 env=dict(os.environ, PATH="/usr/bin:/bin", MANILENS_SKIP_REPO_CHECKS="1"),
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            status = json.loads(Path(str(report) + ".status.json").read_text())
            self.assertFalse(status["complete"])
            self.assertTrue(any("opengrep" in note for note in status["notes"]))



class ChangedLinesPaths(unittest.TestCase):
    def test_paths_with_spaces_and_quoted_unicode_keep_their_changed_lines(self):
        # git sætter en tabulator efter stier med mellemrum og citerer stier med æ/ø/å (core.quotePath), målt 17/9.
        diff = ('diff --git a/dir with space/b.py b/dir with space/b.py\n--- a/dir with space/b.py\t\n'
                '+++ b/dir with space/b.py\t\n@@ -2 +2 @@\n-    return 1\n+    return 2\n'
                'diff --git "a/m\\303\\246ppe/a fil.py" "b/m\\303\\246ppe/a fil.py"\n--- "a/m\\303\\246ppe/a fil.py"\t\n'
                '+++ "b/m\\303\\246ppe/a fil.py"\t\n@@ -2 +2 @@\n-    return 1\n+    return 2\n')
        self.assertEqual(sr.changed_lines(diff), {"dir with space/b.py": {2}, "mæppe/a fil.py": {2}})


if __name__ == "__main__":
    unittest.main()

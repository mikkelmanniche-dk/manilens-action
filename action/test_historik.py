"""historik: commits bag de linjer, PR'en ændrer.  python3 -m unittest -v"""
import subprocess
import tempfile
import unittest
from pathlib import Path

import historik as h


def git(repo, *args, **env):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def commit(repo, message, files):
    for name, text in files.items():
        Path(repo, name).parent.mkdir(parents=True, exist_ok=True)
        Path(repo, name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        commit(self.repo, "feat: første udgave", {"app.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n"})
        self.fix = commit(self.repo, "fix: b gav forkert tal `bug`", {"app.py": "def a():\n    return 1\n\n\ndef b():\n    return 3\n"})
        self.base = self.fix
        commit(self.repo, "feat: ændrer b igen", {"app.py": "def a():\n    return 1\n\n\ndef b():\n    return 4\n"})

    def build(self, **kwargs):
        return h.render(h.build(str(self.repo), self.base, **kwargs))


class Build(Repo):
    def test_the_commit_behind_the_changed_line_is_shown_with_its_subject(self):
        text = self.build()
        self.assertIn("app.py", text)
        self.assertIn(self.fix[:7], text)
        self.assertIn("fix: b gav forkert tal", text)

    def test_the_prs_own_commits_are_listed(self):
        self.assertIn("feat: ændrer b igen", self.build())

    def test_no_author_names_or_emails(self):
        text = self.build()
        self.assertNotIn("example.invalid", text)
        self.assertNotIn("user.name", text)

    def test_subjects_are_cleaned_of_backticks(self):
        text = self.build()
        self.assertNotIn("`bug`", text)
        self.assertIn("'bug'", text)

    def test_added_file_has_no_blame_but_is_named(self):
        commit(self.repo, "feat: ny fil", {"ny.py": "x = 1\n"})
        text = self.build()
        self.assertIn("ny.py", text)
        self.assertIn("Ny i denne PR", text)

    def test_deleted_lines_count_too(self):
        commit(self.repo, "refactor: fjerner a", {"app.py": "def b():\n    return 4\n"})
        self.assertIn(self.fix[:7], self.build())

    def test_unchanged_file_is_not_included(self):
        commit(self.repo, "docs: rører kun ny fil", {"andet.py": "y = 1\n"})
        text = self.build()
        self.assertIn("andet.py", text)
        self.assertNotIn("| app.py", text)


    def test_a_deleted_file_keeps_its_history(self):
        git(self.repo, "rm", "-q", "app.py")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "refactor: fjerner app.py")
        text = self.build()
        self.assertIn("## app.py", text)
        self.assertIn(self.fix[:7], text)

    def test_markdown_links_in_subjects_are_defused(self):
        commit(self.repo, "fix: se [her](https://ond.example/steal)", {"app.py": "def b():\n    return 9\n"})
        text = self.build()
        self.assertNotIn("](https://ond.example", text)
        self.assertIn("ond.example", text)

class Limits(Repo):
    def test_a_slow_repo_stops_at_the_deadline(self):
        import time as t
        original = h.git

        def slow(repo, *args):
            if args and args[0] == "blame":
                t.sleep(0.05)
            return original(repo, *args)

        h.git = slow
        try:
            data = h.build(str(self.repo), self.base, deadline=t.monotonic() - 1)
        finally:
            h.git = original
        self.assertTrue(data["stoppet"])
        self.assertIn("tiden l\u00f8b ud", h.render(data))

    def test_output_is_capped(self):
        files = {f"f{i}.py": f"x = {i}\n" for i in range(40)}
        commit(self.repo, "feat: mange filer", files)
        text = self.build()
        self.assertLessEqual(len(text), h.MAX_CHARS)
        self.assertIn("flere filer", text)

    def test_binary_and_lockfiles_are_skipped(self):
        commit(self.repo, "chore: lock", {"package-lock.json": '{"a": 1}\n', "logo.png": "binært\n"})
        text = self.build()
        self.assertNotIn("package-lock.json", text)
        self.assertNotIn("logo.png", text)


class Robust(Repo):
    def test_a_git_failure_is_a_note_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "historik.md")
            self.assertEqual(h.main(["--repo", str(self.repo), "--base", "0" * 40, "--out", str(out)]), 0)
            self.assertIn("Ingen historik", out.read_text())

    def test_main_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "historik.md")
            self.assertEqual(h.main(["--repo", str(self.repo), "--base", self.base, "--out", str(out)]), 0)
            self.assertIn("fix: b gav forkert tal", out.read_text())

    def test_git_is_called_without_external_diff_or_textconv(self):
        calls = []
        original = h.git

        def spy(repo, *args, **kwargs):
            calls.append(args)
            return original(repo, *args, **kwargs)

        h.git = spy
        try:
            self.build()
        finally:
            h.git = original
        self.assertTrue(all("--no-ext-diff" in a or "blame" in a[0] or "log" in a[0] or "rev-parse" in a[0] for a in calls))
        blame = [a for a in calls if a[0] == "blame"]
        self.assertTrue(blame and all("--no-textconv" in a for a in blame))


class Hunks(unittest.TestCase):
    def test_old_ranges_are_read_from_the_diff(self):
        diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -10,3 +10,2 @@\n@@ -40,0 +39,2 @@\n"
                "diff --git a/y.py b/y.py\n--- /dev/null\n+++ b/y.py\n@@ -0,0 +1,2 @@\n")
        self.assertEqual(h.hunks(diff), {"x.py": [(10, 3), (40, 0)], "y.py": []})

    def test_insertion_at_the_top_of_an_existing_file_is_not_treated_as_new(self):
        # Maalt 18/9: git skriver "@@ -0,0 +1 @@" baade for en ny fil og for en indsaettelse oeverst i en gammel fil.
        gammel = "diff --git a/g.txt b/g.txt\n--- a/g.txt\n+++ b/g.txt\n@@ -0,0 +1 @@\n+NY0\n"
        ny = "diff --git a/n.txt b/n.txt\n--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1 @@\n+x\n"
        self.assertEqual(h.hunks(gammel), {"g.txt": [(1, 1)]})
        self.assertEqual(h.hunks(ny), {"n.txt": []})

    def test_a_deleted_file_is_keyed_on_its_old_path(self):
        slettet = "diff --git a/slet.txt b/slet.txt\n--- a/slet.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
        self.assertEqual(h.hunks(slettet), {"slet.txt": [(1, 2)]})

    def test_quoted_paths_with_special_characters(self):
        diff = 'diff --git "a/ny mappe/\\303\\246.py" "b/ny mappe/\\303\\246.py"\n--- "a/ny mappe/\\303\\246.py"\n+++ "b/ny mappe/\\303\\246.py"\n@@ -1,2 +1,2 @@\n'
        self.assertEqual(h.hunks(diff), {"ny mappe/æ.py": [(1, 2)]})


if __name__ == "__main__":
    unittest.main()

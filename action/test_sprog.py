"""Tests for sprog.  python3 -m unittest -v"""
import tempfile
import unittest
from pathlib import Path

import sprog


class Detect(unittest.TestCase):
    def test_languages_come_from_changed_file_types(self):
        found = sprog.detect(["api/x.php", "web/src/App.tsx", "tools/run.sh", "README.md"])
        self.assertEqual(found, ["php", "shell", "typescript"])

    def test_workflows_and_dockerfiles_are_recognised_by_path(self):
        found = sprog.detect([".github/workflows/ci.yml", "docker/Dockerfile.prod", "app/api.dockerfile",
                              "config/app.yml"])
        self.assertEqual(found, ["dockerfile", "github-actions"])

    def test_only_files_directly_in_the_workflows_dir_are_workflows(self):
        # GitHub kører kun .github/workflows/*.yml, ikke filer i undermapper.
        self.assertEqual(sprog.detect([".github/workflows/sub/shared.yml"]), [])

    def test_a_known_code_extension_wins_over_a_dockerfile_name(self):
        self.assertEqual(sprog.detect(["dockerfile.php", "Dockerfile-helper.py", "Dockerfile.prod"]),
                         ["dockerfile", "php", "python"])

    def test_only_files_without_a_known_language_give_an_empty_list(self):
        self.assertEqual(sprog.detect(["README.md", "assets/logo.png"]), [])

    def test_extension_matching_ignores_case_and_needs_a_real_extension(self):
        self.assertEqual(sprog.detect(["LEGACY.PHP", "notes.phpx", "go"]), ["php"])


class RuleDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["php", "javascript", "typescript", "python", "bash", "go", "dockerfile",
                  "yaml/github-actions", "generic/secrets"]:
            (self.root / d).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_typescript_also_gets_the_javascript_rules_and_secrets_are_always_included(self):
        dirs = sprog.rule_dirs(["typescript"], self.root)
        self.assertEqual(dirs, [str(self.root / "generic/secrets"), str(self.root / "javascript"),
                                str(self.root / "typescript")])

    def test_rule_dirs_are_unique_and_missing_dirs_are_skipped(self):
        dirs = sprog.rule_dirs(["javascript", "typescript", "ruby"], self.root)
        self.assertEqual([Path(d).relative_to(self.root).as_posix() for d in dirs],
                         ["generic/secrets", "javascript", "typescript"])

    def test_java_rules_are_never_selected(self):
        # java/ holds the one rule in opengrep-rules marked as proprietary.
        (self.root / "java").mkdir()
        self.assertNotIn(str(self.root / "java"), sprog.rule_dirs(sorted(sprog.EXTENSIONS.values()), self.root))

    def test_no_changed_files_selects_no_rules(self):
        self.assertEqual(sprog.rule_dirs([], self.root, any_changes=False), [])


class Cli(unittest.TestCase):
    def test_writes_rule_dirs_and_prints_human_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rules/php").mkdir(parents=True)
            (root / "rules/generic/secrets").mkdir(parents=True)
            (root / "files.txt").write_text("a.php\nb.md\n")
            out = root / "configs.txt"
            summary = sprog.main(["--files", str(root / "files.txt"), "--rules", str(root / "rules"),
                                  "--configs", str(out)])
            self.assertEqual(summary, "PHP")
            self.assertEqual(out.read_text().splitlines(),
                             [str(root / "rules/generic/secrets"), str(root / "rules/php")])

    def test_missing_rules_dir_gives_no_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "files.txt").write_text("a.py\n")
            out = root / "configs.txt"
            summary = sprog.main(["--files", str(root / "files.txt"), "--rules", str(root / "mangler"),
                                  "--configs", str(out)])
            self.assertEqual(summary, "Python")
            self.assertEqual(out.read_text(), "")


if __name__ == "__main__":
    unittest.main()

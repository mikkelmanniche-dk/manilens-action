"""Tests for filter_diff.  python3 -m unittest -v"""
import unittest

import filter_diff as fd


def chunk(path, body="+x = 1\n"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


class FilterDiff(unittest.TestCase):
    def test_build_output_and_lockfiles_are_dropped_but_listed(self):
        diff = chunk("src/a.ts") + chunk("app/static/dist/assets/index-abc.js") + \
            chunk("package-lock.json") + chunk("out/_next/static/chunks/x.js")
        out, skipped = fd.filter_diff(diff)
        self.assertIn("diff --git a/src/a.ts", out)
        self.assertNotIn("index-abc.js b/", out)
        self.assertEqual({p for p, _ in skipped},
                         {"app/static/dist/assets/index-abc.js", "package-lock.json",
                          "out/_next/static/chunks/x.js"})
        self.assertIn("#   package-lock.json", out)

    def test_minified_and_huge_files_are_dropped(self):
        mini = chunk("lib/vendor.js", "+" + "a" * 2000 + "\n" * 1 + "".join(f"+{'b' * 900}\n" for _ in range(6)))
        huge = chunk("data/big.json", "+" + "x" * 300_000 + "\n")
        out, skipped = fd.filter_diff(mini + huge + chunk("src/ok.py"))
        self.assertEqual([p for p, _ in skipped], ["lib/vendor.js", "data/big.json"])
        self.assertIn("src/ok.py", out)

    def test_extra_patterns_from_repo_apply(self):
        out, skipped = fd.filter_diff(chunk("docs/gen/api.md") + chunk("src/a.ts"), extra=["docs/gen/**"])
        self.assertEqual([p for p, _ in skipped], ["docs/gen/api.md"])

    def test_empty_diff_stays_empty(self):
        self.assertEqual(fd.filter_diff(""), ("", []))


if __name__ == "__main__":
    unittest.main()

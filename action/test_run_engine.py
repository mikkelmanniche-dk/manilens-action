"""run_engine.sh med en falsk claude: forvalg uden model, modelfordeling og advarsler i resultatet.

  python3 -m unittest -v test_run_engine
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import triage
from post_review import validate

HERE = Path(__file__).resolve().parent

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty", "apiKeySource": "none"}))
    sys.exit(0)
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
result = {"summary": {}, "walkthrough": [], "findings": [], "previous": [], "rejected": [],
          "pre_merge_checks": [], "verdict": "approve"}
print(json.dumps({"is_error": False, "result": json.dumps(result)}))
"""


def chunk(path, body):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,3 +1,3 @@\n{body}"


class RunEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "src").mkdir()
        self.claude = self.tmp / "claude"
        self.claude.write_text(FAKE_CLAUDE)
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IEXEC)
        self.calls = self.tmp / "calls.jsonl"
        (self.tmp / "meta.json").write_text('{"repo": "a/b", "number": 1, "title": "t", "body": ""}')

    def run_engine(self, diff, previous=None, tier="sparsom", graph=None, context=None, history=None, language=None):
        (self.tmp / "diff.patch").write_text(diff)
        args = ["bash", str(HERE / "run_engine.sh"), "--src", str(self.tmp / "src"), "--diff", str(self.tmp / "diff.patch"),
                "--meta", str(self.tmp / "meta.json"), "--out", str(self.tmp / "result.json"), "--log", str(self.tmp / "log")]
        if previous is not None:
            (self.tmp / "previous.json").write_text(json.dumps(previous))
            args += ["--previous", str(self.tmp / "previous.json")]
        if graph is not None:
            (self.tmp / "graf").mkdir(exist_ok=True)
            (self.tmp / "graf" / "graph.md").write_text(graph)
            args += ["--graph", str(self.tmp / "graf" / "graph.md")]
        if context is not None:
            (self.tmp / "kontekst").mkdir(exist_ok=True)
            (self.tmp / "kontekst" / "kontekst.md").write_text(context)
            args += ["--context", str(self.tmp / "kontekst" / "kontekst.md")]
        if history is not None:
            (self.tmp / "hist").mkdir(exist_ok=True)
            (self.tmp / "hist" / "historik.md").write_text(history)
            args += ["--history", str(self.tmp / "hist" / "historik.md")]
        env = {**os.environ, "MANILENS_CLAUDE": str(self.claude), "FAKE_CALLS": str(self.calls),
               "CLAUDE_CODE_OAUTH_TOKEN": "test", "MANILENS_TIER": tier}
        env.pop("MANILENS_LANGUAGE", None)
        if language is not None:
            env["MANILENS_LANGUAGE"] = language
        env.pop("CI", None)
        proc = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr + (self.tmp / "log").read_text(errors="replace"))
        result = json.loads((self.tmp / "result.json").read_text())
        validate(result)
        return result

    def prompts(self):
        if not self.calls.exists():
            return []
        return [json.loads(line)[json.loads(line).index("-p") + 1] for line in self.calls.read_text().splitlines()]

    def test_docs_only_pr_is_approved_without_calling_claude(self):
        result = self.run_engine(chunk("README.md", "+Ny sætning.\n"))

        self.assertEqual(self.prompts(), [])
        self.assertEqual(result["verdict"], "approve")
        self.assertIn(triage.SKIP_CHECK, [c["name"] for c in result["pre_merge_checks"]])

    def test_open_previous_findings_always_get_a_model_review(self):
        self.run_engine(chunk("README.md", "+Ny sætning.\n"), previous=[{"fp": "abc123abc123", "path": "a.py"}])

        self.assertEqual(len(self.prompts()), 1)

    def test_code_is_reviewed_and_warnings_are_added_without_the_model(self):
        code = "".join(f"+export const v{i} = {i}\n" for i in range(12))
        result = self.run_engine(chunk("src/pris.ts", code))

        self.assertEqual(len(self.prompts()), 1)
        self.assertIn(triage.TESTS_CHECK, [c["name"] for c in result["pre_merge_checks"]])

    def test_sparse_tier_really_sends_sonnet_to_the_second_pass_and_security(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), tier="sparsom")
        [prompt] = self.prompts()

        self.assertNotIn("{{", prompt)
        self.assertIn("reverse", prompt)
        self.assertIn("`engine/agents/sikkerhed.md` — model `sonnet`", prompt)
        self.assertIn("`engine/agents/fejl.md` — model `opus`", prompt)

    def test_full_tier_uses_opus_for_security(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), tier="fuld")
        [prompt] = self.prompts()

        self.assertIn("`engine/agents/sikkerhed.md` — model `opus`", prompt)


    def test_the_prompt_says_danish_by_default(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"))
        [prompt] = self.prompts()

        self.assertIn("in **Danish**", prompt)
        self.assertNotIn("{{LANGUAGE}}", prompt)

    def test_the_prompt_switches_to_english(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), language="en")
        [prompt] = self.prompts()

        self.assertIn("in **English**", prompt)
        self.assertNotIn("Danish", prompt)

    def test_an_unknown_language_falls_back_to_danish_in_the_prompt(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), language="de")
        [prompt] = self.prompts()

        self.assertIn("in **Danish**", prompt)

    def test_the_reviewers_are_told_the_language(self):
        # Agentfilerne siger kun "the review language" og kan ikke se erstatningen selv;
        # står sproget ikke i fan out-afsnittet, gætter modellen (målt: instruktionen manglede).
        self.run_engine(chunk("src/a.py", "+x = 1\n"), language="en")
        [prompt] = self.prompts()
        fan_out = prompt.split("**Fan out.**")[1].split("**Merge.**")[0]

        self.assertIn("review language is English", fan_out)

    def test_the_verifier_is_told_the_language(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), language="en")
        [prompt] = self.prompts()
        verify = prompt.split("**Verify.**")[1].split("**Judge")[0] if "**Judge" in prompt else prompt.split("**Verify.**")[1]

        self.assertIn("review language `English`", verify)

    def test_code_graph_path_reaches_the_prompt_and_the_readable_dirs(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), graph="# Kodegraf\n")
        [call] = [json.loads(line) for line in self.calls.read_text().splitlines()]
        prompt = call[call.index("-p") + 1]
        self.assertIn(str(self.tmp / "graf" / "graph.md"), prompt)
        self.assertIn(str(self.tmp / "graf"), [call[i + 1] for i, a in enumerate(call) if a == "--add-dir"])

    def test_context_path_reaches_the_prompt_and_the_readable_dirs(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), context="# Kontekst\n")
        [call] = [json.loads(line) for line in self.calls.read_text().splitlines()]
        prompt = call[call.index("-p") + 1]
        self.assertIn(str(self.tmp / "kontekst" / "kontekst.md"), prompt)
        self.assertIn(str(self.tmp / "kontekst"), [call[i + 1] for i, a in enumerate(call) if a == "--add-dir"])

    def test_history_path_reaches_the_prompt_and_the_readable_dirs(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"), history="# Historik\n")
        [call] = [json.loads(line) for line in self.calls.read_text().splitlines()]
        prompt = call[call.index("-p") + 1]
        self.assertIn(str(self.tmp / "hist" / "historik.md"), prompt)
        self.assertIn(str(self.tmp / "hist"), [call[i + 1] for i, a in enumerate(call) if a == "--add-dir"])

    def test_without_history_the_prompt_says_so(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"))
        [prompt] = self.prompts()
        self.assertIn("(ingen historik)", prompt)
        self.assertNotIn("{{HISTORY}}", prompt)

    def test_without_context_the_prompt_says_so(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"))
        [prompt] = self.prompts()
        self.assertIn("(ingen issues eller CI-kontekst)", prompt)
        self.assertNotIn("{{CONTEXT}}", prompt)

    def test_without_a_graph_the_prompt_says_so(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"))
        [prompt] = self.prompts()
        self.assertIn("(ingen kodegraf)", prompt)
        self.assertNotIn("{{GRAPH}}", prompt)

    def test_prompt_skips_the_verifier_when_there_are_no_candidates(self):
        self.run_engine(chunk("src/a.py", "+x = 1\n"))
        [prompt] = self.prompts()

        self.assertIn("no candidate findings", prompt)


if __name__ == "__main__":
    unittest.main()

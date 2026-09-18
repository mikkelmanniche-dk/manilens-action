"""Tests for de rene funktioner i post_review og render.  python3 -m unittest -v"""
import unittest
import json
import tempfile
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import post_review as pr
import render

PATCH = """@@ -10,4 +10,5 @@ function a() {
 const x = 1;
-const y = 2;
+const y = 3;
+const z = 4;
 return x;
@@ -40,2 +41,2 @@
-old
+new
 tail"""


class CommentableLines(unittest.TestCase):
    def test_includes_added_and_context_lines_on_the_right_side(self):
        lines = pr.commentable_lines([{"filename": "a.ts", "patch": PATCH}])["a.ts"]
        self.assertEqual(lines, {10, 11, 12, 13, 41, 42})

    def test_no_newline_marker_does_not_count_as_a_line(self):
        patch = "@@ -1,2 +1,2 @@\n a\n-b\n+c\n\\ No newline at end of file"
        self.assertEqual(pr.commentable_lines([{"filename": "x", "patch": patch}])["x"], {1, 2})

    def test_file_without_patch_has_no_lines(self):
        self.assertEqual(pr.commentable_lines([{"filename": "img.png"}])["img.png"], set())


class Fingerprint(unittest.TestCase):
    def test_same_path_and_title_ignoring_case_and_punctuation_match(self):
        a = pr.fingerprint({"path": "a.ts", "title": "SQL-injection i søgning!"})
        b = pr.fingerprint({"path": "a.ts", "title": "sql injection i søgning"})
        self.assertEqual(a, b)

    def test_different_path_gives_different_fingerprint(self):
        a = pr.fingerprint({"path": "a.ts", "title": "x"})
        self.assertNotEqual(a, pr.fingerprint({"path": "b.ts", "title": "x"}))


class Validate(unittest.TestCase):
    def test_scanner_gate_checks_commit_completion_and_test_failures(self):
        with tempfile.NamedTemporaryFile(mode="w+") as fh:
            base = {"head_sha": "sha", "complete": True, "notes": [], "failed_checks": []}
            for updates, blocked in [({}, False), ({"head_sha": "old"}, True),
                                     ({"complete": False}, True), ({"failed_checks": ["test"]}, True)]:
                fh.seek(0); fh.truncate(); json.dump(dict(base, **updates), fh); fh.flush()
                self.assertEqual(pr.scanner_gate(fh.name, "sha") is not None, blocked)
        self.assertIsNotNone(pr.scanner_gate(None, "sha"))

    def test_verdict_without_findings_cannot_approve(self):
        with self.assertRaises(ValueError):
            pr.validate({"verdict": "approve"})

    def test_explicit_rejection_blocks_even_without_severe_findings(self):
        self.assertTrue(pr.is_blocked({"verdict": "request_changes", "findings": []}))

    def test_bad_check_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            pr.validate({"verdict": "approve", "findings": [], "pre_merge_checks": "passed"})

    def test_rejects_missing_verdict(self):
        with self.assertRaises(ValueError):
            pr.validate({"findings": []})

    def test_rejects_unknown_severity(self):
        with self.assertRaises(ValueError):
            pr.validate({"verdict": "approve", "findings": [{"path": "a", "severity": "høj"}]})

    def test_accepts_valid_result(self):
        pr.validate({"verdict": "approve", "findings": [{"path": "a", "severity": "mindre"}]})


class ConfidenceGate(unittest.TestCase):
    def test_finding_below_min_confidence_moves_to_rejected(self):
        result = {"verdict": "request_changes", "findings": [
            {"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl", "confidence": 75}]}
        pr.filter_confidence(result)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("75", result["rejected"][0]["reason"])
        self.assertIn(str(pr.MIN_CONFIDENCE), result["rejected"][0]["reason"])

    def test_finding_at_exactly_min_confidence_is_kept(self):
        result = {"verdict": "request_changes", "findings": [
            {"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl", "confidence": 80}]}
        pr.filter_confidence(result)
        self.assertEqual(len(result["findings"]), 1)
        self.assertNotIn("rejected", result)

    def test_finding_without_confidence_is_rejected(self):
        result = {"verdict": "request_changes", "findings": [
            {"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl"}]}
        pr.filter_confidence(result)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["rejected"]), 1)

    def test_non_numeric_confidence_is_rejected(self):
        result = {"verdict": "request_changes", "findings": [
            {"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl", "confidence": "høj"}]}
        pr.filter_confidence(result)
        self.assertEqual(result["findings"], [])

    def test_existing_rejected_entries_are_kept_alongside_new_ones(self):
        result = {"verdict": "approve", "findings": [
            {"path": "a.py", "line": 1, "severity": "mindre", "title": "Fejl", "confidence": 50}],
            "rejected": [{"path": "b.py", "line": 2, "title": "Andet", "reason": "duplikat"}]}
        pr.filter_confidence(result)
        self.assertEqual(len(result["rejected"]), 2)


class RecomputeVerdict(unittest.TestCase):
    def test_verdict_flips_to_approve_when_only_blocker_was_below_confidence(self):
        result = {"verdict": "request_changes", "findings": [], "pre_merge_checks": []}
        self.assertEqual(pr.recompute_verdict(result), "approve")

    def test_verdict_stays_request_changes_for_confirmed_blocking_finding(self):
        result = {"verdict": "approve", "findings": [
            {"path": "a.py", "severity": "kritisk", "title": "x", "confidence": 95}], "pre_merge_checks": []}
        self.assertEqual(pr.recompute_verdict(result), "request_changes")

    def test_verdict_stays_request_changes_for_failed_error_check(self):
        result = {"verdict": "approve", "findings": [], "pre_merge_checks": [
            {"name": "Tests", "mode": "error", "status": "fail"}]}
        self.assertEqual(pr.recompute_verdict(result), "request_changes")

    def test_minor_findings_alone_never_block(self):
        result = {"verdict": "request_changes", "findings": [
            {"path": "a.py", "severity": "mindre", "title": "x", "confidence": 95}], "pre_merge_checks": []}
        self.assertEqual(pr.recompute_verdict(result), "approve")


class BuildReview(unittest.TestCase):
    def test_resolved_comment_does_not_hide_a_reintroduced_bug(self):
        old = {"id": 1, "user": {"login": "manilens[bot]"},
               "body": "<!-- manilens:fp=abcdefabcdef -->"}
        with patch.object(pr.gh, "paginate", return_value=[old]), \
             patch.object(pr, "review_threads", return_value=[("thread", True, 1)]):
            self.assertEqual(pr.open_bot_comments("o/r", 1, "manilens[bot]"), {})

    def test_walkthrough_never_edits_someone_elses_marker(self):
        other = {"id": 42, "user": {"login": "someone"}, "body": render.WALKTHROUGH_MARK}
        with patch.object(pr.gh, "paginate", return_value=[other]), patch.object(pr.gh, "request") as api:
            pr.upsert_walkthrough("o/r", 1, "new")
        api.assert_called_once_with("POST", "/repos/o/r/issues/1/comments", {"body": "new"})

    def test_stale_commit_is_rejected_before_posting(self):
        with patch.object(pr.gh, "request", return_value={"state": "open", "head": {"sha": "new"}}):
            with self.assertRaises(ValueError):
                pr.require_current_head("o/r", 1, "old")

    def test_finding_outside_diff_goes_to_review_body_and_known_fp_is_skipped(self):
        known = {"path": "a.ts", "line": 11, "severity": "alvorlig", "title": "Kendt"}
        result = {"findings": [
            {"path": "a.ts", "line": 12, "severity": "alvorlig", "title": "Ny"},
            {"path": "a.ts", "line": 99, "severity": "mindre", "title": "Udenfor"},
            known,
        ]}
        allowed = pr.commentable_lines([{"filename": "a.ts", "patch": PATCH}])
        inline, outside = pr.build_review(result, {pr.fingerprint(known): {"id": 1}}, allowed)
        self.assertEqual([c["line"] for c in inline], [12])
        self.assertEqual([f["title"] for f in outside], ["Udenfor"])


BOT = "manilens[bot]"


def root(cid, fp="abcdefabcdef"):
    return {"id": cid, "user": {"login": BOT, "type": "Bot"}, "body": f"**Fund** <!-- manilens:fp={fp} -->", "path": "a.py", "line": 3}


def reply(cid, to, login=BOT, body=None):
    return {"id": cid, "in_reply_to_id": to, "user": {"login": login, "type": "Bot" if login == BOT else "User"},
            "body": body if body is not None else f"✅ Rettet i commit abc1234.\n\n{pr.CLOSED_MARK}"}


class ClosedThreads(unittest.TestCase):
    """Mulighed A (17/9): ManiLens løser ikke tråde (kræver contents: write). Et fund lukkes med botsvarets markør."""

    def open_fps(self, comments, threads):
        with patch.object(pr.gh, "paginate", return_value=comments), patch.object(pr, "review_threads", return_value=threads):
            return set(pr.open_bot_comments("o/r", 1, BOT))

    def test_bot_reply_with_closed_mark_closes_the_finding(self):
        self.assertEqual(self.open_fps([root(1), reply(2, 1)], [("t", False, 1)]), set())

    def test_closed_mark_from_someone_else_never_closes_a_finding(self):
        self.assertEqual(self.open_fps([root(1), reply(2, 1, login="kollega")], [("t", False, 1)]), {"abcdefabcdef"})

    def test_closed_mark_in_another_thread_does_not_close_this_finding(self):
        comments = [root(1), root(5, fp="123456123456"), reply(2, 5)]
        self.assertEqual(self.open_fps(comments, [("t", False, 1), ("u", False, 5)]), {"abcdefabcdef"})

    def test_closed_mark_needs_a_bot_account_not_just_the_login(self):
        spoof = reply(2, 1)
        spoof["user"]["type"] = "User"
        self.assertEqual(self.open_fps([root(1), spoof], [("t", False, 1)]), {"abcdefabcdef"})

    def test_bot_reply_without_mark_keeps_the_finding_open(self):
        self.assertEqual(self.open_fps([root(1), reply(2, 1, body="Svar uden markør")], [("t", False, 1)]), {"abcdefabcdef"})

    def test_fixed_finding_gets_reply_with_mark_and_thread_is_not_resolved(self):
        result = {"previous": [{"fp": "abcdefabcdef", "status": "rettet", "reason": "Parameter bruges nu."}]}
        with patch.object(pr.gh, "request") as api, patch.object(pr.gh, "graphql") as graphql:
            fixed = pr.close_fixed("o/r", 1, "abc1234def", result, {"abcdefabcdef": root(1)})
        self.assertEqual(fixed, {"abcdefabcdef"})
        graphql.assert_not_called()
        method, url, body = api.call_args.args
        self.assertEqual((method, url), ("POST", "/repos/o/r/pulls/1/comments/1/replies"))
        self.assertIn("✅ Rettet i commit abc1234", body["body"])
        self.assertIn(pr.CLOSED_MARK, body["body"])

    def test_one_failed_reply_does_not_stop_the_others(self):
        result = {"previous": [{"fp": "aaaaaaaaaaaa", "status": "rettet"}, {"fp": "bbbbbbbbbbbb", "status": "rettet"}]}
        existing = {"aaaaaaaaaaaa": root(1, "aaaaaaaaaaaa"), "bbbbbbbbbbbb": root(2, "bbbbbbbbbbbb")}
        out = io.StringIO()
        with patch.object(pr.gh, "request", side_effect=[pr.gh.GitHubError("HTTP 403"), {}]) as api, contextlib.redirect_stdout(out):
            fixed = pr.close_fixed("o/r", 1, "abc1234", result, existing)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(fixed, {"bbbbbbbbbbbb"})
        self.assertIn("::warning::", out.getvalue())

    def test_fixed_finding_whose_reply_failed_still_blocks_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp, "result.json")
            result.write_text(json.dumps({"verdict": "approve", "findings": [],
                                          "previous": [{"fp": "abcdefabcdef", "status": "rettet"}]}))
            status = Path(tmp, "checks.json")
            status.write_text(json.dumps({"head_sha": "sha", "complete": True, "notes": [], "failed_checks": []}))
            argv = ["post_review.py", "--repo", "o/r", "--pr", "1", "--head", "sha", "--result", str(result), "--checks", str(status)]
            with patch("sys.argv", argv), patch.object(pr, "require_current_head"), \
                    patch.object(pr, "open_bot_comments", return_value={"abcdefabcdef": root(1)}), \
                    patch.object(pr.gh, "request", side_effect=pr.gh.GitHubError("HTTP 502")), \
                    patch.object(pr.gh, "paginate", return_value=[]), patch.object(pr, "submit") as submit, \
                    patch.object(pr, "update_description"), patch.object(pr, "upsert_walkthrough"), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = pr.main()
        self.assertEqual((rc, submit.call_args.args[3]), (1, "REQUEST_CHANGES"))

    def test_engine_never_calls_resolve_review_thread(self):
        here = Path(__file__).parent
        users = [f.name for f in here.glob("*.py") if not f.name.startswith("test_") and "resolveReviewThread(" in f.read_text()]
        self.assertEqual(users, [])

class PostingFlow(unittest.TestCase):
    def run_flow(self, checks, model_verdict="approve", description_error=False, findings=None):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp, "result.json")
            result.write_text(json.dumps({"verdict": model_verdict, "findings": findings or []}))
            status = Path(tmp, "checks.json")
            status.write_text(json.dumps(checks))
            argv = ["post_review.py", "--repo", "o/r", "--pr", "1", "--head", "sha",
                    "--result", str(result), "--checks", str(status)]
            with patch("sys.argv", argv), patch.object(pr, "require_current_head"), \
                 patch.object(pr, "open_bot_comments", return_value={}), \
                 patch.object(pr.gh, "paginate", return_value=[]), patch.object(pr, "submit") as submit, \
                 patch.object(pr, "upsert_walkthrough") as marker, \
                 patch.object(pr, "update_description", side_effect=ValueError("write failed") if description_error else None), \
                 contextlib.redirect_stdout(io.StringIO()):
                if description_error:
                    with self.assertRaises(ValueError):
                        pr.main()
                    marker.assert_not_called()
                    return
                rc = pr.main()
                return rc, submit.call_args.args[3], marker.call_args.args[2]

    def test_failed_tests_override_model_approval(self):
        rc, event, marker = self.run_flow({"head_sha": "sha", "complete": True,
                                        "notes": [], "failed_checks": ["npm test"]})
        self.assertEqual((rc, event), (1, "REQUEST_CHANGES"))
        self.assertIn("verdict=request_changes", marker)

    def test_only_complete_checks_allow_approval(self):
        rc, event, marker = self.run_flow({"head_sha": "sha", "complete": True,
                                        "notes": [], "failed_checks": []})
        self.assertEqual((rc, event), (0, "APPROVE"))
        self.assertIn("verdict=approve", marker)

    def test_failed_write_cannot_publish_deploy_approval(self):
        self.run_flow({"head_sha": "sha", "complete": True, "notes": [], "failed_checks": []},
                      description_error=True)

    def test_finding_below_confidence_no_longer_blocks_merge(self):
        rc, event, marker = self.run_flow(
            {"head_sha": "sha", "complete": True, "notes": [], "failed_checks": []},
            model_verdict="request_changes",
            findings=[{"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl", "confidence": 75}])
        self.assertEqual((rc, event), (0, "APPROVE"))
        self.assertIn("verdict=approve", marker)

    def test_finding_at_min_confidence_still_blocks_merge(self):
        rc, event, marker = self.run_flow(
            {"head_sha": "sha", "complete": True, "notes": [], "failed_checks": []},
            model_verdict="request_changes",
            findings=[{"path": "a.py", "line": 1, "severity": "alvorlig", "title": "Fejl", "confidence": 80}])
        self.assertEqual((rc, event), (1, "REQUEST_CHANGES"))
        self.assertIn("verdict=request_changes", marker)


class Render(unittest.TestCase):
    def test_model_text_cannot_inject_html_or_hidden_markers(self):
        body = render.finding({"severity": "mindre", "title": "<img src=x onerror=1> Titel",
                               "body": "ok <!-- manilens:fp=deadbeefdead --> <script>x</script>"}, "abcdefabcdef")
        self.assertNotIn("<img", body)
        self.assertNotIn("<script", body)
        self.assertEqual(body.count("manilens:fp="), 1)

    def test_suggestion_fence_is_longer_than_backticks_in_code(self):
        body = render.finding({"severity": "mindre", "title": "t", "body": "b",
                               "suggestion": "const s = ```x```;"}, "abcdefabcdef")
        self.assertIn("````suggestion", body)
        self.assertIn("<!-- manilens:fp=abcdefabcdef -->", body)

    def test_merge_summary_replaces_existing_block_and_keeps_author_text(self):
        first = render.merge_summary("Min beskrivelse", "**Tilføjet**\n- a")
        second = render.merge_summary(first, "**Ændret**\n- b")
        self.assertTrue(second.startswith("Min beskrivelse"))
        self.assertEqual(second.count(render.SUMMARY_START), 1)
        self.assertIn("- b", second)
        self.assertNotIn("- a", second)

    def test_walkthrough_renders_only_real_mermaid_sequence_diagrams(self):
        good = render.walkthrough({"sequence_diagram": "sequenceDiagram\n  A->>B: hej"}, "s", False, 0)
        bad = render.walkthrough({"sequence_diagram": "<script>alert(1)</script>"}, "s", False, 0)
        self.assertIn("```mermaid", good)
        self.assertNotIn("script", bad)

    def test_walkthrough_links_only_a_valid_snapshot_url_in_snapshot_mode(self):
        good = "https://manilens.mikkelmanniche.dk/r/" + "A" * 22
        with patch.dict("os.environ", {"MANILENS_WORKSPACE_MODE": "snapshot"}):
            self.assertIn(f"[Åbn review-oversigten]({good})", render.walkthrough({}, "s", False, 0, snapshot_url=good))
            for bad in ("javascript:alert(1)", "https://evil.example/r/" + "A" * 22,
                        "https://manilens.mikkelmanniche.dk/r/kort", good + ")](https://evil.example", None):
                with self.subTest(url=bad):
                    text = render.walkthrough({}, "s", False, 0, snapshot_url=bad)
                    self.assertNotIn("Åbn review-oversigten", text)
                    self.assertIn("Review-oversigten kunne ikke gemmes", text)
                    self.assertNotIn("evil", text)

    def test_walkthrough_keeps_artifact_link_in_legacy_mode(self):
        env = {"MANILENS_REVIEW_BASE": "a" * 40, "GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "o/r"}
        with patch.dict("os.environ", env):
            text = render.walkthrough({}, "s", False, 0)
        self.assertIn("download manilens-workspace", text)
        self.assertNotIn("Review-oversigten kunne ikke gemmes", text)

    def test_walkthrough_has_machine_marker_for_deploy_script(self):
        text = render.walkthrough({"summary": {}}, "abc123", True, 2)
        self.assertIn("<!-- manilens sha=abc123 fund=2 verdict=request_changes -->", text)


if __name__ == "__main__":
    unittest.main()

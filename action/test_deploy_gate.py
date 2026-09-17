import unittest
import deploy_gate as gate

SHA = "a" * 40


def comment(**updates):
    base = {"id": 1, "user": {"login": "manilens[bot]", "type": "Bot"},
            "updated_at": "2026-09-14T12:00:00Z", "body": gate.WALKTHROUGH +
            f"\n<!-- manilens sha={SHA} fund=0 verdict=approve -->"}
    return dict(base, **updates)


class DeployGate(unittest.TestCase):
    def setUp(self):
        self.pr = {"state": "open", "head": {"sha": SHA}}

    def test_current_bot_review_is_accepted(self):
        self.assertEqual(gate.verdict(self.pr, [comment()], SHA), "approve")

    def test_human_account_with_same_name_cannot_approve(self):
        forged = comment(user={"login": "manilens", "type": "User"})
        self.assertIsNone(gate.verdict(self.pr, [forged], SHA))

    def test_new_commit_invalidates_old_approval(self):
        self.pr["head"]["sha"] = "b" * 40
        self.assertEqual(gate.verdict(self.pr, [comment()], SHA), "error")

    def test_embedded_marker_without_final_marker_is_not_approval(self):
        c = comment()
        c["body"] += "\nMore text after an embedded marker"
        self.assertIsNone(gate.verdict(self.pr, [c], SHA))

    def test_latest_error_supersedes_approval(self):
        c = comment(id=2, updated_at="2026-09-14T12:01:00Z",
                    body=gate.WALKTHROUGH + f"\n<!-- manilens sha={SHA} fund=-1 verdict=error -->")
        self.assertEqual(gate.verdict(self.pr, [comment(), c], SHA), "error")

    def test_nonzero_findings_cannot_be_approved(self):
        c = comment(); c["body"] = c["body"].replace("fund=0", "fund=2")
        self.assertEqual(gate.verdict(self.pr, [c], SHA), "error")

"""collect_previous: kun ManiLens' egne, stadig åbne fund.  python3 -m unittest -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_previous as cp
import post_review as pr

BOT = "manilens[bot]"


class CollectPrevious(unittest.TestCase):
    def collect(self, comments, threads):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "previous.json")
            argv = ["collect_previous.py", "--repo", "o/r", "--pr", "1", "--out", str(out)]
            with patch.object(sys, "argv", argv), patch.object(pr.gh, "paginate", return_value=comments), \
                    patch.object(cp, "review_threads", return_value=threads), patch("builtins.print"):
                cp.main()
            return json.loads(out.read_text())

    def test_finding_closed_by_bot_mark_is_not_sent_to_the_model_again(self):
        comments = [
            {"id": 1, "user": {"login": BOT}, "body": "**Åben** <!-- manilens:fp=aaaaaaaaaaaa -->", "path": "a.py", "line": 3},
            {"id": 2, "user": {"login": BOT}, "body": "**Rettet** <!-- manilens:fp=bbbbbbbbbbbb -->", "path": "a.py", "line": 9},
            {"id": 3, "in_reply_to_id": 2, "user": {"login": BOT, "type": "Bot"}, "body": f"✅ Rettet i commit abc.\n\n{pr.CLOSED_MARK}"},
        ]
        previous = self.collect(comments, [("t1", False, 1), ("t2", False, 2)])
        self.assertEqual([p["fp"] for p in previous], ["aaaaaaaaaaaa"])

    def test_resolved_thread_is_still_closed(self):
        comments = [{"id": 1, "user": {"login": BOT}, "body": "**X** <!-- manilens:fp=aaaaaaaaaaaa -->", "path": "a.py", "line": 3}]
        self.assertEqual(self.collect(comments, [("t1", True, 1)]), [])


if __name__ == "__main__":
    unittest.main()

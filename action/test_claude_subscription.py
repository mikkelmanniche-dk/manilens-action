import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import claude_subscription as cs


class Subscription(unittest.TestCase):
    def test_setup_token_is_accepted_only_without_api_key_source(self):
        for source, expected in [(None, 0), ("apiKeyHelper", 2)]:
            auth = type("Auth", (), {"returncode": 0, "stdout": json.dumps({
                "loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty",
                "apiKeySource": source})})()
            with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "fake"}), \
                 patch.object(cs.subprocess, "run", return_value=auth), \
                 patch.object(cs.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
                launch.return_value.wait.return_value = 0
                self.assertEqual(cs.run(["claude"]), expected)
                if expected:
                    launch.assert_not_called()

    def test_api_auth_is_rejected_without_starting_review(self):
        auth = type("Auth", (), {"returncode": 0, "stdout": json.dumps({
            "loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"})})()
        with patch.object(cs.subprocess, "run", return_value=auth), \
             patch.object(cs.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cs.run(["claude", "-p", "review"]), 2)
        launch.assert_not_called()

    def test_auth_and_review_do_not_receive_api_or_github_tokens(self):
        auth = type("Auth", (), {"returncode": 0, "stdout": json.dumps({
            "loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"})})()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake", "GITHUB_TOKEN": "fake"}), \
             patch.object(cs.subprocess, "run", return_value=auth) as check, \
             patch.object(cs.subprocess, "Popen") as launch:
            launch.return_value.wait.return_value = 0
            self.assertEqual(cs.run(["claude", "-p", "review"]), 0)
        for call in (check.call_args, launch.call_args):
            self.assertNotIn("ANTHROPIC_API_KEY", call.kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN", call.kwargs["env"])

    def test_timeout_stops_process_group_without_retry(self):
        auth = type("Auth", (), {"returncode": 0, "stdout": json.dumps({
            "loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"})})()
        with patch.object(cs.subprocess, "run", return_value=auth), \
             patch.object(cs.subprocess, "Popen") as launch, patch.object(cs.os, "killpg") as kill, \
             contextlib.redirect_stderr(io.StringIO()):
            launch.return_value.pid = 123
            launch.return_value.wait.side_effect = [cs.subprocess.TimeoutExpired("claude", 1), 0]
            self.assertEqual(cs.run(["claude"], 1), 124)
            launch.assert_called_once()
            kill.assert_called_once_with(123, cs.signal.SIGTERM)

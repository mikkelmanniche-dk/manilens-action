#!/usr/bin/env python3
"""Run official Claude Code with subscription auth, no retries, and a deadline."""
import json
import os
import signal
import subprocess
import sys

REMOVE_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
              "CLAUDE_CODE_USE_FOUNDRY")


def run(command, timeout=600):
    env = {k: v for k, v in os.environ.items() if k not in REMOVE_ENV}
    try:
        auth = subprocess.run([command[0], "auth", "status"], env=env,
                              capture_output=True, text=True, timeout=30)
        status = json.loads(auth.stdout)
        subscription = (status.get("authMethod") == "claude.ai"
                        or (status.get("authMethod") == "oauth_token"
                            and bool(env.get("CLAUDE_CODE_OAUTH_TOKEN"))))
        if (auth.returncode or status.get("loggedIn") is not True
                or not subscription or status.get("apiProvider") != "firstParty"
                or status.get("apiKeySource") not in (None, "none")):
            raise ValueError("subscription login required")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("ManiLens kræver et aktivt Claude-abonnementslogin; API-login bruges ikke.", file=sys.stderr)
        return 2
    proc = subprocess.Popen(command, env=env, start_new_session=True)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        print("ManiLens stoppede ved tidsgrænsen; reviewet genstartes ikke automatisk.", file=sys.stderr)
        return 124


if __name__ == "__main__":
    try:
        deadline = int(os.environ.get("MANILENS_TIMEOUT_SECONDS", "600"))
        if deadline <= 0 or not sys.argv[1:]:
            raise ValueError("invalid arguments")
        sys.exit(run(sys.argv[1:], deadline))
    except (OSError, ValueError):
        print("Ugyldig Claude-kommando eller tidsgrænse.", file=sys.stderr)
        sys.exit(2)

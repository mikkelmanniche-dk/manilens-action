#!/usr/bin/env python3
"""Read-only deploy gate: current commit plus the bot's own final marker."""
import json
import os
import re
import subprocess
import sys
import time

MARKER = re.compile(r"<!-- manilens sha=([0-9a-f]{40}) fund=(-?\d+) verdict=(approve|request_changes|error) -->")
WALKTHROUGH = "<!-- manilens:walkthrough -->"


def verdict(pr, comments, head):
    if pr.get("state") != "open" or pr.get("head", {}).get("sha") != head:
        return "error"
    own = [c for c in comments if c.get("user", {}).get("login") == "manilens[bot]"
           and c["user"].get("type") == "Bot" and c.get("body", "").startswith(WALKTHROUGH)]
    if not own:
        return None
    latest = max(own, key=lambda c: (c.get("updated_at", ""), c.get("id", 0)))
    match = MARKER.fullmatch(latest["body"].strip().splitlines()[-1])
    if not match or match[1] != head:
        return None
    if match[3] == "approve" and int(match[2]) != 0:
        return "error"
    return match[3]


def api(path, pages=False):
    cmd = [os.environ.get("GH", "gh"), "api", path]
    if pages:
        cmd += ["--paginate", "--slurp"]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise ValueError("GitHub kunne ikke læses")
    data = json.loads(run.stdout)
    return [c for page in data for c in page] if pages else data


def main():
    number, head = sys.argv[1:3]
    minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    if not number.isdigit() or not re.fullmatch(r"[0-9a-f]{40}", head) or minutes < 1:
        raise ValueError("Forventet: PR-nummer, fuld commit-SHA og positive minutter")
    end = time.monotonic() + minutes * 60
    print(f"Venter på ManiLens for {head[:7]} (op til {minutes} minutter)", flush=True)
    while time.monotonic() < end:
        comments = api(f"repos/{{owner}}/{{repo}}/issues/{number}/comments", pages=True)
        pr = api(f"repos/{{owner}}/{{repo}}/pulls/{number}")
        state = verdict(pr, comments, head)
        if state:
            print("ManiLens godkender dette commit." if state == "approve"
                  else "ManiLens blokerer, eller PR'en er ændret. Merge er ikke tilladt.")
            return 0 if state == "approve" else 1
        time.sleep(min(10, max(0, end - time.monotonic())))
    print("Intet gyldigt review inden tidsgrænsen. Merge er ikke tilladt.")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("ManiLens-gate kunne ikke verificere reviewet. Merge er ikke tilladt.", file=sys.stderr)
        sys.exit(2)

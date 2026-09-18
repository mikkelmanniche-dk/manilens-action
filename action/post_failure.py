#!/usr/bin/env python3
"""Melder paa PR'en, at ManiLens ikke kunne gennemfoere et review (fail-closed).

  post_failure.py --repo ejer/navn --pr 12 --head <sha> --run-url <url>

Opdaterer den faste ManiLens-kommentar med verdict=error, saa udrul.sh aldrig
tolker en fejl som "ingen fund".
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_api as gh  # noqa: E402
import render
from tekster import t  # noqa: E402
from post_review import upsert_walkthrough  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--run-url", required=True)
    args = ap.parse_args()

    body = "\n".join([
        render.WALKTHROUGH_MARK,
        "## 🔍 ManiLens",
        "",
        t("review_failed", head=args.head[:7], run_url=args.run_url),
        "",
        f"<!-- manilens sha={args.head} fund=-1 verdict=error -->",
    ])
    upsert_walkthrough(args.repo, args.pr, body)
    print("ManiLens: fejl meldt på PR'en")


if __name__ == "__main__":
    try:
        main()
    except gh.GitHubError as err:
        print(f"Kunne ikke melde fejlen: {err}", file=sys.stderr)
        sys.exit(2)

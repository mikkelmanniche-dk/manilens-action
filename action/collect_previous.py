#!/usr/bin/env python3
"""Henter ManiLens' egne, stadig aabne fund paa en PR til motorens {{PREVIOUS}}.

  collect_previous.py --repo ejer/navn --pr 12 --out previous.json
"""
import argparse
import json
import os
import re

import github_api as gh
from post_review import FP_RE, review_threads

TITLE_RE = re.compile(r"\*\*(.+?)\*\*")


def unresolved_comment_ids(repo, pr):
    return {first_id for _, resolved, first_id in review_threads(repo, pr) if not resolved}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bot", default=os.environ.get("MANILENS_BOT", "manilens[bot]"))
    args = ap.parse_args()

    open_ids = unresolved_comment_ids(args.repo, args.pr)
    previous = []
    for c in gh.paginate(f"/repos/{args.repo}/pulls/{args.pr}/comments"):
        fp = FP_RE.search(c["body"])
        if c["user"]["login"] != args.bot or not fp or c["id"] not in open_ids:
            continue
        title = TITLE_RE.search(c["body"])
        previous.append({"fp": fp.group(1), "path": c["path"],
                         "line": c.get("line") or c.get("original_line"),
                         "title": title.group(1) if title else "",
                         "body": c["body"].split("<details>")[0][:1500]})
    with open(args.out, "w") as fh:
        json.dump(previous, fh, ensure_ascii=False, indent=2)
    print(f"{len(previous)} åbne fund fra tidligere commits")


if __name__ == "__main__":
    main()

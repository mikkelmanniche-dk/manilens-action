#!/usr/bin/env python3
"""Poster ManiLens' resultat paa en pull request.

  post_review.py --repo ejer/navn --pr 12 --head <sha> --result result.json

Goer i denne raekkefoelge:
  1. Markerer tidligere fund som rettet og loeser deres traade.
  2. Poster nye fund som kommentarer i koden (fund uden for diff'en kommer i
     review-teksten), uden at gentage fund der allerede staar aabne.
  3. Afgiver review: REQUEST_CHANGES ved blokerende fund, ellers APPROVE.
  4. Opdaterer den faste ManiLens-kommentar og opsummeringen i PR-beskrivelsen.

Exit-kode 1, naar PR'en blokeres, saa checket "ManiLens" bliver roedt.
Exit-kode 2 ved fejl — fejl blokerer altid (fail-closed).
"""
import argparse
import hashlib
import json
import os
import re
import sys

import github_api as gh
import render

BLOCKING = ("kritisk", "alvorlig")
FP_RE = re.compile(r"<!-- manilens:fp=([0-9a-f]{12}) -->")


def fingerprint(finding):
    title = re.sub(r"\W+", " ", finding.get("title", "")).strip().lower()
    return hashlib.sha1(f"{finding.get('path')}|{title}".encode()).hexdigest()[:12]


def commentable_lines(files):
    """Linjenumre i HEAD-filen, som GitHub tillader kommentarer paa."""
    lines = {}
    for f in files:
        allowed, new_line = set(), 0
        for row in (f.get("patch") or "").splitlines():
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", row)
            if hunk:
                new_line = int(hunk.group(1))
                continue
            if row.startswith("-") or row.startswith("\\"):  # "\ No newline at end of file"
                continue
            allowed.add(new_line)
            new_line += 1
        lines[f["filename"]] = allowed
    return lines


def validate(result):
    if not isinstance(result, dict) or result.get("verdict") not in ("approve", "request_changes"):
        raise ValueError("resultatet mangler et gyldigt 'verdict'")
    for field in ("findings", "previous", "pre_merge_checks"):
        value = result.get(field, [])
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            raise ValueError(f"ugyldigt felt: {field}")
    if "findings" not in result:
        raise ValueError("resultatet mangler findings")
    for f in result.get("findings", []):
        if f.get("severity") not in ("kritisk", "alvorlig", "mindre") or not f.get("path"):
            raise ValueError(f"ugyldigt fund: {json.dumps(f, ensure_ascii=False)[:200]}")
    for c in result.get("pre_merge_checks", []):
        if c.get("mode") not in ("error", "warning") or c.get("status") not in ("pass", "fail", "skip"):
            raise ValueError("ugyldigt pre-merge-tjek")
    for p in result.get("previous", []):
        if p.get("status") not in ("rettet", "stadig_aktuel"):
            raise ValueError("uafklaret status for tidligere fund")


def is_blocked(result):
    """A model's explicit rejection can never be turned into an approval."""
    return (result["verdict"] == "request_changes"
            or any(f["severity"] in BLOCKING for f in result["findings"])
            or any(c.get("mode") == "error" and c.get("status") == "fail"
                   for c in result.get("pre_merge_checks", []))
            or any(p.get("status") == "stadig_aktuel" for p in result.get("previous", [])))


def require_current_head(repo, pr, head):
    current = gh.request("GET", f"/repos/{repo}/pulls/{pr}")
    if current.get("state") != "open" or current.get("head", {}).get("sha") != head:
        raise ValueError("PR'en er lukket eller har fået nye commits; reviewet er forældet")


def scanner_gate(path, head):
    """Deterministic test results are required independently of the model."""
    if not path:
        return "Faste tjek er ikke kørt; reviewet kan ikke godkende merge."
    try:
        with open(path) as fh:
            status = json.load(fh)
        if status.get("head_sha") != head:
            return "Faste tjek gælder et andet commit."
        if status.get("complete") is not True or status.get("notes") != []:
            return "Scannerdækningen er ufuldstændig."
        if status.get("failed_checks") != []:
            return "Et eller flere faste tjek fejlede."
    except (OSError, ValueError, AttributeError):
        return "Status fra faste tjek mangler eller er ugyldig."
    return None


def open_bot_comments(repo, pr, bot):
    comments = gh.paginate(f"/repos/{repo}/pulls/{pr}/comments")
    ours = {}
    for c in comments:
        if c["user"]["login"] != bot or c.get("in_reply_to_id"):
            continue
        match = FP_RE.search(c["body"])
        if match:
            ours[match.group(1)] = c
    open_ids = {first_id for _, resolved, first_id in review_threads(repo, pr) if not resolved}
    return {fp: c for fp, c in ours.items() if c["id"] in open_ids}


def review_threads(repo, pr):
    """Alle review-traade paa PR'en: (thread_id, is_resolved, foerste kommentars id)."""
    owner, name = repo.split("/")
    threads, cursor = [], None
    while True:
        data = gh.graphql("""
          query($owner:String!,$name:String!,$pr:Int!,$after:String){repository(owner:$owner,name:$name){
            pullRequest(number:$pr){reviewThreads(first:100,after:$after){
              pageInfo{hasNextPage endCursor}
              nodes{id isResolved comments(first:1){nodes{databaseId}}}}}}}""",
            {"owner": owner, "name": name, "pr": pr, "after": cursor})
        page = data["repository"]["pullRequest"]["reviewThreads"]
        for t in page["nodes"]:
            first = t["comments"]["nodes"]
            if first:
                threads.append((t["id"], t["isResolved"], first[0]["databaseId"]))
        if not page["pageInfo"]["hasNextPage"]:
            return threads
        cursor = page["pageInfo"]["endCursor"]


def resolve_thread(repo, pr, comment_id):
    for thread_id, resolved, first_id in review_threads(repo, pr):
        if first_id == comment_id and not resolved:
            gh.graphql("mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id}}}",
                       {"id": thread_id})


def close_fixed(repo, pr, head, result, existing):
    fixed = 0
    for prev in result.get("previous", []):
        comment = existing.get(prev.get("fp"))
        if not comment or prev.get("status") != "rettet":
            continue
        gh.request("POST", f"/repos/{repo}/pulls/{pr}/comments/{comment['id']}/replies",
                   {"body": f"✅ Rettet i commit {head[:7]}. {render.clean(prev.get('reason', ''))}".strip()})
        resolve_thread(repo, pr, comment["id"])
        fixed += 1
    return fixed


def build_review(result, existing, allowed):
    inline, outside = [], []
    for f in result.get("findings", []):
        fp = fingerprint(f)
        if fp in existing:  # allerede postet paa et tidligere commit
            continue
        body = render.finding(f, fp)
        line = f.get("line")
        if isinstance(line, int) and line in allowed.get(f["path"], set()):
            comment = {"path": f["path"], "line": line, "side": "RIGHT", "body": body}
            start = f.get("start_line")
            if isinstance(start, int) and start < line and start in allowed[f["path"]]:
                comment.update(start_line=start, start_side="RIGHT")
            inline.append(comment)
        else:
            outside.append(f)
    return inline, outside


def submit(repo, pr, head, event, body, comments):
    payload = {"commit_id": head, "event": event, "body": body, "comments": comments}
    try:
        gh.request("POST", f"/repos/{repo}/pulls/{pr}/reviews", payload)
    except gh.GitHubError as err:
        # APPROVE kan vaere slaaet fra for Actions i repoet; saa en almindelig kommentar.
        if event != "APPROVE" or "422" not in str(err):
            raise
        payload["event"] = "COMMENT"
        gh.request("POST", f"/repos/{repo}/pulls/{pr}/reviews", payload)


def upsert_walkthrough(repo, pr, body, bot=None):
    bot = bot or os.environ.get("MANILENS_BOT", "manilens[bot]")
    for c in gh.paginate(f"/repos/{repo}/issues/{pr}/comments"):
        if c.get("user", {}).get("login") == bot and render.WALKTHROUGH_MARK in c["body"]:
            gh.request("PATCH", f"/repos/{repo}/issues/comments/{c['id']}", {"body": body})
            return
    gh.request("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})


def update_description(repo, pr, summary_md):
    current = gh.request("GET", f"/repos/{repo}/pulls/{pr}").get("body") or ""
    gh.request("PATCH", f"/repos/{repo}/pulls/{pr}", {"body": render.merge_summary(current, summary_md)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--checks", help="tools.md.status.json fra tjek-jobbet")
    ap.add_argument("--bot", default=os.environ.get("MANILENS_BOT", "manilens[bot]"))
    ap.add_argument("--snapshot-url", default="", help="v2: link til review-oversigten fra snapshot_upload.py")
    args = ap.parse_args()

    with open(args.result) as fh:
        result = json.load(fh)
    validate(result)
    gate_error = scanner_gate(args.checks, args.head)
    if gate_error:
        result.setdefault("pre_merge_checks", []).append({"name": "Faste tjek",
            "mode": "error", "status": "fail", "explanation": gate_error})
    require_current_head(args.repo, args.pr, args.head)

    existing = open_bot_comments(args.repo, args.pr, args.bot)
    fixed = close_fixed(args.repo, args.pr, args.head, result, existing)
    files = gh.paginate(f"/repos/{args.repo}/pulls/{args.pr}/files")
    inline, outside = build_review(result, existing, commentable_lines(files))

    blocking = [f for f in result.get("findings", []) if f["severity"] in BLOCKING]
    blocked = is_blocked(result)
    fixed_fps = {p.get("fp") for p in result.get("previous", []) if p.get("status") == "rettet"}
    blocked = blocked or bool(set(existing) - fixed_fps)
    event = "REQUEST_CHANGES" if blocked else "APPROVE"

    result["review_info"] = {"head": args.head, "inline_count": len(inline),
                             "files": [f["filename"] for f in files]}

    submit(args.repo, args.pr, args.head, event,
           render.review_body(result, outside, fixed, blocked), inline)
    update_description(args.repo, args.pr, render.summary(result))
    # Publish the deploy marker only after all other writes have succeeded.
    require_current_head(args.repo, args.pr, args.head)
    upsert_walkthrough(args.repo, args.pr,
                       render.walkthrough(result, args.head, blocked, len(blocking), args.snapshot_url or None), args.bot)

    print(f"ManiLens: {len(result.get('findings', []))} fund, {fixed} rettet, "
          f"{'blokeret' if blocked else 'godkendt'}")
    return 1 if blocked else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, gh.GitHubError) as err:
        print(f"ManiLens-fejl: {err}", file=sys.stderr)
        sys.exit(2)

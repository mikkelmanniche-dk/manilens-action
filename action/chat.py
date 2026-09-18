#!/usr/bin/env python3
"""Chat-delen af ManiLens: saml konteksten, og post svaret bagefter.

  chat.py context --event event.json --out context.json
  chat.py reply   --event event.json --result reply.json

Konteksten indeholder den nye kommentar og, hvis det er et svar i en af
ManiLens' traade, det oprindelige fund og hele traaden.
"""
import argparse
import json
import os
import re
import sys

import github_api as gh
import render
from commands import parse_command
from post_review import CLOSED_MARK, FP_RE, is_minor_finding_comment, open_finding_ids, review_threads

TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
WRITE_PERMISSIONS = ("admin", "maintain", "write")
V1_NO_FINISHING = ("Kodehandlinger er ikke tilgængelige for kolleger i v1. "
                   "Brug `@manilens fix prompt` for en samlet prompt til din egen coding-agent; ingen Claude-kald.")
BOT = os.environ.get("MANILENS_BOT", "manilens[bot]")

HELP = """## ManiLens-kommandoer

| Kommando | Handling |
|---|---|
| `@manilens review` | Review med tidligere fund som kontekst; bruger Claude-kvote |
| `@manilens full review` | Nyt review uden tidligere fund som kontekst; bruger Claude-kvote |
| `@manilens pause` | Stop automatiske reviews på denne PR |
| `@manilens resume` | Genoptag fra næste push |
| `@manilens fix prompt` | Saml åbne linjefund til din coding-agent; ingen Claude-kald |
| `@manilens help` | Vis denne hjælp; ingen Claude-kald |

Du kan også stille spørgsmål med `@manilens` eller svare i en fundtråd.
"""

HELP_FINISHING = """Kodehandlinger kræver `MANILENS_ENABLE_FINISHING=true` og bruger Claude-kvote:
`@manilens autofix`, `@manilens generate unit tests`, `@manilens generate docstrings`,
`@manilens simplify`, `@manilens fix ci`. Tilføj `stacked pr` for en separat draft-PR.
Uden dette suffix afleveres en commit på PR-branchen. Konfigurerede tests køres i et separat job.
`@manilens run recipe-name` bruger en recipe fra PR-basens konfiguration.
`@manilens fix merge conflict` foreslår løsning af tekstkonflikter og afleverer en merge-commit;
uklare eller ikke understøttede konflikter stopper uden at ændre branchen.
"""


def open_fix_prompt(repo, number):
    comments = gh.paginate(f"/repos/{repo}/pulls/{number}/comments")
    open_ids = open_finding_ids(comments, review_threads(repo, number), BOT)
    findings = []
    for c in comments:
        user = c.get("user") or {}
        if (c["id"] not in open_ids or user.get("login") != BOT
                or user.get("type") != "Bot" or not FP_RE.search(c.get("body", ""))):
            continue
        # Keep the entire original instructions as untrusted data, including its prompt.
        findings.append({"path": c["path"], "line": c.get("line") or c.get("original_line"),
                         "agent_prompt": c["body"]})
    return render.prompt_block(findings) or "Ingen åbne linjefund fra ManiLens."


def help_text(no_finishing):
    return HELP if no_finishing else HELP + HELP_FINISHING


def require_write_permission(repo, login):
    """author_association beviser ikke nuværende skriveadgang (M8); spørg GitHub live."""
    perm = gh.request("GET", f"/repos/{repo}/collaborators/{login}/permission")
    if (perm or {}).get("permission") not in WRITE_PERMISSIONS:
        raise PermissionError("kommentaren er ikke fra en med skriveadgang lige nu")


def load_event(path):
    with open(path) as fh:
        event = json.load(fh)
    comment = event["comment"]
    if comment.get("author_association") not in TRUSTED:
        raise PermissionError("kommentaren er ikke fra en med skriveadgang")
    if comment["user"]["login"] == BOT:
        raise PermissionError("ManiLens svarer ikke sig selv")
    repo = event["repository"]["full_name"]
    number = (event.get("pull_request") or event.get("issue") or {})["number"]
    return event, repo, number, comment


def thread_for(repo, number, comment):
    """Traaden, hvis kommentaren er et svar paa et ManiLens-fund."""
    root_id = comment.get("in_reply_to_id")
    if not root_id:
        return None
    comments = gh.paginate(f"/repos/{repo}/pulls/{number}/comments")
    root = next((c for c in comments if c["id"] == root_id), None)
    if not root or root["user"]["login"] != BOT or not FP_RE.search(root["body"]):
        return None
    replies = [c for c in comments if c.get("in_reply_to_id") == root_id]
    return {
        "root_id": root_id,
        "path": root["path"],
        "line": root.get("line") or root.get("original_line"),
        "finding": root["body"].split("<details>")[0],
        "replies": [{"author": r["user"]["login"], "body": r["body"]}
                    for r in sorted(replies, key=lambda r: r["created_at"])],
    }


def cmd_context(args):
    event, repo, number, comment = load_event(args.event)
    require_write_permission(repo, comment["user"]["login"])
    no_finishing = getattr(args, "no_finishing", False)
    body = comment["body"]
    thread = thread_for(repo, number, comment) if "pull_request" in event else None
    if not thread and not re.search(r"@manilens\b", body, re.I):
        print("ingen opgave til ManiLens")
        return 3
    if parse_command(body):
        if no_finishing:
            gh.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": V1_NO_FINISHING})
        elif os.environ.get("MANILENS_ENABLE_FINISHING") != "true":
            gh.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body":
                "Kodehandlinger er ikke aktiveret. Repoets ejer skal først kontrollere forbrugsindstillinger "
                "og aktivere MANILENS_ENABLE_FINISHING. Brug `@manilens fix prompt` uden Claude-kald."})
        return 3
    local_command = re.fullmatch(r"\s*@manilens\s+(help|fix prompt)\s*", body, re.I)
    if local_command:
        reply = help_text(no_finishing) if local_command.group(1).lower() == "help" else open_fix_prompt(repo, number)
        gh.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": reply})
        return 3
    pr = gh.request("GET", f"/repos/{repo}/pulls/{number}")
    command = re.search(r"@manilens\s+(full review|review|pause|resume)\b", body, re.I)
    mode = command.group(1).lower() if command and not thread else "chat"
    context = {"repo": repo, "number": number, "title": pr["title"], "head": pr["head"]["sha"],
               "base": (pr.get("base") or {}).get("sha", ""),
               "mode": mode,
               "same_repo": pr["head"]["repo"] is not None and pr["head"]["repo"]["full_name"] == repo,
               "comment": {"author": comment["user"]["login"], "body": body}, "thread": thread}
    with open(args.out, "w") as fh:
        json.dump(context, fh, ensure_ascii=False, indent=2)
    if mode in ("pause", "resume"):
        url = f"/repos/{repo}/issues/{number}/labels"
        if mode == "pause":
            gh.request("POST", url, {"labels": ["manilens:pause"]})
        else:
            try:
                gh.request("DELETE", f"{url}/manilens:pause")
            except gh.GitHubError as err:
                if "404" not in str(err):
                    raise
        gh.request("POST", f"/repos/{repo}/issues/{number}/comments",
                   {"body": "⏸️ ManiLens holder pause på denne PR." if mode == "pause"
                    else "▶️ ManiLens reviewer igen fra næste push (eller `@manilens review`)."})
        print(f"label {mode}")
        return 3
    print(f"kontekst klar ({mode})")
    return 0


def cmd_reply(args):
    event, repo, number, comment = load_event(args.event)
    require_write_permission(repo, comment["user"]["login"])
    with open(args.result) as fh:
        result = json.load(fh)
    reply = (result.get("reply") or "").strip()
    if not reply:
        raise ValueError("svaret er tomt")
    if result.get("learning"):
        reply += f"\n\n📝 Forslag til `.manilens/laering.md`: {result['learning']}"
    thread = thread_for(repo, number, comment) if "pull_request" in event else None
    if thread:
        # "resolve" lukker fundet med markøren; tråden løses ikke (kræver contents: write). Et blokerende fund lukkes
        # aldrig fra chat (prompt-injektion i tråden må ikke kunne fjerne en blokering) — kun et nyt review lukker det.
        if result.get("thread_action") == "resolve":
            if is_minor_finding_comment(thread["finding"]):
                reply += f"\n\n{CLOSED_MARK}"
            else:
                reply += "\n\n_Fundet er blokerende og lukkes først, når et nyt review bekræfter rettelsen._"
        gh.request("POST", f"/repos/{repo}/pulls/{number}/comments/{thread['root_id']}/replies",
                   {"body": reply})
    else:
        gh.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": reply})
    print(f"svar postet ({result.get('thread_action', 'none')})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context")
    c.add_argument("--event", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--no-finishing", action="store_true",
                   help="kolleger (v2): kodehandlinger findes ikke og nævnes ikke i hjælpen")
    r = sub.add_parser("reply")
    r.add_argument("--event", required=True)
    r.add_argument("--result", required=True)
    args = ap.parse_args()
    return cmd_context(args) if args.cmd == "context" else cmd_reply(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PermissionError as err:
        print(f"springer over: {err}")
        sys.exit(3)
    except (OSError, ValueError, KeyError, gh.GitHubError) as err:
        print(f"ManiLens chat-fejl: {err}", file=sys.stderr)
        sys.exit(2)

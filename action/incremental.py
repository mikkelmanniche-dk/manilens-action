"""Select a proven prior bot review; fall back to the entire PR on uncertainty."""
import argparse
import json
from pathlib import Path
import re
import subprocess

import github_api as gh
from deploy_gate import MARKER, WALKTHROUGH
from workspace import commit, git

BASE = re.compile(r'<!-- manilens:review-base=([0-9a-f]{40}) -->')


def choose_base(repo, base, head, comments):
    own = [c for c in comments if c.get('user', {}).get('login') == 'manilens[bot]'
           and c['user'].get('type') == 'Bot' and c.get('body', '').startswith(WALKTHROUGH)]
    if not own:
        return None
    latest = max(own, key=lambda c: (c.get('updated_at', ''), c.get('id', 0)))
    body = latest['body'].strip()
    prior_base = BASE.search(body)
    prior_head = MARKER.fullmatch(body.splitlines()[-1])
    if not prior_base or prior_base[1] != base or not prior_head or prior_head[3] == 'error':
        return None
    previous = prior_head[1]
    # Re-reviewing the same SHA is a full review, not an empty diff approval.
    if previous == head:
        return None
    known = subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',previous,head],capture_output=True)
    return previous if known.returncode == 0 else None


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--src',required=True);ap.add_argument('--repo',required=True)
    ap.add_argument('--pr',type=int,required=True);ap.add_argument('--base',required=True)
    ap.add_argument('--head',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args()
    base,head=commit(args.src,args.base),commit(args.src,args.head)
    previous=choose_base(args.src,base,head,gh.paginate(f'/repos/{args.repo}/issues/{args.pr}/comments'))
    start=previous or git(args.src,'merge-base',base,head).strip()
    diff=git(args.src,'diff','--no-ext-diff','--no-textconv',start,head,'--')
    # A non-code push still needs a full pass to re-evaluate unresolved findings.
    if previous and not diff.strip():
        previous=None;start=git(args.src,'merge-base',base,head).strip()
        diff=git(args.src,'diff','--no-ext-diff','--no-textconv',start,head,'--')
    Path(args.out).write_text(diff)
    Path(args.out+'.scope.json').write_text(json.dumps({'mode':'incremental' if previous else 'full',
                                                      'start_sha':start,'head_sha':head,'base_sha':base}))


if __name__=='__main__':
    main()

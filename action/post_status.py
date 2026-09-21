"""One PR summary for scanners and AI, including runs without a Claude credential."""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import github_api as gh
import render
from post_review import require_current_head, scanner_gate, upsert_walkthrough
from scan_report import render_execution
from snapshot_upload import USER_AGENT, oidc_token

MARK = '<!-- manilens:checks -->'
AI_TEXT = {
    'verified': ('Claude-review gennemført', 'Claude review completed'),
    'not_needed': ('Ingen model nødvendig for ændringen', 'No model needed for this change'),
    'missing_token': ('Claude-token mangler. Tilføj det i GitHub Secrets for at aktivere AI-review.', 'Claude token missing. Add it in GitHub Secrets to enable AI review.'),
    'daily_cap': ('Dagens AI-kvote er brugt. Scannerne er stadig kørt.', 'Daily AI quota reached. Scanners still ran.'),
    'error': ('AI-review kunne ikke gennemføres. Åbn kørslen for fejlen.', 'AI review did not complete. Open the run for details.'),
}


def scanner_state(path, head):
    try:
        data = json.loads(Path(path).read_text())
        if scanner_gate(path, head):
            return 'error', data
        return ('findings' if data.get('findings_count', 0) else 'passed'), data
    except (OSError, ValueError):
        return 'error', {}


def report(repo, pr, head, checks, ai, run_url, published=False):
    require_current_head(repo, pr, head)
    state, data = scanner_state(checks, head)
    english = os.environ.get('MANILENS_LANGUAGE') == 'en'
    ai_text = AI_TEXT[ai][int(english)]
    base = f'{render.WALKTHROUGH_MARK}\n## ManiLens\n\n<!-- manilens sha={head} fund=-1 verdict=error -->'
    # Preserve only a review published for this exact head by our bot in this run.
    if published:
        for comment in gh.paginate(f'/repos/{repo}/issues/{pr}/comments'):
            if (comment.get('user', {}).get('login') == os.environ.get('MANILENS_BOT', 'manilens[bot]')
                    and render.WALKTHROUGH_MARK in comment.get('body', '')
                    and f'manilens sha={head} ' in comment['body']):
                base = comment['body'].split(MARK)[0].rstrip()
                break
    lines = [base, '', MARK, f'**Scanners: {state} · AI: {ai_text}**', '', f'Commit: `{head[:12]}` · [GitHub Actions]({run_url})', '']
    error = scanner_gate(checks, head)
    if error:
        lines += [error, '']
    def safe(value):
        return re.sub(r'[\r\n`<>|@]', ' ', str(value))[:400]
    for failed in data.get('failed_checks', [])[:30]:
        lines.append(f'- Failed: `{safe(failed)}`')
    for note in data.get('notes', [])[:30]:
        lines.append(f'- `{safe(note)}`')
    if not published:
        lines += ['No merge approval was issued.' if english else 'Der er ikke afgivet en merge-godkendelse.', '']
    rows = data.get('checks', [])
    if isinstance(rows, list):
        lines.append(render_execution([r for r in rows[:100] if isinstance(r, dict) and 'tool' in r and 'status' in r]))
    findings = data.get('findings', [])
    if isinstance(findings, list) and findings:
        lines += ['### Scanner findings' if english else '### Scannerfund', '']
        for f in findings[:50]:
            if isinstance(f, dict):
                lines.append(f"- `{safe(f.get('path', ''))}:{safe(f.get('line', ''))}` · `{safe(f.get('tool', ''))}/{safe(f.get('rule', ''))}` — `{safe(f.get('message', ''))}`")
    require_current_head(repo, pr, head)
    upsert_walkthrough(repo, pr, '\n'.join(lines)[:60000])
    return state


def record(pr, head, scanners, ai):
    base = os.environ.get('MANILENS_URL', 'https://manilens.mikkelmanniche.dk').rstrip('/')
    token = oidc_token(base)
    body = json.dumps({'pr': pr, 'head_sha': head, 'scanners': scanners, 'ai': ai}).encode()
    req = urllib.request.Request(base + '/api/run-status', data=body, headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        if res.status != 204:
            raise ValueError('Run status was not accepted')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    for name in ('repo', 'head', 'checks', 'run-url'):
        ap.add_argument('--' + name, required=True)
    ap.add_argument('--pr', type=int, required=True)
    ap.add_argument('--ai', choices=AI_TEXT, required=True)
    ap.add_argument('--published', action='store_true')
    a = ap.parse_args()
    try:
        state = report(a.repo, a.pr, a.head, a.checks, a.ai, a.run_url, a.published)
        record(a.pr, a.head, state, a.ai)
        sys.exit(0 if state != 'error' and a.published else 1)
    except Exception as exc:
        # Never echo HTTP bodies, credentials, source paths or token-bearing URLs.
        print(f'ManiLens status failed: {type(exc).__name__}', file=sys.stderr)
        sys.exit(2)

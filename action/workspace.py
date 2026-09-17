"""Offline, immutable review snapshots. No model calls or remote mutations."""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess

from filter_diff import filter_diff, split


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), '-c', 'core.quotePath=false',
                                    *args], text=True, errors='replace')


def commit(repo, ref):
    if ref.startswith('-'):
        raise ValueError('A revision cannot be an option')
    return git(repo, 'rev-parse', '--verify', ref + '^{commit}').strip()


def layer(path):
    if re.search(r'(^|/)(test[s]?|__tests__)(/|_)|test_|\.test\.|\.spec\.', path):
        return 'Tests'
    if path.endswith(('.md', '.rst', '.txt')):
        return 'Documentation'
    if path.startswith(('.github/', '.manilens/')) or path.endswith(('.json', '.yaml', '.yml', '.toml')):
        return 'Configuration'
    return 'Implementation'


def review_usage(raw_path=None, scope_path=None):
    """Review-type (fuld/inkrementel) og Claude Codes prisestimat til kontosidens review-log.

    Beløbet er det estimat, Claude Code selv skriver (total_cost_usd) — ikke en regning, men det bedste
    mål for kvoteforbruget. Uden scope ved vi ikke, hvad der blev reviewet, og så sendes intet.
    """
    def load(path):
        try:
            data = json.loads(Path(path).read_text()) if path else None
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    mode = load(scope_path).get('mode')
    if mode not in ('full', 'incremental'):
        return None
    raw = load(raw_path)
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
    cost = raw.get('total_cost_usd')
    duration = raw.get('duration_ms')
    return {'mode': mode, 'cost_usd': cost if number(cost) and cost <= 1000 else None,
            'duration_s': int(duration // 1000) if number(duration) and duration <= 86_400_000 else None}


def build_snapshot(repo, base, head='HEAD', result=None, previous=None, repository=None, review=None):
    """Bind a review result explicitly to its SHA. Unbound results are rejected."""
    repo = Path(repo).resolve()
    base_sha, head_sha = commit(repo, base), commit(repo, head)
    merge_base = git(repo, 'merge-base', base_sha, head_sha).strip()
    result = result or {}
    if result and result.get('head_sha') != head_sha:
        raise ValueError('Review result must contain head_sha matching the snapshot')
    filtered, skipped = filter_diff(git(repo, 'diff', '--no-ext-diff', '--no-textconv',
                                      merge_base, head_sha, '--'))
    files = []
    for path, patch in split(filtered):
        files.append({'path': path, 'layer': layer(path), 'patch': patch,
                      'added': sum(x.startswith('+') and not x.startswith('+++') for x in patch.splitlines()),
                      'removed': sum(x.startswith('-') and not x.startswith('---') for x in patch.splitlines())})
    incremental = None
    if previous:
        prev = commit(repo, previous)
        ancestor = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', prev, head_sha],
                                  capture_output=True).returncode
        if ancestor == 0:
            incremental = {'base_sha': prev, 'patch': filter_diff(git(repo, 'diff', '--no-ext-diff',
                                                                   '--no-textconv', prev, head_sha, '--'))[0]}
    # M12: repoet er tjekket ud i mappen "pr", så navnet skal kunne gives udefra.
    return {'schema': 1, 'repository': repository or repo.name, 'base_sha': base_sha, 'merge_base': merge_base,
            'head_sha': head_sha, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'grouping': 'Deterministic file-type groups; not AI semantic layers',
            'files': files, 'excluded': skipped, 'incremental': incremental,
            'review_status': 'imported' if result else 'not_run',
            'findings': result.get('findings', []), 'summary': result.get('summary', ''),
            'checks': result.get('pre_merge_checks', []), 'verdict': result.get('verdict', 'not_run'), 'review': review}


def save_snapshot(snapshot, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2)
    name = snapshot['head_sha'][:12] + '-' + hashlib.sha256(encoded.encode()).hexdigest()[:12] + '.json'
    path = directory / name
    with path.open('x') as stream:
        stream.write(encoded)
    return path


def write_html(snapshot, destination, security=None):
    template = (Path(__file__).parent / 'workspace.html').read_text()
    # JSON script data must not be able to terminate its own HTML element.
    data = json.dumps({'snapshot': snapshot, 'security': security}, ensure_ascii=False)
    data = data.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    logo = Path(__file__).parent.parent / 'assets/brand/manilens-avatar-github.jpg'
    logo_uri = 'data:image/jpeg;base64,' + base64.b64encode(logo.read_bytes()).decode('ascii')
    Path(destination).write_text(template.replace('__MANILENS_DATA__', data).replace('__MANILENS_LOGO__', logo_uri))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--previous', help='Optional previously reviewed commit')
    parser.add_argument('--result', help='Review JSON with an explicit matching head_sha')
    parser.add_argument('--security', help='Security history export JSON')
    parser.add_argument('--out', required=True, help='Private local output directory')
    parser.add_argument('--repository', help='owner/name (standard: mappenavnet)')
    parser.add_argument('--usage-raw', help="Claude Codes råsvar (engine.log.raw.json) med prisestimatet")
    parser.add_argument('--scope', help="incremental.py's .scope.json (fuld eller inkrementel)")
    parser.add_argument('--json-only', action='store_true', help='v2: skriv kun out/snapshot.json (ingen HTML eller logo)')
    args = parser.parse_args()
    read = lambda p: json.loads(Path(p).read_text()) if p else None
    snapshot = build_snapshot(args.repo, args.base, args.head, read(args.result), args.previous, args.repository,
                              review_usage(args.usage_raw, args.scope))
    if args.json_only:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        print(out / 'snapshot.json')
        return
    path = save_snapshot(snapshot, args.out)
    write_html(snapshot, path.with_suffix('.html'), read(args.security))
    print(path.with_suffix('.html'))


if __name__ == '__main__':
    main()

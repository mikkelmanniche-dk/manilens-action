"""A deterministic plan shared by installation, execution and reporting. No PR code runs here."""
import argparse
import json
import hashlib
import os
import re
import subprocess
from pathlib import Path

TOOLS = ('opengrep', 'ruff', 'shellcheck', 'actionlint', 'zizmor', 'hadolint', 'squawk',
         'phpstan', 'html-validate', 'stylelint', 'oxlint', 'golangci-lint', 'deno',
         'trivy', 'osv-scanner', 'gitleaks', 'ast-grep')
LOCKS = {'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'composer.lock', 'poetry.lock',
         'uv.lock', 'go.sum', 'Cargo.lock', 'deno.lock'}
EXCLUDED = {'node_modules', 'vendor', '.git', '.venv', 'venv', 'dist', 'build'}


def tracked(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], timeout=30).decode().split('\0')[:-1]


def versions():
    text = Path(__file__).with_name('install_scanners.sh').read_text()
    values = dict(re.findall(r'^([A-Z_]+)_VERSION=([\w.]+)', text, re.M))
    aliases = {'osv-scanner': 'OSV', 'golangci-lint': 'GOLANGCI_LINT'}
    return {t: values.get(aliases.get(t, t.upper().replace('-', '_')), 'runtime') for t in TOOLS}


def plan(repo, base):
    repo = Path(repo).resolve()
    files = [f for f in tracked(repo, 'ls-files', '-z')
             if not EXCLUDED.intersection(Path(f).parts) and (repo / f).is_file()
             and (repo / f).resolve().is_relative_to(repo)]
    changed = tracked(repo, 'diff', '--name-only', '-z', '--diff-filter=AMRD', base, 'HEAD')
    active = [f for f in changed if (repo / f).is_file()]
    names = {Path(f).name for f in active}
    suffixes = {Path(f).suffix.lower() for f in active}
    selected = {'gitleaks'}
    mapping = {'.py': ('ruff',), '.sh': ('shellcheck',), '.sql': ('squawk',), '.php': ('phpstan',),
               '.html': ('html-validate',), '.htm': ('html-validate',), '.css': ('stylelint',),
               '.scss': ('stylelint',), '.go': ('golangci-lint',)}
    for ext, tools in mapping.items():
        if ext in suffixes:
            selected.update(tools)
    if any(Path(f).suffix.lower() in {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'}
           and not f.startswith('supabase/functions/') for f in active):
        selected.add('oxlint')
    if any(f.startswith('supabase/functions/') and f.endswith('.ts') for f in active):
        selected.add('deno')
    if any(f.startswith('.github/workflows/') and Path(f).suffix in ('.yml', '.yaml') for f in active):
        selected.update(('actionlint', 'zizmor'))
    if any('Dockerfile' in n for n in names):
        selected.add('hadolint')
    if any('Dockerfile' in f or Path(f).suffix in ('.tf', '.yaml', '.yml') for f in files):
        selected.add('trivy')
    if names & LOCKS or any(n.startswith('requirements') and n.endswith('.txt') for n in names):
        selected.add('osv-scanner')
    if suffixes & {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.php', '.go',
                   '.java', '.rb', '.rs', '.c', '.cpp', '.cs', '.sh', '.yaml', '.yml', '.tf', '.html'}:
        selected.update(('opengrep', 'ast-grep'))
    projects = {}
    for f in files:
        p = Path(f)
        kind = {'package.json': 'node', 'composer.json': 'composer', 'pyproject.toml': 'python',
                'setup.py': 'python', 'pytest.ini': 'python'}.get(p.name)
        if kind:
            projects[(str(p.parent), kind)] = {'path': str(p.parent), 'kind': kind}
        elif p.name.startswith('test_') and p.suffix == '.py' and p.parent.name == 'tests':
            parent = str(p.parent.parent)
            projects.setdefault((parent, 'python'), {'path': parent, 'kind': 'python'})
    # Shared configuration can affect any package. Run the small, bounded project set;
    # never silently lose a package through an arbitrary nearest-directory heuristic.
    if len(projects) > 32:
        raise ValueError('More than 32 projects: split the repository checks into explicit CI jobs.')
    runtime = {'node': bool({'html-validate', 'stylelint'} & selected) or any(k == 'node' for _, k in projects),
               'deno': 'deno' in selected, 'php': 'phpstan' in selected or any(k == 'composer' for _, k in projects)}
    return {'schema': 1, 'runtimes': runtime, 'cache_key': hashlib.sha256(' '.join(sorted(selected)).encode()).hexdigest()[:16],
            'head_sha': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
            'changed_files': changed, 'projects': list(projects.values()),
            'tools': [{'tool': t, 'version': versions()[t], 'selected': t in selected,
                       'scope': 'repository' if t in ('trivy', 'osv-scanner') else 'changes',
                       'reason': 'Selected for this change' if t in selected else 'No matching files'} for t in TOOLS]}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('base')
    ap.add_argument('out')
    args = ap.parse_args()
    result = plan(args.repo, args.base)
    Path(args.out).write_text(json.dumps(result, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as fh:
            fh.write(f"cache_key={result['cache_key']}\n")
            for runtime, needed in result['runtimes'].items():
                fh.write(f'{runtime}={str(needed).lower()}\n')

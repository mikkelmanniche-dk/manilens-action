"""Run project-owned checks in the secret-free job, including nested projects."""
import argparse
import fnmatch
import json
import os
import subprocess
import time
from pathlib import Path


def execute(repo, plan, logs, events):
    rows = []
    installed = {}
    def check(project, label, command):
        start = time.monotonic()
        try:
            proc = subprocess.run(command, cwd=repo / project, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, timeout=180)
            code, output = proc.returncode, proc.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, output = 124, str(type(exc).__name__)
        name = f'{project}: {label}'
        (logs / f'project-{len(rows)}.log').write_text(('OK' if code == 0 else f'FEJL (exit {code})') + '\n' + name + '\n' + output[-20000:])
        rows.append({'tool': name, 'version': 'project', 'scope': project, 'status': 'passed' if code == 0 else 'error',
                     'exit_code': code, 'duration_ms': round((time.monotonic() - start) * 1000)})
        return code == 0

    for project in plan['projects']:
        path, kind = project['path'], project['kind']
        root = repo / path
        if os.environ.get('MANILENS_SKIP_REPO_CHECKS'):
            rows.append({'tool': f'{path}: {kind}', 'version': 'project', 'scope': path, 'status': 'skipped',
                         'reason': 'MANILENS_SKIP_REPO_CHECKS', 'duration_ms': 0})
            continue
        if kind in ('node', 'composer'):
            file = root / ('package.json' if kind == 'node' else 'composer.json')
            try:
                manifest = json.loads(file.read_text())
                scripts = manifest.get('scripts', {})
            except (OSError, ValueError, AttributeError):
                check(path, 'invalid manifest', ['false'])
                continue
            names = [n for n in ('lint', 'typecheck', 'test', 'analyse', 'analyze', 'phpstan', 'stan') if n in scripts]
            if kind == 'node' and names:
                install_root = root
                for parent in root.parents:
                    if not parent.is_relative_to(repo):
                        break
                    package = parent / 'package.json'
                    if package.is_file() and (parent / 'package-lock.json').is_file():
                        try:
                            workspaces = json.loads(package.read_text()).get('workspaces', [])
                            if isinstance(workspaces, dict):
                                workspaces = workspaces.get('packages', [])
                            if isinstance(workspaces, list) and any(isinstance(w, str) and fnmatch.fnmatch(root.relative_to(parent).as_posix(), w) for w in workspaces):
                                install_root = parent
                                break
                        except (OSError, ValueError, AttributeError):
                            pass
                if (install_root / 'package-lock.json').exists():
                    install_path = str(install_root.relative_to(repo))
                    if install_path not in installed:
                        installed[install_path] = check(install_path, 'npm ci', ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'])
                    if not installed[install_path]:
                        continue
                elif not (root / 'node_modules').exists() and (manifest.get('dependencies') or manifest.get('devDependencies')):
                    check(path, 'dependencies missing: provide package-lock.json or install in CI', ['false'])
                    continue
            phpunit = kind == 'composer' and 'test' not in names and any((root / p).exists() for p in ('phpunit.xml', 'phpunit.xml.dist'))
            if kind == 'composer' and (names or phpunit):
                if not check(path, 'composer install', ['composer', 'install', '--no-interaction', '--no-progress', '--no-scripts', '--no-plugins']):
                    continue
            for name in names:
                check(path, name, ['npm', 'run', name] if kind == 'node' else ['composer', '--no-interaction', '--no-plugins', 'run-script', name])
            if phpunit:
                check(path, 'phpunit', ['php', 'vendor/bin/phpunit'])
            if not names and not phpunit:
                rows.append({'tool': f'{path}: {kind}', 'version': 'project', 'scope': path, 'status': 'skipped',
                             'reason': 'No recognized check scripts declared', 'duration_ms': 0})
        else:
            pytest = (root / 'pytest.ini').exists() or ('[tool.pytest' in (root / 'pyproject.toml').read_text() if (root / 'pyproject.toml').exists() else False)
            if pytest:
                check(path, 'pytest', ['python3', '-m', 'pytest', '-q'])
            elif (root / 'tests').is_dir():
                check(path, 'unittest', ['python3', '-m', 'unittest', 'discover', '-s', 'tests'])
            else:
                rows.append({'tool': f'{path}: python', 'version': 'project', 'scope': path, 'status': 'skipped',
                             'reason': 'No tests directory or pytest configuration', 'duration_ms': 0})
    with events.open('a') as fh:
        for row in rows:
            fh.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    for arg in ('repo', 'plan', 'logs', 'events'):
        ap.add_argument(arg)
    a = ap.parse_args()
    execute(Path(a.repo), json.loads(Path(a.plan).read_text()), Path(a.logs), Path(a.events))

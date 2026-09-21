import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner_plan
import scanner_runtime
import project_checks
import scan_report
import post_status


class ScannerFlow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        (self.repo / 'README.md').write_text('base\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')

    def test_nested_node_and_composer_are_discovered_and_executed(self):
        for directory, manifest in [('web', 'package.json'), ('server', 'composer.json')]:
            p = self.repo / directory
            p.mkdir()
            (p / manifest).write_text(json.dumps({'scripts': {'test': 'test-command'}}))
            if directory == 'web':
                (p / 'package-lock.json').write_text('{}')
        self.commit()
        plan = scanner_plan.plan(self.repo, self.base)
        self.assertEqual({(p['path'], p['kind']) for p in plan['projects']}, {('web', 'node'), ('server', 'composer')})
        logs = self.root / 'logs'; logs.mkdir()
        commands = []
        def run(cmd, **kwargs):
            commands.append((Path(kwargs['cwd']).name, cmd))
            return subprocess.CompletedProcess(cmd, 0, 'passed')
        with patch('project_checks.subprocess.run', side_effect=run), patch.dict(os.environ, {}, clear=True):
            project_checks.execute(self.repo, plan, logs, self.root / 'events')
        self.assertIn(('web', ['npm', 'run', 'test']), commands)
        self.assertIn(('server', ['composer', '--no-interaction', '--no-plugins', 'run-script', 'test']), commands)
        self.assertEqual(len(list(logs.glob('*.log'))), 4)

    def test_docs_only_does_not_download_language_scanners(self):
        (self.repo / 'README.md').write_text('changed\n')
        self.commit()
        plan = scanner_plan.plan(self.repo, self.base)
        self.assertEqual([t['tool'] for t in plan['tools'] if t['selected']], ['gitleaks'])
        self.assertEqual(plan['projects'], [])

    def test_docs_only_execution_does_not_require_opengrep_rules(self):
        (self.repo / 'README.md').write_text('changed\n')
        self.commit()
        binaries = self.root / 'bin'; binaries.mkdir()
        fake = binaries / 'gitleaks'
        fake.write_text('#!/usr/bin/env python3\nimport pathlib, sys\npathlib.Path(sys.argv[sys.argv.index("--report-path") + 1]).write_text("[]")\n')
        fake.chmod(0o755)
        report = self.root / 'tools.md'
        proc = subprocess.run(['bash', str(Path(__file__).with_name('scanners.sh')), str(self.repo), self.base, str(binaries), str(report)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = json.loads((self.root / 'tools.md.status.json').read_text())
        self.assertTrue(status['complete'], status['notes'])
        self.assertEqual(status['failed_checks'], [])

    def test_deleted_workflow_and_supabase_typescript_match_executor(self):
        wf = self.repo / '.github/workflows/ci.yml'; wf.parent.mkdir(parents=True)
        wf.write_text('name: old\n')
        self.commit(); base = self.git('rev-parse', 'HEAD')
        wf.unlink()
        ts = self.repo / 'supabase/functions/example/index.ts'; ts.parent.mkdir(parents=True)
        ts.write_text('console.log(1)')
        self.commit()
        selected = {t['tool'] for t in scanner_plan.plan(self.repo, base)['tools'] if t['selected']}
        self.assertIn('deno', selected)
        self.assertFalse({'actionlint', 'zizmor', 'oxlint'} & selected)

    def test_deleted_python_does_not_demand_an_unrun_python_scanner(self):
        (self.repo / 'old.py').write_text('print(1)')
        self.commit(); base = self.git('rev-parse', 'HEAD')
        (self.repo / 'old.py').unlink(); self.commit()
        self.assertNotIn('ruff', [t['tool'] for t in scanner_plan.plan(self.repo, base)['tools'] if t['selected']])

    def test_missing_selected_scanner_cannot_look_successful(self):
        (self.repo / 'file.py').write_text('print(1)')
        self.commit()
        plan_path = self.root / 'plan.json'
        plan_path.write_text(json.dumps(scanner_plan.plan(self.repo, self.base)))
        rows, errors = scan_report.execution_status(str(plan_path), str(self.root / 'missing'), [])
        self.assertTrue(errors)
        self.assertEqual(next(r for r in rows if r['tool'] == 'ruff')['status'], 'error')
        self.assertEqual(next(r for r in rows if r['tool'] == 'hadolint')['status'], 'skipped')

    def test_scanner_timeout_records_error(self):
        events = self.root / 'events'
        with patch.dict(os.environ, MANILENS_SCAN_EVENTS=str(events)):
            code = scanner_runtime.run('ruff', {0, 1}, [sys.executable, '-c', 'import time; time.sleep(3)'], timeout=.05)
        self.assertEqual(code, 124)
        self.assertEqual(json.loads(events.read_text())['status'], 'error')

    def test_scanner_only_summary_never_issues_approval_or_reuses_old_head(self):
        checks = self.root / 'status.json'
        checks.write_text(json.dumps({'head_sha': 'a' * 40, 'complete': True, 'notes': [], 'failed_checks': [], 'checks': []}))
        with patch('post_status.require_current_head'), patch('post_status.upsert_walkthrough') as write, patch('post_status.gh.paginate') as read:
            state = post_status.report('owner/repo', 1, 'a' * 40, str(checks), 'missing_token', 'https://github.com/owner/repo/actions/runs/1')
        self.assertEqual(state, 'passed')
        read.assert_not_called()
        self.assertIn('verdict=error', write.call_args.args[2])
        self.assertNotIn('verdict=approve', write.call_args.args[2])
        self.assertEqual(post_status.scanner_state(str(checks), 'b' * 40)[0], 'error')

    def test_installer_empty_plan_makes_no_downloads(self):
        plan = self.root / 'plan.json'; plan.write_text('{"tools": []}')
        out = self.root / 'installed'
        p = subprocess.run(['bash', str(Path(__file__).with_name('install_scanners.sh')), str(out), str(plan)], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(list(out.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from workspace import build_snapshot, review_usage
from incremental import choose_base


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.git('init', '-q'); self.git('config', 'user.email', 'test@example.invalid'); self.git('config', 'user.name', 'Test')
        (self.repo / 'app.py').write_text('x = 1\n')
        self.git('add', '.'); self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'app.py').write_text('x = 2\n')
        self.git('commit', '-qam', 'change')
        self.head = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True)

    def test_repository_name_can_be_given(self):
        self.assertEqual(build_snapshot(self.repo, self.base)['repository'], 'repo')
        self.assertEqual(build_snapshot(self.repo, self.base, repository='kollega/projekt')['repository'], 'kollega/projekt')

    def test_json_only_writes_snapshot_json_without_html(self):
        out = self.root / 'out'
        result = subprocess.run(['python3', str(Path(__file__).parent / 'workspace.py'), '--repo', str(self.repo),
                                 '--base', self.base, '--out', str(out), '--json-only', '--repository', 'kollega/projekt'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['snapshot.json'])
        self.assertEqual(json.loads((out / 'snapshot.json').read_text())['repository'], 'kollega/projekt')

    def test_review_usage_reads_scope_and_claude_cost_estimate(self):
        raw, scope = self.root / 'engine.log.raw.json', self.root / 'diff.raw.patch.scope.json'
        raw.write_text(json.dumps({'total_cost_usd': 1.2345, 'duration_ms': 61999, 'result': 'modeltekst'}))
        scope.write_text(json.dumps({'mode': 'incremental', 'start_sha': self.base}))
        self.assertEqual(review_usage(raw, scope), {'mode': 'incremental', 'cost_usd': 1.2345, 'duration_s': 61})
        self.assertEqual(review_usage(self.root / 'mangler.json', scope), {'mode': 'incremental', 'cost_usd': None, 'duration_s': None})
        self.assertIsNone(review_usage(raw, self.root / 'mangler.json'), 'uden scope ved vi ikke, hvad der blev reviewet')
        raw.write_text(json.dumps({'total_cost_usd': True, 'duration_ms': -5}))
        self.assertEqual(review_usage(raw, scope)['cost_usd'], None, 'bool er ikke et beløb')
        raw.write_text('ikke json')
        self.assertEqual(review_usage(raw, scope)['cost_usd'], None)

    def test_snapshot_carries_review_usage(self):
        self.assertIsNone(build_snapshot(self.repo, self.base)['review'])
        usage = {'mode': 'full', 'cost_usd': 2.0, 'duration_s': 90}
        self.assertEqual(build_snapshot(self.repo, self.base, review=usage)['review'], usage)

    def test_real_diff_and_incremental(self):
        snapshot = build_snapshot(self.repo, self.base, previous=self.base)
        self.assertEqual(snapshot['head_sha'], self.head)
        self.assertIn('+x = 2', snapshot['files'][0]['patch'])
        self.assertEqual(snapshot['review_status'], 'not_run')
        self.assertEqual(snapshot['incremental']['base_sha'], self.base)

    def test_stale_review_rejected(self):
        with self.assertRaisesRegex(ValueError, 'head_sha'):
            build_snapshot(self.repo, self.base, result={'head_sha': self.base, 'findings': []})

    def test_incremental_requires_bot_base_and_ancestor(self):
        body = '<!-- manilens:walkthrough -->\n<!-- manilens:review-base='+self.base+' -->\n<!-- manilens sha='+self.base+' fund=0 verdict=approve -->'
        comment = {'user': {'login': 'manilens[bot]', 'type': 'Bot'}, 'body': body}
        self.assertEqual(choose_base(self.repo,self.base,self.head,[comment]),self.base)
        self.assertIsNone(choose_base(self.repo,self.head,self.head,[comment]))
        comment['user']['type']='User'
        self.assertIsNone(choose_base(self.repo,self.base,self.head,[comment]))

    def test_diverged_previous_falls_back(self):
        self.git('checkout', '-qb', 'other', self.base)
        (self.repo / 'other').write_text('other'); self.git('add', '.'); self.git('commit', '-qm', 'other')
        previous = self.git('rev-parse', 'HEAD').strip()
        snapshot = build_snapshot(self.repo, self.base, self.head, previous=previous)
        self.assertIsNone(snapshot['incremental'])


if __name__ == '__main__':
    unittest.main()

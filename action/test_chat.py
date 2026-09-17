"""Chat: live skriveadgang, lokale kommandoer uden model og v1-tekst for kodehandlinger.  python3 -m unittest -v"""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chat


def event(body='@manilens help', association='COLLABORATOR', login='kollega'):
    return {'comment': {'body': body, 'author_association': association, 'user': {'login': login, 'type': 'User'}, 'id': 5},
            'repository': {'full_name': 'kollega/projekt'}, 'issue': {'number': 12, 'pull_request': {}}}


class Chat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_context(self, ev, permission='write', no_finishing=False, extra_responses=()):
        path = self.dir / 'event.json'
        path.write_text(json.dumps(ev))
        calls = []

        def fake(method, url, body=None):
            calls.append((method, url, body))
            if url.endswith('/permission'):
                return {'permission': permission, 'role_name': permission}
            if url.endswith('/pulls/12') and method == 'GET':
                return {'title': 'T', 'head': {'sha': 'a' * 40, 'repo': {'full_name': 'kollega/projekt'}}, 'base': {'sha': 'b' * 40}}
            return {}

        args = argparse.Namespace(event=str(path), out=str(self.dir / 'context.json'), no_finishing=no_finishing)
        with patch.object(chat.gh, 'request', side_effect=fake), patch.object(chat.gh, 'paginate', return_value=[]), \
                patch.object(chat, 'review_threads', return_value=[]):
            code = chat.cmd_context(args)
        return code, calls

    def test_collaborator_with_only_read_permission_is_refused_without_comment(self):
        with self.assertRaises(PermissionError):
            self.run_context(event(association='COLLABORATOR'), permission='read')

    def test_live_permission_is_checked_for_the_commenter(self):
        code, calls = self.run_context(event())
        self.assertEqual(code, 3)
        self.assertIn(('GET', '/repos/kollega/projekt/collaborators/kollega/permission', None), calls)

    def test_help_and_fix_prompt_are_posted_without_model(self):
        code, calls = self.run_context(event('@manilens help'))
        self.assertEqual(code, 3)
        posts = [c for c in calls if c[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('ManiLens-kommandoer', posts[0][2]['body'])
        self.assertFalse((self.dir / 'context.json').exists(), 'ingen kontekst til model-jobbet')

        code, calls = self.run_context(event('@manilens fix prompt'))
        self.assertEqual(code, 3)
        self.assertEqual([c[0] for c in calls].count('POST'), 1)

    def test_finishing_command_gives_v1_text_for_colleagues(self):
        code, calls = self.run_context(event('@manilens autofix'), no_finishing=True)
        self.assertEqual(code, 3)
        posts = [c for c in calls if c[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('ikke tilgængelige for kolleger i v1', posts[0][2]['body'])
        self.assertNotIn('MANILENS_ENABLE_FINISHING', posts[0][2]['body'])

    def test_help_for_colleagues_does_not_mention_code_actions(self):
        code, calls = self.run_context(event('@manilens help'), no_finishing=True)
        body = [c for c in calls if c[0] == 'POST'][0][2]['body']
        self.assertNotIn('MANILENS_ENABLE_FINISHING', body)
        self.assertNotIn('autofix', body)

    def test_model_mode_writes_context(self):
        code, _ = self.run_context(event('@manilens review'))
        self.assertEqual(code, 0)
        context = json.loads((self.dir / 'context.json').read_text())
        self.assertEqual((context['mode'], context['head'], context['base']), ('review', 'a' * 40, 'b' * 40))

    def test_bot_and_untrusted_association_are_refused_before_any_call(self):
        for ev in (event(association='CONTRIBUTOR'), event(login=chat.BOT)):
            with self.subTest(ev=ev['comment']['user']['login']), self.assertRaises(PermissionError):
                self.run_context(ev)


if __name__ == '__main__':
    unittest.main()

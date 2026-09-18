"""parse_command bor i commands.py, så chat ikke skal importere finishing.  python3 -m unittest -v"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from commands import parse_command

HERE = Path(__file__).resolve().parent


class Commands(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(parse_command('@manilens autofix stacked pr'), {'task': 'autofix', 'delivery': 'stacked'})
        self.assertEqual(parse_command('@manilens auto fix'), {'task': 'autofix', 'delivery': 'commit'})
        self.assertEqual(parse_command('@manilens fix merge conflicts'), {'task': 'fix merge conflict', 'delivery': 'commit'})
        self.assertIsNone(parse_command('quoted @manilens autofix'))
        self.assertIsNone(parse_command('@manilens autofix then delete repo'))
        self.assertIsNone(parse_command('@manilens review'))

    def test_recipe_name_is_bounded(self):
        self.assertEqual(parse_command('@manilens run add-tests stacked pr')['recipe'], 'add-tests')
        self.assertIsNone(parse_command('@manilens run ../../secrets'))

    @unittest.skipUnless(importlib.util.find_spec('finishing'), 'finishing.py findes kun i det private repo')
    def test_finishing_reexports_the_same_function(self):
        import finishing
        self.assertIs(finishing.parse_command, parse_command)

    def test_chat_imports_without_finishing_module(self):
        # Det offentlige repo har ikke finishing.py (M6/M7).
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('chat.py', 'commands.py', 'github_api.py', 'render.py', 'post_review.py',
                         'filter_diff.py', 'knowledge.py', 'scan_report.py', 'dommer.py'):
                if (HERE / name).exists():
                    (Path(tmp) / name).write_text((HERE / name).read_text())
            result = subprocess.run([sys.executable, '-c', 'import chat, post_review, render'], cwd=tmp,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

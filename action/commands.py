"""ManiLens-kommandoer i PR-kommentarer. Delt af chat.py og finishing.py (M7: ingen cirkulær import)."""
import re


def parse_command(body):
    match = re.fullmatch(r'\s*@manilens\s+(autofix|auto-fix|auto fix|generate unit tests|generate docstrings|simplify|fix ci)(\s+stacked pr)?\s*', body, re.I)
    if not match:
        if re.fullmatch(r'\s*@manilens\s+(?:fix|resolve) merge conflicts?\s*', body):
            return {'task': 'fix merge conflict', 'delivery': 'commit'}
        recipe = re.fullmatch(r'\s*@manilens\s+run ([a-zA-Z0-9_-]{1,60})(\s+stacked pr)?\s*', body)
        if recipe:
            return {'task': 'recipe', 'recipe': recipe[1],
                    'delivery': 'stacked' if recipe[2] else 'commit'}
        return None
    task = match[1].lower()
    if task in ('auto-fix', 'auto fix'):
        task = 'autofix'
    return {'task': task, 'delivery': 'stacked' if match[2] else 'commit'}

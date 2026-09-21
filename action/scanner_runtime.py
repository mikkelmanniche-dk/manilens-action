"""Run a scanner with a timeout and retain execution evidence, independently of findings."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def run(tool, accepted, command, directory=None, timeout=300):
    start = time.monotonic()
    reason = None
    try:
        status = subprocess.run(command, cwd=directory, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        status, reason = 124, type(exc).__name__
    row = {'tool': tool, 'version': 'runtime', 'scope': 'project', 'status': 'passed' if status in accepted else 'error',
           'exit_code': status, 'duration_ms': round((time.monotonic() - start) * 1000), 'reason': reason}
    path = os.environ.get('MANILENS_SCAN_EVENTS')
    if path:
        with Path(path).open('a') as fh:
            fh.write(json.dumps(row) + '\n')
    return status


if __name__ == '__main__':
    sys.exit(run(sys.argv[1], {int(v) for v in sys.argv[2].split(',')}, sys.argv[3:]))

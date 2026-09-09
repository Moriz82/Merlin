import json
from pathlib import Path
import subprocess
import sys


def test_synthetic_performance_runner_uses_the_real_local_api():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, 'tests/performance/runner.py',
            '--assets', '25', '--relationships', '100', '--sessions', '2',
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['synthetic'] is True
    assert report['production_api'] is True
    assert report['browser_rendering'] is False
    assert report['network_targets'] is False
    assert report['input'] == {'assets': 25, 'relationships': 100, 'sessions': 2}
    assert report['within_thresholds'] is True

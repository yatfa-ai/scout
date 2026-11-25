"""Shared helpers for the invariant tests.

The tests run short probe subprocesses with an explicitly built environment
instead of importing `socweb.settings` into the test process. Two reasons:
the fail-fast test needs an environment that is *missing* DATABASE_URL, and
the suite itself must then be runnable with no environment configured at all
(`python -m unittest discover`).

No probe opens a database connection: `django.setup()` loads app configs and
model metadata without talking to Postgres.
"""

import os
import subprocess
import sys
from pathlib import Path

# web-django/
PROJECT_DIR = Path(__file__).resolve().parent.parent
# Repo root.
REPO_ROOT = PROJECT_DIR.parent

# Values good enough to let settings resolve; they reach no real database.
DUMMY_DATABASE_URL = 'postgres://probe:probe@127.0.0.1:5432/social'
DUMMY_SECRET = 'invariant-tests-only'

# A path that cannot exist, so a developer's real repo-root `.env` can never
# leak into a probe and decide the outcome of a test.
NO_ENV_FILE = '/nonexistent/scout-invariant-tests.env'


def probe_env(**overrides):
    """A clean environment for a probe: no inherited project configuration."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ('DATABASE_URL', 'SECRET_KEY', 'DEBUG', 'SCOUT_ENV_FILE')
    }
    env['DJANGO_SETTINGS_MODULE'] = 'socweb.settings'
    env['SCOUT_ENV_FILE'] = NO_ENV_FILE
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def run_probe(script, **overrides):
    """Run `script` with the project importable; return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, '-c', script],
        cwd=str(PROJECT_DIR),
        env=probe_env(**overrides),
        capture_output=True,
        text=True,
        timeout=120,
    )


def tracked_files():
    """Paths of the files git actually tracks, relative to the repo root."""
    out = subprocess.run(
        ['git', 'ls-files', '-z'],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.split('\0') if p]

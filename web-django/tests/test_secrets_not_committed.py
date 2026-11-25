"""Invariant (e): `.env` stays git-ignored and no committed file carries secrets.

The repo has ignored `.env` since before this slice; the Django project reads
that same file, so the ignore rule must keep holding. Beyond the ignore, no
tracked file may carry a connection string with real credentials or a literal
Django secret key — the settings module reads both from the environment.
"""

import re
import subprocess
import unittest

from ._probe import REPO_ROOT, tracked_files

# Credential pairs that are demonstrably not secrets: the documented
# placeholder in .env.example, the unreachable pair the invariant probes use,
# and the default local scratch-cluster pair the Node test suites spin up.
# Any other pair in a tracked file is a committed secret.
ALLOWED_CREDENTIALS = {('user', 'password'), ('probe', 'probe'), ('postgres', 'postgres')}

POSTGRES_URL_PATTERN = re.compile(r'postgres(?:ql)?://([^:/@\s]+):([^@/\s]+)@')

# A quoted literal bound to the secret-key name — what a leaked key looks like
# in a tracked file. The settings module binds a call expression instead, so it
# does not match. This file deliberately spells out no such binding anywhere,
# not even as an example in a comment: the scan reads every tracked text file,
# including this one, so a spelled-out example would make the guard report
# itself and go permanently red. `synthesize_literal_secret()` below builds the
# matching text at runtime instead, which keeps the liveness check honest
# without putting a match in the source.
LITERAL_SECRET_PATTERN = re.compile(r'''SECRET_KEY\s*[:=]\s*['"][^'"\n]+['"]''')

TEXT_SUFFIXES = {
    '.py', '.mjs', '.js', '.json', '.md', '.sql', '.txt', '.example', '.yml', '.yaml', '.cfg', '.toml'
}


def tracked_text_files():
    for rel in tracked_files():
        if rel.suffix in TEXT_SUFFIXES or rel.name == '.gitignore':
            yield rel


def synthesize_literal_secret():
    """A leaked secret-key binding, assembled at runtime.

    Composed through a format string so the matching text exists only while the
    test runs: the scan over tracked files never reads it out of this source.
    """
    return '%s = %r' % ('SECRET_KEY', 'django-insecure-synthetic')


def synthesize_leaked_url():
    """A connection string carrying credentials that are not on the allow-list.

    The scheme is composed at runtime for the same reason: written out in full
    it would be matched by the credential scan below.
    """
    return '%s://%s:%s@127.0.0.1:5432/social' % ('postgres', 'leakeduser', 'leakedpass')


class DotEnvStaysIgnoredTest(unittest.TestCase):
    def test_gitignore_still_lists_dot_env(self):
        lines = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
        self.assertIn('.env', [line.strip() for line in lines])

    def test_git_reports_dot_env_as_ignored(self):
        result = subprocess.run(
            ['git', 'check-ignore', '-q', '.env'], cwd=str(REPO_ROOT)
        )
        self.assertEqual(result.returncode, 0, '.env is not ignored by git')

    def test_dot_env_is_not_tracked(self):
        tracked = {str(rel) for rel in tracked_files()}
        self.assertNotIn('.env', tracked)
        self.assertIn('.env.example', tracked, 'the documented template must stay committed')


class NoCommittedSecretsTest(unittest.TestCase):
    def test_the_scan_sees_the_tracked_files(self):
        names = {rel.name for rel in tracked_text_files()}
        self.assertIn('.env.example', names)
        self.assertIn('.gitignore', names)

    def test_no_tracked_file_carries_real_database_credentials(self):
        offenders = []
        for rel in tracked_text_files():
            text = (REPO_ROOT / rel).read_text(encoding='utf-8', errors='replace')
            for match in POSTGRES_URL_PATTERN.finditer(text):
                if match.groups() not in ALLOWED_CREDENTIALS:
                    offenders.append(f'{rel}: {match.group(1)}:***')
        self.assertEqual(offenders, [], f'committed database credentials: {offenders}')

    def test_no_tracked_file_assigns_a_literal_secret_key(self):
        offenders = [
            str(rel)
            for rel in tracked_text_files()
            if LITERAL_SECRET_PATTERN.search(
                (REPO_ROOT / rel).read_text(encoding='utf-8', errors='replace')
            )
        ]
        self.assertEqual(offenders, [], f'committed SECRET_KEY value: {offenders}')

    def test_the_literal_secret_scan_matches_a_synthesized_secret_key(self):
        # Without this, a pattern that had gone dead — reworded, over-escaped,
        # or scanning nothing — would report an empty offender list and read as
        # a pass, which is how a secret-leak guard stops protecting anything.
        self.assertRegex(synthesize_literal_secret(), LITERAL_SECRET_PATTERN)

    def test_the_credential_scan_matches_a_synthesized_leaked_pair(self):
        match = POSTGRES_URL_PATTERN.search(synthesize_leaked_url())
        self.assertIsNotNone(match, 'the credential pattern no longer matches a connection string')
        self.assertNotIn(
            match.groups(),
            ALLOWED_CREDENTIALS,
            'the allow-list must not excuse a synthesized leaked pair',
        )

    def test_the_env_template_documents_both_variables_without_values(self):
        text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertRegex(text, r'(?m)^DATABASE_URL=')
        self.assertRegex(text, r'(?m)^SECRET_KEY=\s*$')


if __name__ == '__main__':
    unittest.main()

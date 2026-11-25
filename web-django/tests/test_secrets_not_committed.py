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

# A literal assignment such as SECRET_KEY = 'django-insecure-...'. The settings
# module assigns a call expression instead, which does not match.
LITERAL_SECRET_PATTERN = re.compile(r'''SECRET_KEY\s*[:=]\s*['"][^'"\n]+['"]''')

TEXT_SUFFIXES = {
    '.py', '.mjs', '.js', '.json', '.md', '.sql', '.txt', '.example', '.yml', '.yaml', '.cfg', '.toml'
}


def tracked_text_files():
    for rel in tracked_files():
        if rel.suffix in TEXT_SUFFIXES or rel.name == '.gitignore':
            yield rel


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

    def test_the_env_template_documents_both_variables_without_values(self):
        text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertRegex(text, r'(?m)^DATABASE_URL=')
        self.assertRegex(text, r'(?m)^SECRET_KEY=\s*$')


if __name__ == '__main__':
    unittest.main()

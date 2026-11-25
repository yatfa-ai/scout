"""Invariant (d): no HTTP client is importable from this project's own code.

Zero outbound social-network calls is a binding invariant of Yatfa Scout: the
parity target `web/server.mjs` imports only `node:http/fs/path/url` and the pg
helper, so nothing in it can reach a social network. This is the grep-level
equivalent for the Python side — an import statement for an HTTP client
anywhere under `web-django/`, outside a virtualenv, fails the suite.

The forbidden names appear below as data, never as import statements, so this
guard cannot match itself: the pattern anchors on `import`/`from`.
"""

import re
import unittest

from ._probe import PROJECT_DIR

FORBIDDEN_MODULES = ['requests', 'httpx', 'urllib', 'urllib3', 'aiohttp', 'http.client']

# `import requests`, `import requests as r`, `from httpx import Client`,
# `from urllib.parse import quote` — all matched; a mention inside a string or
# a comment that does not start a statement is not.
IMPORT_PATTERN = re.compile(
    r'^\s*(?:import|from)\s+(?:' + '|'.join(re.escape(m) for m in FORBIDDEN_MODULES) + r')\b',
    re.MULTILINE,
)

# Virtualenv and build directories: not this project's code.
SKIP_DIRS = {'.venv', 'venv', 'env', 'site-packages', '__pycache__', '.mypy_cache', '.pytest_cache'}


def project_python_files():
    for path in sorted(PROJECT_DIR.rglob('*.py')):
        if SKIP_DIRS.intersection(path.relative_to(PROJECT_DIR).parts):
            continue
        yield path


class NoOutboundHttpClientTest(unittest.TestCase):
    def test_the_scan_sees_the_projects_own_files(self):
        names = {p.name for p in project_python_files()}
        self.assertIn('settings.py', names)
        self.assertIn('models.py', names)

    def test_no_python_file_imports_an_http_client(self):
        offenders = []
        for path in project_python_files():
            source = path.read_text(encoding='utf-8', errors='replace')
            for match in IMPORT_PATTERN.finditer(source):
                line = source[: match.start()].count('\n') + 1
                offenders.append(f'{path.relative_to(PROJECT_DIR)}:{line} {match.group(0).strip()}')
        self.assertEqual(
            offenders,
            [],
            'the app must make no outbound calls; found HTTP-client imports: ' + '; '.join(offenders),
        )

    def test_requirements_declare_no_http_client(self):
        requirements = (PROJECT_DIR / 'requirements.txt').read_text(encoding='utf-8')
        declared = [
            line.split('#', 1)[0].strip().lower()
            for line in requirements.splitlines()
            if line.split('#', 1)[0].strip()
        ]
        for module in FORBIDDEN_MODULES:
            for line in declared:
                self.assertFalse(
                    line.startswith(module),
                    f'{module} must not be a dependency: {line}',
                )


if __name__ == '__main__':
    unittest.main()

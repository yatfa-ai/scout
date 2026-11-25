"""Invariants (a) and (b): the environment contract and the bind surface.

`lib/db.mjs` throws when no DATABASE_URL is reachable. These tests pin that the
Django settings behave the same way instead of silently falling back to a
hardcoded connection string or secret key, and that the host allowlist stays on
the loopback interface, matching `server.mjs`'s `.listen(PORT, '127.0.0.1')`.
"""

import json
import unittest

from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

READ_DATABASE = """
import json
from django.conf import settings
db = settings.DATABASES['default']
print(json.dumps({'engine': db['ENGINE'], 'name': db['NAME'], 'host': db['HOST']}))
"""

READ_HOSTS = """
import json
from django.conf import settings
print(json.dumps({
    'allowed_hosts': settings.ALLOWED_HOSTS,
    'debug': settings.DEBUG,
    'use_tz': settings.USE_TZ,
}))
"""


class DatabaseUrlFromEnvironmentTest(unittest.TestCase):
    def test_resolves_database_url_from_the_environment(self):
        result = run_probe(
            READ_DATABASE, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        db = json.loads(result.stdout)
        self.assertEqual(db['engine'], 'django.db.backends.postgresql')
        self.assertEqual(db['name'], 'social')
        self.assertEqual(db['host'], '127.0.0.1')

    def test_refuses_to_boot_without_database_url(self):
        result = run_probe(READ_DATABASE, DATABASE_URL=None, SECRET_KEY=DUMMY_SECRET)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('DATABASE_URL', result.stderr)

    def test_refuses_to_boot_without_secret_key(self):
        result = run_probe(
            READ_DATABASE, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=None
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('SECRET_KEY', result.stderr)


class AllowedHostsStayOnLoopbackTest(unittest.TestCase):
    def setUp(self):
        result = run_probe(
            READ_HOSTS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.settings = json.loads(result.stdout)

    def test_allowed_hosts_is_exactly_the_two_loopback_names(self):
        self.assertEqual(self.settings['allowed_hosts'], ['127.0.0.1', 'localhost'])

    def test_allowed_hosts_contains_no_public_or_wildcard_host(self):
        for host in self.settings['allowed_hosts']:
            self.assertNotIn('*', host, f'{host} would accept any Host header')
            self.assertNotIn('.', host.replace('127.0.0.1', ''), f'{host} is not loopback')

    def test_debug_defaults_off_when_the_environment_does_not_ask_for_it(self):
        self.assertFalse(self.settings['debug'])

    def test_timezone_support_is_on(self):
        self.assertTrue(self.settings['use_tz'])


if __name__ == '__main__':
    unittest.main()

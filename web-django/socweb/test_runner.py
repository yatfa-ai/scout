"""Test runner that never creates a database.

Every model in this project is unmanaged and adopts the existing `social`
database. The invariant tests of this slice are pure settings/metadata checks,
so the runner skips database setup entirely: `manage.py test` can never create,
migrate or drop anything.
"""

from django.test.runner import DiscoverRunner


class NoDatabaseTestRunner(DiscoverRunner):
    """DiscoverRunner with database setup and teardown removed."""

    def setup_databases(self, **kwargs):
        return []

    def teardown_databases(self, old_config, **kwargs):
        return None

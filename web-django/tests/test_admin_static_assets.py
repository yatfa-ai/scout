"""The admin's own CSS and JS are served in the mode the project pins: `DEBUG` off.

The admin is the ops surface (knowledge scout-2), and every asset it references
lives in `django/contrib/admin/static`. Nothing in this repo serves that tree:
there is no project `static/` directory, no `STATIC_ROOT` and no
`STATICFILES_DIRS`, and `django.contrib.staticfiles` only serves assets by
itself when `DEBUG` is on — which the settings deliberately default to off and
`tests/test_settings_env.py` pins there. So before this slice all 23 assets the
admin references answered 404: the surface rendered unstyled with its
JavaScript dead (the nav sidebar toggle, the changelist filters and actions,
the add-form widgets). `socweb/urls.py` now routes `/static/…` to the
staticfiles view with `insecure: True`.

⚠️ The failure mode this module exists to catch is silent. The view opens with
`if not settings.DEBUG and not insecure: raise Http404`, so a static route that
*looks* right but omits `{'insecure': True}` answers a plain 404 — no
exception, no log line, byte-identical to having no route at all. That is why
every example here fetches an asset and asserts on the response: reading the
urlconf cannot tell the fix from its mutant. For the same reason the probe
asserts `DEBUG is False` before fetching anything — with `DEBUG` on the view
serves the assets regardless, so a 200 measured there would prove nothing about
the configuration the operator actually runs.

No database: a static request never reaches a model, and `Client()` sends no
session cookie, so these examples belong in the cheap half of the suite
(`tests/_probe.py`) and pass with no `.env`, no `DEBUG` and no
`TEST_DATABASE_URL`.

The second claim is the scope of what the route exposes. The finders resolve
exactly one location — `AppDirectoriesFinder` over `django.contrib.admin`, the
`social` app shipping no `static/` of its own — so the served tree is Django's
admin asset directory and nothing else: a missing path is a 404, `/static/` is
not a directory index, a traversal attempt is rejected, and `finders.find()`
cannot reach the repo-root `.env`.
"""

import json
import unittest

from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

# One representative asset of each kind, chosen so both halves of the defect
# are covered: the two stylesheets are on every admin page, `core.js` is the
# shared changelist/widget helper and `nav_sidebar.js` is the toggle whose
# death is the visible half of the JavaScript breakage.
ASSETS = [
    '/static/admin/css/base.css',
    '/static/admin/css/changelists.css',
    '/static/admin/js/core.js',
    '/static/admin/js/nav_sidebar.js',
]

# A name no asset directory holds, so a route that answered 200 for everything
# would fail here.
MISSING_ASSET = '/static/admin/css/no-such-stylesheet-scout-75.css'

# Not an asset request at all: the prefix itself, and two ways of asking for
# the repo-root `.env` the settings read.
STATIC_PREFIX = '/static/'
DOTENV_BY_TRAVERSAL = '/static/../../.env'
DOTENV_BY_NAME = '/static/.env'

# Resolvable through the finders (it is Django's own), and not.
FOUND_BY_FINDERS = 'admin/css/base.css'
NOT_FOUND_BY_FINDERS = '.env'

# --- the probe -------------------------------------------------------------

# `setup_test_environment()` is what puts `testserver` into ALLOWED_HOSTS,
# which the settings deliberately keep to the two loopback names.
FETCH_THE_ADMIN_ASSETS = """
import json
import django
django.setup()
from django.conf import settings
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.staticfiles import finders
from django.test import Client

PATHS = %r

client = Client()
fetched = {}
for path in PATHS:
    response = client.get(path)
    fetched[path] = {
        'status': response.status_code,
        'content_type': response.headers.get('Content-Type'),
        'bytes': len(response.getvalue()) if response.status_code == 200 else 0,
    }

print(json.dumps({
    # Asserted by the suite: with DEBUG on, a 200 below would prove nothing.
    'debug': settings.DEBUG,
    'static_url': settings.STATIC_URL,
    'static_root': settings.STATIC_ROOT,
    'staticfiles_dirs': list(settings.STATICFILES_DIRS),
    'fetched': fetched,
    'finds_admin_asset': finders.find(%r) is not None,
    'finds_dotenv': finders.find(%r),
}))
""" % (
    ASSETS + [MISSING_ASSET, STATIC_PREFIX, DOTENV_BY_TRAVERSAL, DOTENV_BY_NAME],
    FOUND_BY_FINDERS,
    NOT_FOUND_BY_FINDERS,
)


class AdminStaticAssetsTest(unittest.TestCase):
    """Fetched responses, not configuration: the mutant is invisible in the urlconf."""

    @classmethod
    def setUpClass(cls):
        result = run_probe(
            FETCH_THE_ADMIN_ASSETS,
            DATABASE_URL=DUMMY_DATABASE_URL,
            SECRET_KEY=DUMMY_SECRET,
        )
        assert result.returncode == 0, result.stderr
        cls.probe = json.loads(result.stdout)

    def test_the_assets_are_fetched_with_debug_off(self):
        # The premise of every other example here: the gap exists only with
        # DEBUG off, so a run that quietly turned it on would prove nothing.
        self.assertIs(
            self.probe['debug'],
            False,
            'the probe must measure the mode the project pins',
        )

    def test_each_kind_of_admin_asset_answers_200_with_a_content_type(self):
        for path in ASSETS:
            with self.subTest(asset=path):
                fetched = self.probe['fetched'][path]
                self.assertEqual(
                    fetched['status'],
                    200,
                    f'{path} is not served — the admin renders without it',
                )
                self.assertTrue(fetched['content_type'], f'{path} has no content type')
                self.assertGreater(fetched['bytes'], 0, f'{path} is served empty')

    def test_a_missing_static_file_is_still_a_404(self):
        self.assertEqual(self.probe['fetched'][MISSING_ASSET]['status'], 404)

    def test_the_static_prefix_is_not_a_directory_index(self):
        self.assertEqual(self.probe['fetched'][STATIC_PREFIX]['status'], 404)

    def test_the_repo_dotenv_is_not_reachable_through_the_static_route(self):
        # Both ways of asking: climbing out of the served tree, and asking for
        # the name directly inside it.
        self.assertNotEqual(self.probe['fetched'][DOTENV_BY_TRAVERSAL]['status'], 200)
        self.assertNotEqual(self.probe['fetched'][DOTENV_BY_NAME]['status'], 200)

    def test_the_served_tree_is_djangos_admin_assets_and_nothing_else(self):
        # What the route exposes is decided by the finders, so it is pinned
        # here: one location, because no project directory is configured.
        self.assertEqual(self.probe['staticfiles_dirs'], [])
        self.assertIsNone(self.probe['static_root'])
        self.assertTrue(
            self.probe['finds_admin_asset'],
            'the admin asset directory must be the one location the finders resolve',
        )
        self.assertIsNone(
            self.probe['finds_dotenv'],
            'no finder may resolve the repo-root `.env`',
        )

    def test_the_static_url_is_unchanged(self):
        # The route derives its prefix from this setting the way
        # `staticfiles_urlpatterns()` does, so changing it here would move the
        # route with it — and the admin's templates render this prefix.
        self.assertEqual(self.probe['static_url'], '/static/')


if __name__ == '__main__':
    unittest.main()

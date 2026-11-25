"""The front door: `/` lands on the queue, and both surfaces link to each other.

`web/server.mjs` answers `GET /` with the whole screen (knowledge scout-2: the
Node app is the parity reference), and `web/index.html` carries a six-tab nav
over the panes. Before this slice the Django side had neither: `GET /` was a
404 for every caller, the admin's «Открыть сайт» — the only outbound link the
admin offers — pointed at that 404, and `/queue/` linked nowhere. So the three
claims pinned here are *navigation* claims: where a URL lands, and which
targets the rendered pages actually offer.

Two halves, for two kinds of claim (the precedent of
`tests/test_admin_read_surface.py`):

  1. metadata, no database — `admin.site.site_url`, and that the root route
     exists and resolves to a redirect at the `queue` view name. Read in a
     probe subprocess, where `django.setup()` needs no Postgres.
  2. rendered pages, against a scratch database built from `schema.sql` by
     `tests/_db.py` — because "the operator lands on the queue" and "the tab
     answers 200" are claims about responses, not about configuration. With no
     reachable Postgres these skip honestly and the suite stays green.

⚠️ Every claim about a link is asserted on an extracted `href`, never on a
substring of the page: `/queue/` appears in this template's own form actions
(`/queue/approve/` and friends), and `/admin/` is a prefix of every URL the
admin renders, so grepping the HTML would pass with no nav and no site link at
all — the exact false pass this module exists to rule out. The nav is read as
label → href pairs, so the tab-to-target mapping is pinned rather than the
mere presence of six anchors.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_front_door_test_{os.getpid()}'

OPERATOR = 'front-door-tests-operator'

QUEUE_URL = '/queue/'
ADMIN_INDEX = '/admin/'
LOGIN_NEXT_QUEUE = f'/admin/login/?next={QUEUE_URL}'

# The nav row of `web/index.html`, tab by tab, with the Django target each one
# gets. «Ушло» is the ledger because `sent()` there renders `S.ledger`, and
# «Ответы нам» carries the admin's own filter value so the tab lands on the
# set that pane shows (`direction = 'in'`) rather than on every interaction.
NAV = {
    'Ушло': '/admin/social/ledger/',
    'Лиды': '/admin/social/leads/',
    'Вопросы рынка': '/admin/social/questions/',
    'Ответы нам': '/admin/social/interactions/?direction__exact=in',
    'Люди': '/admin/social/people/',
    'Админка': ADMIN_INDEX,
}

# «Ждёт тебя» is this page: the current tab, and deliberately not a link.
CURRENT_TAB = 'Ждёт тебя'

INBOUND_TAB = 'Ответы нам'
INTERACTIONS_UNFILTERED = '/admin/social/interactions/'

# Admin pages the site link must appear on — the index and one changelist, so
# the claim is "every admin page" rather than "the index page".
ADMIN_PAGES = [ADMIN_INDEX, '/admin/social/people/']

# --- fixture ---------------------------------------------------------------

# Two waiting and one approved draft: the badge must show the waiting ones, so
# a badge rendering `drafts.count()` or `approved_count` cannot pass.
WAITING_BODIES = ['первый черновик ждёт', 'второй черновик ждёт']
APPROVED_BODY = 'этот уже утверждён'
EXPECTED_WAITING = len(WAITING_BODIES)

PEOPLE = [
    ('li:anna', 'Анна Петрова', 'linkedin'),
    ('li:boris', 'Борис Смирнов', 'linkedin'),
]

# Two inbound, one outbound: the «Ответы нам» link must drop the outbound one.
INTERACTIONS = [
    ('i-anna-in', 'in', 'li:anna', 'нам ответили'),
    ('i-boris-in', 'in', 'li:boris', 'и ещё один ответ'),
    ('i-anna-out', 'out', 'li:anna', 'наш комментарий под их постом'),
]
EXPECTED_INBOUND = sum(1 for row in INTERACTIONS if row[1] == 'in')

# --- probes ----------------------------------------------------------------

READ_ROUTING_METADATA = """
import json
import django
django.setup()
from django.contrib import admin
from django.urls import resolve, reverse

match = resolve('/')
print(json.dumps({
    'site_url': admin.site.site_url,
    'home_url': reverse('home'),
    'queue_url': reverse('queue'),
    'root_view': match.func.__name__,
    'root_view_class': getattr(match.func, 'view_class', type(None)).__name__,
    'root_pattern_name': getattr(match.func, 'view_initkwargs', {}).get('pattern_name'),
    'root_route_name': match.url_name,
}))
"""

# `setup_test_environment()` is what puts `testserver` into ALLOWED_HOSTS,
# which the settings deliberately keep to the two loopback names.
HTTP_PRELUDE = """
import json
import os
import re
import html as entities
import django
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.auth.models import User
from django.test import Client

OPERATOR = 'front-door-tests-operator'

ANCHOR = re.compile(r'<a\\s[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S)
ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TAG = re.compile(r'<[^>]+>')


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username=OPERATOR))
    return client


def text_of(fragment):
    return entities.unescape(TAG.sub(' ', fragment)).strip()


def links_of(body):
    # href values, never page text: '/queue/' also occurs in this template's
    # form actions and '/admin/' is a prefix of every admin URL.
    return [(entities.unescape(href), text_of(label)) for href, label in ANCHOR.findall(body)]


def nav_of(body):
    if '<nav>' not in body:
        return None
    return body.split('<nav>')[1].split('</nav>')[0]


def body_rows(body):
    if '<tbody>' not in body:
        return 0
    table = body.split('<tbody>')[1].split('</tbody>')[0]
    return len(ROW.findall(table))
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='front-door-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

# Where `/` lands, for staff and for a visitor who is not logged in.
FOLLOW_THE_FRONT_DOOR = HTTP_PRELUDE + """
staff = operator_client()
hop = staff.get('/')
landing = staff.get('/', follow=True)
anonymous = Client().get('/', follow=True)

print(json.dumps({
    'staff_status': hop.status_code,
    'staff_location': hop.headers.get('Location'),
    'staff_chain': [[url, code] for url, code in landing.redirect_chain],
    'staff_final': landing.status_code,
    'anonymous_chain': [[url, code] for url, code in anonymous.redirect_chain],
    'anonymous_final': anonymous.status_code,
    # The login form the anonymous visitor is handed, so «returns to the
    # queue» is read off the page rather than off the redirect alone.
    'anonymous_next': re.findall(r'name="next"\\s+value="([^"]*)"',
                                 anonymous.content.decode('utf-8')),
}))
"""

# The admin's own outbound link, on more than one admin page.
ADMIN_SITE_LINK = HTTP_PRELUDE + """
client = operator_client()
out = {}
for page in json.loads(os.environ['PROBE_PAGES']):
    response = client.get(page)
    out[page] = {
        'status': response.status_code,
        'links': links_of(response.content.decode('utf-8')),
    }
print(json.dumps(out))
"""

# The queue's nav row, and where each of its links actually goes.
QUEUE_NAV = HTTP_PRELUDE + """
client = operator_client()
response = client.get('/queue/')
body = response.content.decode('utf-8')
nav = nav_of(body)

visited = {}
for href, _label in links_of(nav or ''):
    page = client.get(href)
    visited[href] = {'status': page.status_code,
                     'rows': body_rows(page.content.decode('utf-8'))}

unfiltered = client.get(os.environ['PROBE_UNFILTERED'])

print(json.dumps({
    'status': response.status_code,
    'nav_present': nav is not None,
    'nav_links': links_of(nav or ''),
    'nav_text': text_of(nav or ''),
    'badge': re.findall(r'<span class="badge">([^<]*)</span>', nav or ''),
    'visited': visited,
    'unfiltered_rows': body_rows(unfiltered.content.decode('utf-8')),
}))
"""

# --- scratch database lifecycle -------------------------------------------

database = None


def setUpModule():
    global database
    database = _db.scratch_database(SCRATCH_DB)
    if database is None:
        return
    try:
        _seed()
        # The queue and the admin are both staff-only, so the rendered half
        # needs real users and sessions. `migrate` creates only the
        # `django_*`/`auth_*` tables — every social model is unmanaged — so it
        # cannot touch the schema just loaded.
        result = run_probe(
            MIGRATE_AND_CREATE_OPERATOR, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET
        )
        assert result.returncode == 0, result.stderr
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _seed():
    for body in WAITING_BODIES:
        database.sql(
            """insert into drafts (network, kind, status, body)
               values ('linkedin', 'comment', 'waiting', %s)""",
            (body,),
        )
    database.sql(
        """insert into drafts (network, kind, status, body, approved_at)
           values ('x', 'post', 'approved', %s, '2026-11-20 10:00')""",
        (APPROVED_BODY,),
    )
    for person_id, name, network in PEOPLE:
        database.sql(
            """insert into people (id, name, network, profile, first_seen)
               values (%s, %s, %s, %s, '2026-11-01')""",
            (person_id, name, network, f'https://example.test/{person_id}'),
        )
    for interaction_id, direction, person, text in INTERACTIONS:
        database.sql(
            """insert into interactions (id, ts, network, direction, kind, person, text)
               values (%s, '2026-11-20 10:00', 'linkedin', %s, 'reply', %s, %s)""",
            (interaction_id, direction, person, text),
        )
    database.sql(
        """insert into questions (id, date, network, asker, to_us, question, status)
           values ('q-roi', '2026-11-20', 'linkedin', 'li:anna', true,
                   'Как считать окупаемость агентов?', 'открыт')"""
    )
    database.sql(
        "insert into leads (person, stage, next_step) values ('li:anna', 'talking', 'написать')"
    )
    database.sql(
        """insert into ledger (at, network, topic, url, outcome)
           values ('2026-11-19 12:00', 'linkedin', 'очередь утверждений',
                   'https://example.test/post', 'отклик: три комментария')"""
    )


# --- the metadata half -----------------------------------------------------


class RoutingMetadataTest(unittest.TestCase):
    """No database: the root route and the admin's site link, as configured."""

    def setUp(self):
        result = run_probe(
            READ_ROUTING_METADATA, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.routing = json.loads(result.stdout)

    def test_the_admin_site_link_points_at_the_queue(self):
        # Unset, `site_url` defaults to '/', which is what made «Открыть сайт»
        # a link to a 404 on every admin page.
        self.assertEqual(self.routing['site_url'], QUEUE_URL)

    def test_the_root_url_is_routed_at_all(self):
        self.assertEqual(self.routing['home_url'], '/')
        self.assertEqual(self.routing['root_route_name'], 'home')

    def test_the_root_route_redirects_to_the_queue_by_name(self):
        self.assertEqual(self.routing['root_view_class'], 'RedirectView')
        self.assertEqual(
            self.routing['root_pattern_name'],
            'queue',
            'the redirect must resolve the queue by name, not by a hardcoded path',
        )

    def test_the_queue_is_still_served_at_its_own_url(self):
        # The front door is a second way in, not a move: `/queue/` stays the
        # canonical URL, which is why `/` redirects rather than renders.
        self.assertEqual(self.routing['queue_url'], QUEUE_URL)


# --- the rendered half -----------------------------------------------------


class RenderedTestCase(unittest.TestCase):
    """Shared probe handling: skip without a database."""

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class FrontDoorTest(RenderedTestCase):
    """`GET /` lands the operator on the queue, logging in on the way if needed."""

    def setUp(self):
        super().setUp()
        self.landing = self.probe(FOLLOW_THE_FRONT_DOOR)

    def test_staff_opening_the_root_url_are_redirected_to_the_queue(self):
        self.assertEqual(self.landing['staff_status'], 302)
        self.assertEqual(self.landing['staff_location'], QUEUE_URL)

    def test_staff_following_the_root_url_reach_the_queue_itself(self):
        self.assertEqual(self.landing['staff_chain'], [[QUEUE_URL, 302]])
        self.assertEqual(self.landing['staff_final'], 200)

    def test_an_anonymous_visitor_ends_at_the_login_and_comes_back_to_the_queue(self):
        self.assertEqual(
            self.landing['anonymous_chain'],
            [[QUEUE_URL, 302], [LOGIN_NEXT_QUEUE, 302]],
            'the front door must also be the login door',
        )
        self.assertEqual(self.landing['anonymous_final'], 200)
        self.assertEqual(
            self.landing['anonymous_next'],
            [QUEUE_URL],
            'the login form must return the visitor to the queue',
        )


class AdminSiteLinkTest(RenderedTestCase):
    """«Открыть сайт» is a working link on an admin page, not a 404."""

    def setUp(self):
        super().setUp()
        self.pages = self.probe(ADMIN_SITE_LINK, PROBE_PAGES=json.dumps(ADMIN_PAGES))

    def test_every_admin_page_offers_the_queue_as_its_site_link(self):
        for page in ADMIN_PAGES:
            with self.subTest(page=page):
                self.assertEqual(self.pages[page]['status'], 200)
                hrefs = [href for href, _label in self.pages[page]['links']]
                self.assertIn(
                    QUEUE_URL,
                    hrefs,
                    f'{page} renders no link to the queue: {hrefs}',
                )

    def test_the_site_link_is_labelled_as_the_way_out_to_the_site(self):
        links = dict(
            (label, href) for href, label in self.pages[ADMIN_INDEX]['links'] if label
        )
        self.assertIn('Открыть сайт', links)
        self.assertEqual(links['Открыть сайт'], QUEUE_URL)


class QueueNavTest(RenderedTestCase):
    """The queue carries the Node screen's six tabs, and each one works."""

    def setUp(self):
        super().setUp()
        self.nav = self.probe(QUEUE_NAV, PROBE_UNFILTERED=INTERACTIONS_UNFILTERED)
        self.assertEqual(self.nav['status'], 200)
        self.assertTrue(self.nav['nav_present'], 'the queue page renders no nav row')
        self.links = dict((label, href) for href, label in self.nav['nav_links'])

    def test_the_nav_offers_exactly_the_tabs_with_exactly_their_targets(self):
        # The mapping, not the count: «Ушло» must be the ledger and «Ответы
        # нам» must carry its filter, or the tab lands on the wrong set.
        self.assertEqual(self.links, NAV)

    def test_the_current_tab_is_shown_but_is_not_a_link(self):
        self.assertIn(CURRENT_TAB, self.nav['nav_text'])
        self.assertNotIn(
            CURRENT_TAB,
            self.links,
            'the page the operator is on must not link to itself',
        )

    def test_every_nav_link_answers_200(self):
        for label, href in NAV.items():
            with self.subTest(tab=label):
                self.assertEqual(
                    self.nav['visited'][href]['status'],
                    200,
                    f'«{label}» leads to a broken page',
                )

    def test_the_inbound_tab_arrives_filtered_to_the_replies_to_us(self):
        filtered = self.nav['visited'][NAV[INBOUND_TAB]]['rows']
        self.assertEqual(
            filtered,
            EXPECTED_INBOUND,
            'the tab must land on `direction = in`, not on every interaction',
        )
        # Positive control: the fixture holds an outbound row, so the equal
        # count above is narrowing rather than an unfiltered list that happens
        # to be the same size.
        self.assertEqual(self.nav['unfiltered_rows'], len(INTERACTIONS))
        self.assertLess(filtered, self.nav['unfiltered_rows'])

    def test_the_waiting_badge_matches_the_number_of_waiting_drafts(self):
        counted = database.sql(
            "select count(*) as n from drafts where status = 'waiting'"
        )[0]['n']
        self.assertEqual(counted, EXPECTED_WAITING, 'the fixture should hold this count')
        self.assertEqual(
            self.nav['badge'],
            [str(EXPECTED_WAITING)],
            'the badge must show the waiting drafts, not every open draft',
        )


class ScratchSchemaTest(RenderedTestCase):
    """Positive control: the harness really did build and seed the schema."""

    def test_the_fixture_holds_the_rows_the_examples_read(self):
        counts = {
            'drafts': len(WAITING_BODIES) + 1,
            'people': len(PEOPLE),
            'interactions': len(INTERACTIONS),
            'questions': 1,
            'leads': 1,
            'ledger': 1,
        }
        for table, expected in counts.items():
            with self.subTest(table=table):
                self.assertEqual(
                    database.sql(f'select count(*) as n from {table}')[0]['n'], expected
                )


if __name__ == '__main__':
    unittest.main()

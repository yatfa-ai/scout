"""The queue card's «куда»: an `href` only when `target_url` really is one.

`drafts.target_url` is the column that answers "where does this comment go",
and `schema.sql` declares it free-form `text` with no CHECK. Nothing narrows it
on the way in either, and the project's own backup shows what it actually
holds: three of its eleven drafts store a note such as
`(ссылку достану при отправке)` there. The card used to mint its anchor from
whatever was stored, so prose became a link that resolves inside the app and
404s the operator away from the queue, and a `javascript:` value became a live
`href` on the page whose forms carry the approval CSRF token.

`social/links.py` is the single rule — the one `social/admin.py`'s five link
columns have applied since slice 10, moved out of that module so this page and
any later one import it instead of re-deciding it. These examples pin the queue
half: the admin half is `ClickThroughTest` in `tests/test_admin_read_surface.py`,
unchanged by this slice and the reason it is not repeated here.

⚠️ Every claim is asserted on an EXTRACTED `href`, never on page text. The
stored value appears on the card as text by design — that is the whole point of
the text fallback — so grepping the HTML for `javascript:` or for a prose
target says nothing about whether it became an attribute, which is the only
question that matters. The examples therefore go through `cards()`, which cuts
the page into one record per draft and pulls the `href`s out of each, and
`test_the_extractor_sees_a_real_anchor` is its positive control: without a card
that *does* carry a link, every "no anchor" claim below would pass against a
page the extractor failed to read at all.

The fixture stores one `waiting` draft per URL shape: five that must become
an anchor (the two accepted schemes, an upper-case scheme, a padded value, an
embedded quote) and seven that must not (the real production note, a
`javascript:` value, a protocol-relative one, a `mailto:`, a bare host,
whitespace only, and nothing stored at all) — so one render answers every
shape. Each draft's body is unique, which is how a card is tied back to the
value it was built from.

The database is a scratch one built from `schema.sql` by `tests/_db.py` (see
its docstring for why neither test runner can host this), with the Django
tables `migrate` creates on top so the page's login gate has real users and
sessions, exactly as `tests/test_queue_transitions.py` does it. With no
reachable Postgres the rendering examples skip honestly; the two source-level
classes need no database and never skip.
"""

import html
import json
import os
import re
import unittest

from . import _db
from ._probe import DUMMY_SECRET, PROJECT_DIR, run_probe

SCRATCH_DB = f'scout_django_queue_link_test_{os.getpid()}'

OPERATOR = 'queue-link-tests-operator'

# Every column the approve example compares, so "untouched" is a claim about
# the row rather than about a return value.
DRAFT_COLUMNS = 'id, network, kind, status, body, target_url, approved_at, sent_at, skip_reason'

# --- the URL shapes under test ---------------------------------------------

# key -> (stored target_url, the href the card must carry, or None for "no
# anchor at all"). The expected href is written UNESCAPED and compared against
# the unescaped attribute, so `&` and `"` need no second spelling here.
SHAPES = {
    # Accepted: both schemes, in the shapes the data really carries.
    'http': ('http://linkedin.example/posts/plain', 'http://linkedin.example/posts/plain'),
    'https': ('https://linkedin.example/posts/secure', 'https://linkedin.example/posts/secure'),
    # The scheme is matched case-insensitively and the value is returned as
    # stored: drop the case-folding half of the match and this stops being a
    # link; lower the value itself and the path silently changes.
    'upper_scheme': (
        'HTTPS://LinkedIn.example/Posts/Shouted',
        'HTTPS://LinkedIn.example/Posts/Shouted',
    ),
    # A stored value with surrounding whitespace is a working address; the
    # attribute carries the trimmed one.
    'padded': (
        '  https://linkedin.example/posts/padded  ',
        'https://linkedin.example/posts/padded',
    ),
    # A real link whose value tries to close the attribute and open an event
    # handler. It stays one link, escaped — see its own example.
    'quoted': (
        'https://linkedin.example/posts/q?x=" onmouseover="alert(1)',
        'https://linkedin.example/posts/q?x=" onmouseover="alert(1)',
    ),
    # Refused. The first is the value three of the eleven drafts in the
    # project's own backup actually store.
    'prose': ('(ссылку достану при отправке через поиск)', None),
    'script': ('javascript:alert(document.cookie)', None),
    'protocol_relative': ('//evil.example/steal', None),
    'mailto': ('mailto:roman@example.com', None),
    'bare_host': ('linkedin.com/in/roman', None),
    # Whitespace only: unchecked this links to the current page.
    'blank': ('   ', None),
    # Nothing stored at all, the shape the card has always handled.
    'empty': (None, None),
}

# The values that must not become an attribute but must still be readable on
# the card: the approver needs to see the note, in full.
TEXT_FALLBACK_KEYS = ('prose', 'script', 'protocol_relative', 'mailto', 'bare_host')

# --- page extraction -------------------------------------------------------

# One `<article class="card">` per draft; the meta row is where «куда» lives,
# and the hidden id input is how a card is tied to its draft.
CARD = re.compile(r'<article class="card.*?</article>', re.S)
META = re.compile(r'<div class="meta">(.*?)</div>', re.S)
HREF = re.compile(r'href="([^"]*)"')
CARD_DRAFT_ID = re.compile(r'name="id" value="(\d+)"')

MIGRATE_AND_CREATE_OPERATOR = f"""
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username={OPERATOR!r},
    defaults={{'is_staff': True, 'is_active': True}},
)
print('ok')
"""

RENDER_QUEUE = f"""
import json
import django
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

client = Client()
client.force_login(User.objects.get(username={OPERATOR!r}))
response = client.get(reverse('queue'))
print(json.dumps({{
    'status': response.status_code,
    'html': response.content.decode('utf-8'),
}}))
"""

APPROVE_THROUGH_THE_PAGE = f"""
import json
import os
import django
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

client = Client()
client.force_login(User.objects.get(username={OPERATOR!r}))
response = client.post(reverse('queue_approve'), {{'id': os.environ['PROBE_ID']}})
print(json.dumps({{
    'status': response.status_code,
    'location': response.headers.get('Location'),
}}))
"""

# The scheme rule's own module, imported directly rather than through a probe:
# it must stand on its own, with no Django settings and no database behind it.
WEB_HREF_CASES = [
    ('http://host/x', 'http://host/x'),
    ('https://host/x', 'https://host/x'),
    ('HTTP://Host/X', 'HTTP://Host/X'),
    ('HtTpS://Host/X?A=b', 'HtTpS://Host/X?A=b'),
    ('  https://host/x  ', 'https://host/x'),
    ('javascript:alert(1)', None),
    ('JavaScript:alert(1)', None),
    ('//host/x', None),
    ('mailto:a@b.c', None),
    ('ftp://host/x', None),
    ('host/x', None),
    ('httpx://host/x', None),
    # `http`/`https` without the separator is not a scheme either.
    ('https:/host/x', None),
    ('', None),
    ('   ', None),
    ('\t\n', None),
    (None, None),
]

SOCIAL_DIR = PROJECT_DIR / 'social'

# Module-level so the scratch database, its schema and `migrate` are paid for
# once; None when no Postgres answered, which the rendering examples skip on.
database = None
ids = {}
baseline = []


def setUpModule():
    global database, baseline
    database = _db.scratch_database(SCRATCH_DB)
    if database is None:
        return
    try:
        _seed()
        # The page's login gate needs real users and sessions; `migrate`
        # creates only the `django_*`/`auth_*` tables, never a social one
        # (every model is unmanaged), so it cannot touch the schema just
        # loaded from schema.sql.
        result = run_probe(
            MIGRATE_AND_CREATE_OPERATOR, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET
        )
        assert result.returncode == 0, result.stderr
        baseline = _drafts()
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _seed():
    for key, (stored, _expected) in SHAPES.items():
        row = database.sql(
            """insert into drafts (network, kind, status, body, target_url)
               values ('linkedin', 'comment', 'waiting', %s, %s)
               returning id""",
            (_body_for(key), stored),
        )[0]
        ids[key] = row['id']


def _body_for(key):
    """A unique body per card, so a card can be read back to its URL shape."""
    return f'черновик для проверки ссылки: {key}'


def _drafts():
    return database.sql(f'select {DRAFT_COLUMNS} from drafts order by id')


class QueueCardLinkTestCase(unittest.TestCase):
    """Shared fixture handling: skip without a database, render once."""

    _page = None

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def page(self):
        if QueueCardLinkTestCase._page is None:
            rendered = self.probe(RENDER_QUEUE)
            self.assertEqual(rendered['status'], 200)
            QueueCardLinkTestCase._page = rendered['html']
        return QueueCardLinkTestCase._page

    def cards(self):
        """`{shape key: card markup}` for every draft the fixture stored.

        Keyed through the card's hidden draft id, not through its text: a card
        is matched to the value it was built from by identity.
        """
        by_id = {}
        for markup in CARD.findall(self.page()):
            found = set(CARD_DRAFT_ID.findall(markup))
            self.assertEqual(
                len(found), 1, f'a card must carry exactly one draft id: {markup!r}'
            )
            by_id[int(found.pop())] = markup
        cards = {key: by_id[draft_id] for key, draft_id in ids.items() if draft_id in by_id}
        self.assertEqual(
            sorted(cards),
            sorted(ids),
            'every seeded waiting draft must render a card the extractor can find',
        )
        return cards

    def card(self, key):
        return self.cards()[key]

    def meta_of(self, key):
        """The card's `.meta` row — where «куда» sits — as markup."""
        found = META.findall(self.card(key))
        self.assertEqual(len(found), 1, f'the {key} card must carry one meta row')
        return found[0]

    def hrefs_in(self, markup):
        """Every `href` in `markup`, unescaped, so one spelling is enough."""
        return [html.unescape(value) for value in HREF.findall(markup)]


class QueueCardLinkTest(QueueCardLinkTestCase):
    def test_the_extractor_sees_a_real_anchor(self):
        # Positive control for every "no anchor" claim below: with a page the
        # extractor cannot read, or a card that renders no link at all, those
        # claims would pass vacuously.
        self.assertEqual(
            self.hrefs_in(self.meta_of('http')),
            [SHAPES['http'][1]],
            'the card for a plain http target must carry exactly its own link',
        )
        self.assertIn('куда', self.meta_of('http'))

    def test_only_an_http_or_https_target_url_becomes_an_href(self):
        for key, (stored, expected) in SHAPES.items():
            with self.subTest(shape=key, stored=stored):
                self.assertEqual(
                    self.hrefs_in(self.meta_of(key)),
                    [] if expected is None else [expected],
                    f'{key}: target_url {stored!r} must render '
                    + ('no anchor' if expected is None else f'href {expected!r}'),
                )

    def test_a_refused_value_reaches_no_href_anywhere_on_the_card(self):
        # Not just "not in the meta row": a waiting card carries three forms,
        # one of them a collapsed editor, and the claim is that the value is
        # an attribute nowhere on it. `blank` is left out because an empty
        # needle proves nothing — the table example above pins that shape.
        for key in TEXT_FALLBACK_KEYS:
            with self.subTest(shape=key):
                stored = SHAPES[key][0]
                attributes = ''.join(self.hrefs_in(self.card(key)))
                self.assertNotIn(stored.strip(), attributes, f'{key} must reach no attribute')

    def test_no_href_on_the_page_carries_a_scheme_the_rule_refuses(self):
        # The page's own links are the nav's `{% url %}` paths plus the cards'
        # «куда»: a relative path or one of the two accepted schemes, and
        # nothing else. `javascript:` is named explicitly because it is the
        # one shape that is live rather than merely wrong.
        for href in self.hrefs_in(self.page()):
            with self.subTest(href=href):
                lowered = href.lower()
                self.assertFalse(lowered.startswith('javascript:'), 'no live script href')
                self.assertTrue(
                    href.startswith('/') or lowered.startswith(('http://', 'https://')),
                    f'{href!r} is neither an in-app path nor an http(s) address',
                )

    def test_a_real_link_keeps_the_label_and_the_new_tab_shape_it_had(self):
        for key in ('http', 'https', 'upper_scheme', 'padded'):
            with self.subTest(shape=key):
                meta = self.meta_of(key)
                self.assertIn(f'href="{SHAPES[key][1]}" target="_blank" rel="noopener"', meta)
                self.assertIn('>куда</a>', meta)

    def test_a_refused_value_is_shown_as_text_on_the_card_in_full(self):
        # The approver still needs the note: «куда» is gone, the text is not.
        # Asserted as the card's own text node rather than as "somewhere in the
        # markup" — on the unchecked page the value sits in the `href`, which
        # is exactly the shape this must not accept as "readable". In full,
        # unlike the admin's truncated cell: the card is a reading surface and
        # `(ссылку достану при отправке)` is information, not noise.
        for key in TEXT_FALLBACK_KEYS:
            with self.subTest(shape=key):
                stored = html.escape(SHAPES[key][0], quote=False)
                self.assertIn(
                    f'<span class="tag">{stored}</span>',
                    self.meta_of(key),
                    'a value that is not a link must be shown as text instead',
                )

    def test_a_real_url_is_not_repeated_as_text_beside_its_own_link(self):
        for key in ('http', 'https'):
            with self.subTest(shape=key):
                meta = self.meta_of(key)
                self.assertEqual(
                    meta.count(SHAPES[key][1]),
                    1,
                    'a link renders once, as the anchor — not again as a caption',
                )

    def test_nothing_is_rendered_for_a_draft_with_no_target_url(self):
        meta = self.meta_of('empty')
        self.assertEqual(self.hrefs_in(meta), [])
        self.assertNotIn('куда', meta)
        self.assertNotIn('None', meta, 'a NULL target_url must not print as "None"')

    def test_an_embedded_quote_cannot_open_an_event_handler(self):
        markup = self.meta_of('quoted')
        raw = HREF.findall(markup)
        self.assertEqual(len(raw), 1, f'the card must carry exactly one href: {markup!r}')
        self.assertIn('&quot;', raw[0], 'the stored quote belongs inside the attribute, escaped')
        self.assertNotIn(
            'onmouseover="', markup, 'an embedded quote must not open an event handler'
        )
        self.assertIn('>куда</a>', markup, 'and the value is still a link')


class QueueApprovalUnaffectedTest(QueueCardLinkTestCase):
    """The link rendering changed; the write path did not."""

    def tearDown(self):
        if database is None:
            return
        for row in baseline:
            database.sql(
                """update drafts set status = %s, approved_at = %s where id = %s""",
                (row['status'], row['approved_at'], row['id']),
            )

    def test_approving_moves_exactly_that_row_and_redirects_to_the_queue(self):
        before = _drafts()
        waiting = [row for row in before if row['status'] == 'waiting']
        self.assertGreater(
            len(waiting), 1, 'siblings are the control a mass update would sweep along'
        )

        result = self.probe(APPROVE_THROUGH_THE_PAGE, PROBE_ID=str(ids['prose']))

        self.assertEqual(result['status'], 302)
        self.assertEqual(result['location'], '/queue/')
        after = _drafts()
        self.assertEqual([row['id'] for row in after], [row['id'] for row in before])
        for was, now in zip(before, after):
            if now['id'] == ids['prose']:
                self.assertEqual(now['status'], 'approved')
                self.assertIsNotNone(now['approved_at'])
                continue
            with self.subTest(draft=now['id']):
                self.assertEqual(now, was, f'draft {now["id"]} must be untouched')


class SchemeRuleTest(unittest.TestCase):
    """`social/links.py`'s own contract. No database, no Django settings.

    Imported into the test process directly rather than run in a probe: the
    module is deliberately dependency-free, and importing it with nothing
    configured is what pins that.
    """

    def test_web_href_accepts_only_an_http_or_https_value_and_keeps_its_case(self):
        from social.links import web_href

        for stored, expected in WEB_HREF_CASES:
            with self.subTest(stored=stored):
                self.assertEqual(web_href(stored), expected)

    def test_the_two_schemes_are_the_whole_allow_list(self):
        from social.links import LINK_SCHEMES

        self.assertEqual(LINK_SCHEMES, ('http://', 'https://'))


class SingleSourcedRuleTest(unittest.TestCase):
    """One rule, one module: both render sites import it.

    The defect this slice fixes was not a missing check but a check that lived
    inside `admin.py`, where the queue could not reach it. A second definition
    anywhere under `social/` is that defect returning, so it is pinned at the
    source level rather than left to the rendering examples, which a drifted
    copy could still satisfy on the day it was written.
    """

    def source_of(self, name):
        return (SOCIAL_DIR / name).read_text(encoding='utf-8')

    def test_the_rule_lives_in_its_own_module(self):
        source = self.source_of('links.py')
        self.assertIn('LINK_SCHEMES = ', source)
        self.assertIn('def web_href(', source)

    def test_no_other_module_defines_the_scheme_list(self):
        definitions = [
            path.name
            for path in sorted(SOCIAL_DIR.rglob('*.py'))
            if re.search(r'^LINK_SCHEMES\s*=', path.read_text(encoding='utf-8'), re.MULTILINE)
        ]
        self.assertEqual(
            definitions, ['links.py'], 'the scheme rule must be defined exactly once'
        )

    def test_both_render_sites_import_the_rule(self):
        imports_the_rule = re.compile(r'^from \.links import .*\bweb_href\b', re.MULTILINE)
        for name in ('admin.py', 'views.py'):
            with self.subTest(module=name):
                self.assertTrue(
                    imports_the_rule.search(self.source_of(name)),
                    f'social/{name} must take the rule from social/links.py',
                )

    def test_the_queue_template_anchors_on_the_checked_value(self):
        template = (SOCIAL_DIR / 'templates' / 'social' / 'queue.html').read_text(
            encoding='utf-8'
        )
        self.assertIn('href="{{ draft.target_href }}"', template)
        self.assertNotIn(
            'href="{{ draft.target_url }}"',
            template,
            'the raw column must not be minted into an href again',
        )


if __name__ == '__main__':
    unittest.main()

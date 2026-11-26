"""The queue shows the body it stores, blank first line included.

`social/templates/social/queue.html` carries the draft body twice: in the
`<pre class="text">` the approver reads, and in the `<textarea name="body">`
«Правка» hands back. HTML tree construction drops a single newline immediately
after the start tag of a `pre` or a `textarea`, so with the body written flush
against either start tag a body whose own first character is a newline loses it
*in the parser*: the card shows a text that is not the stored one, the editor
hands back a text that is not the stored one, and `social/queue.edit` then
stores that shortened text. The defect `tests/test_queue_body_line_endings.py`
closed for line endings — a save that edited nothing rewrites the draft —
arriving from the render side instead of the submit side.

The trigger is one ordinary gesture: an operator who types a blank line above
the text in «Правка» and saves stores a body beginning with exactly one LF
(`queue._lf` collapses the payload's CRLF and is deliberately not `.strip()`).
From that moment the blank line is invisible on the page and the next save
deletes it. The fix is Django's own widget shape — one newline between the
start tag and the value, never two, since the parser drops exactly one and a
second would invent a blank first line on every ordinary body
(`django/forms/templates/django/forms/widgets/textarea.html`).

It matters outside this app: the publisher in the companion repo `scout-ext`
reads the stored body byte-for-byte — `senders/linkedin-connect.mjs` inserts
it, reads it back and refuses the send on a mismatch, and both connect senders
throw above the note limit — and `drafts` has no version history, so the body
is the only copy of the text.

Two points of mechanics, neither of them obvious:

  * **A card is located by its hidden `id`, never by its body.** A card
    rendering a body that is *not* the stored one must still be found. This is
    why the line-endings module cannot see this defect: its
    `_rendered_textarea` matches the recovered value against the *expected*
    body and otherwise raises "no editor on /queue/ rendered …", so a wrong
    render is reported as a missing card rather than as a wrong body.
  * **The one-leading-newline rule is applied by hand.** Python's stdlib
    `html.parser` returns `'\\nX'` for both `<pre>\\nX</pre>` and
    `<textarea>\\nX</textarea>`: dropping the newline is browser
    tree-construction behaviour, not parsing, so `tests/_formfields.py` and
    the line-endings module both strip it themselves. These examples do the
    same — at most one newline, `'\\r\\n'` before `'\\n'`.

Seven of the twelve examples are controls, because the risk here is a fix that
over-reaches by one character: an ordinary body must render, hand back and
round-trip byte-identically, an empty body must still render an empty editor
(an empty body is a real send mode on the publisher side — an invitation
*without* a note), `edit`'s status gate must still refuse a draft the operator
can no longer act on, a `sent` draft must stay off the page, and the fixture
pins its own shapes so the other five cannot pass for the wrong reason.

The harness is the one `tests/test_queue_body_line_endings.py` uses: a scratch
database built from the repo-root `schema.sql` by `tests/_db.py`, the Django
tables `migrate` creates on top so the page's login gate has real users, and
every example run in a probe subprocess per `tests/_probe.py`. With no
reachable Postgres the examples skip honestly.
"""

import html
import json
import os
import re
import unittest

from . import _db
from ._probe import DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_queue_lead_nl_test_{os.getpid()}'

# Every column `edit` can write, plus the ones a mutant could write by
# accident: a comparison over this shape is what "untouched" means.
DRAFT_COLUMNS = 'id, network, kind, status, body, approved_at, sent_at, sent_url, skip_reason'

OPERATOR = 'queue-leading-newline-tests-operator'

# The body an operator produces by typing a blank line above the text: exactly
# one LF, which is the one character HTML drops after a start tag.
LEADING_BODY = '\nпервая строка пустая, текст идёт ниже'

# Two LFs, so "the parser drops one" is told apart from "the template drops
# them all": a fix that swallowed the whole run would pass with one newline.
DOUBLE_BODY = '\n\nдве пустые строки сверху, потом текст'

# An ordinary multi-line body, no leading whitespace: the shape every control
# protects, and the one a second newline in the template would damage.
PLAIN_BODY = 'обычный черновик\n\nсо вторым абзацем'

SENT_URL = 'https://linkedin.example/posts/sent-one'

# key -> (status, kind, body)
FIXTURE = {
    'leading': ('waiting', 'comment', LEADING_BODY),
    'double': ('waiting', 'comment', DOUBLE_BODY),
    'plain': ('waiting', 'comment', PLAIN_BODY),
    'empty': ('waiting', 'connect', ''),
    'sent': ('sent', 'comment', 'уже отправлено'),
}

# The card and the two elements that carry the body, as the template renders
# them. Sliced out of the page rather than assumed, so an example asserts what
# the operator was actually shown.
CARD_OPEN = '<article class="card'
PRE_OPEN = '<pre class="text">'
PRE_CLOSE = '</pre>'
TEXTAREA_OPEN = '<textarea name="body" aria-label="текст черновика">'
TEXTAREA_CLOSE = '</textarea>'

# The hidden field every card's forms carry; the only way to identify a card
# whose rendered body may be wrong.
CARD_ID = re.compile(r'name="id" value="(\d+)"')

HTTP_PRELUDE = """
import json
import os
import django
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username=%r))
    return client
""" % OPERATOR

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username=%r,
    defaults={'is_staff': True, 'is_active': True},
)
print('ok')
""" % OPERATOR

RENDER_QUEUE = HTTP_PRELUDE + """
response = operator_client().get(reverse('queue'))
print(json.dumps({'status': response.status_code, 'html': response.content.decode('utf-8')}))
"""

POST_EDIT = HTTP_PRELUDE + """
payload = json.loads(os.environ['PROBE_PAYLOAD'])
response = operator_client().post(
    reverse('queue_edit'), {'id': str(payload['id']), 'body': payload['value']}
)
print(json.dumps({'status': response.status_code, 'location': response.headers.get('Location')}))
"""

# The ORM probe, for the one example that must reach `queue.edit` past the
# page: the status gate's refusal is not visible in a redirect.
TRANSITION = """
import json
import os
import django
django.setup()
from social import queue

payload = json.loads(os.environ['PROBE_PAYLOAD'])
row = queue.edit(payload['id'], payload['value'])
print(json.dumps(None if row is None else {'status': row.status, 'body': row.body}))
"""

# Module-level so the scratch database, its schema and `migrate` are paid for
# once; None when no Postgres answered, which every example then skips on.
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
        # The page's login gate needs real users and sessions; `migrate` creates
        # only the `django_*`/`auth_*` tables, never a social one (every model is
        # unmanaged), so it cannot touch the schema just loaded from schema.sql.
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
    for key, (status, kind, body) in FIXTURE.items():
        row = database.sql(
            """insert into drafts (network, kind, status, body, target_note)
               values ('linkedin', %s, %s, %s, %s)
               returning id""",
            (kind, status, body, f'черновик {key}'),
        )[0]
        ids[key] = row['id']

    # Nothing on this surface may produce a `sent` row, so its stamps are set
    # with SQL.
    database.sql(
        'update drafts set approved_at = now(), sent_at = now(), sent_url = %s where id = %s',
        (SENT_URL, ids['sent']),
    )


def _drafts():
    return database.sql(f'select {DRAFT_COLUMNS} from drafts order by id')


def _cards(page_html):
    """Every card on the page, keyed by the draft id its hidden field carries.

    Keyed by id and not by body on purpose: a card rendering the wrong body
    must still be found, or this suite would report the defect as a missing
    card. Each `<article class="card">` carries at least one form, and every
    one of those carries `<input type="hidden" name="id">`.
    """
    cards = {}
    for chunk in page_html.split(CARD_OPEN)[1:]:
        match = CARD_ID.search(chunk)
        assert match is not None, 'a card must carry the hidden id its forms post'
        cards[int(match.group(1))] = chunk
    return cards


def _element_value(chunk, open_tag, close_tag):
    """What a browser reads out of one element of a card.

    Two HTML rules, both of them the browser's rather than the parser's:
    character references are resolved, and a single newline directly after the
    start tag is dropped — at most one, which is exactly what makes a second
    newline in the template a defect of its own.
    """
    assert open_tag in chunk, f'the card must render {open_tag}'
    value = html.unescape(chunk.split(open_tag)[1].split(close_tag)[0])
    for newline in ('\r\n', '\n'):
        if value.startswith(newline):
            return value[len(newline):]
    return value


class QueueBodyLeadingNewlineTestCase(unittest.TestCase):
    """Shared fixture handling: skip without a database, restore after each example."""

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def tearDown(self):
        if database is None:
            return
        for row in baseline:
            database.sql(
                """update drafts set status = %s, body = %s, approved_at = %s, sent_at = %s,
                                     sent_url = %s, skip_reason = %s
                   where id = %s""",
                (
                    row['status'],
                    row['body'],
                    row['approved_at'],
                    row['sent_at'],
                    row['sent_url'],
                    row['skip_reason'],
                    row['id'],
                ),
            )

    # --- helpers ---------------------------------------------------------

    def row(self, draft_id):
        rows = database.sql(f'select {DRAFT_COLUMNS} from drafts where id = %s', (draft_id,))
        self.assertEqual(len(rows), 1, f'draft {draft_id} must exist')
        return rows[0]

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def cards(self):
        page = self.probe(RENDER_QUEUE)
        self.assertEqual(page['status'], 200)
        return _cards(page['html'])

    def card(self, key):
        cards = self.cards()
        self.assertIn(ids[key], cards, f'the {key} draft must have a card on /queue/')
        return cards[ids[key]]

    def shown(self, key):
        """The text the approver reads for `key`'s draft."""
        return _element_value(self.card(key), PRE_OPEN, PRE_CLOSE)

    def handed_back(self, key):
        """The text «Правка» hands back for `key`'s draft."""
        return _element_value(self.card(key), TEXTAREA_OPEN, TEXTAREA_CLOSE)

    def save_unchanged(self, key):
        """Open «Правка» and save without typing; return the row afterwards.

        The one thing a browser adds is written in by hand: the HTML form
        payload rule rewrites every newline in a submitted textarea to CRLF,
        which `queue._lf` collapses back. `django.test.Client` is not a browser
        and normalises nothing, so stating the rule here is what makes this an
        operator's no-op save rather than a hand-built payload.
        """
        submitted = self.handed_back(key).replace('\n', '\r\n')
        result = self.probe(
            POST_EDIT, PROBE_PAYLOAD=json.dumps({'id': ids[key], 'value': submitted})
        )
        self.assertEqual(result['status'], 302)
        self.assertEqual(result['location'], '/queue/')
        return self.row(ids[key])


class BlankFirstLineSurvivesTest(QueueBodyLeadingNewlineTestCase):
    """The five examples the defect fails: the page carries the stored body."""

    def test_a_body_beginning_with_a_newline_is_shown_with_its_blank_first_line(self):
        shown = self.shown('leading')

        self.assertEqual(shown, LEADING_BODY)
        self.assertTrue(shown.startswith('\n'), 'the card must show the blank first line')

    def test_a_body_beginning_with_a_newline_is_handed_back_by_the_editor_unchanged(self):
        handed_back = self.handed_back('leading')

        self.assertEqual(handed_back, LEADING_BODY)
        self.assertTrue(
            handed_back.startswith('\n'), '«Правка» must hand back the blank first line'
        )

    def test_a_body_beginning_with_two_newlines_is_shown_with_both(self):
        # One newline is dropped by the parser, so a template that swallowed
        # the whole run instead of exactly one would still pass the example
        # above and fail here.
        self.assertEqual(self.shown('double'), DOUBLE_BODY)

    def test_a_body_beginning_with_two_newlines_is_handed_back_with_both(self):
        self.assertEqual(self.handed_back('double'), DOUBLE_BODY)

    def test_saving_the_editor_unchanged_does_not_delete_the_blank_first_line(self):
        before = self.row(ids['leading'])

        after = self.save_unchanged('leading')

        self.assertEqual(
            after['body'], before['body'], 'a save that edited nothing must change nothing'
        )
        self.assertEqual(
            len(after['body'].encode('utf-8')),
            len(before['body'].encode('utf-8')),
            'the stored body must be byte-identical, not merely similar',
        )
        self.assertTrue(after['body'].startswith('\n'), 'the blank first line must survive')


class OrdinaryBodyIsUntouchedTest(QueueBodyLeadingNewlineTestCase):
    """Controls: one newline, never two — no body gains a blank first line."""

    def test_an_ordinary_body_is_shown_without_a_blank_first_line(self):
        shown = self.shown('plain')

        self.assertEqual(shown, PLAIN_BODY)
        self.assertFalse(shown.startswith('\n'), 'the card must not invent a blank first line')

    def test_an_ordinary_body_is_handed_back_by_the_editor_unchanged(self):
        handed_back = self.handed_back('plain')

        self.assertEqual(handed_back, PLAIN_BODY)
        self.assertFalse(
            handed_back.startswith('\n'), '«Правка» must not invent a blank first line'
        )

    def test_saving_an_ordinary_body_unchanged_leaves_it_byte_identical(self):
        before = self.row(ids['plain'])

        after = self.save_unchanged('plain')

        self.assertEqual(after['body'], before['body'])
        self.assertEqual(
            len(after['body'].encode('utf-8')), len(before['body'].encode('utf-8'))
        )

    def test_an_empty_body_still_renders_an_empty_card_and_editor(self):
        # An empty body is a meaningful state, not a mistake to repair:
        # `scout-ext`'s `senders/linkedin-connect.mjs` branches on it to send
        # an invitation without a note. A second newline in the template would
        # turn it into a one-newline body here.
        self.assertEqual(self.shown('empty'), '')
        self.assertEqual(self.handed_back('empty'), '')


class RenderChangeIsScopedTest(QueueBodyLeadingNewlineTestCase):
    """Controls: the page's own rules are untouched by a whitespace change."""

    def test_edit_still_refuses_a_draft_the_operator_can_no_longer_act_on(self):
        before = self.row(ids['sent'])

        result = self.probe(
            TRANSITION, PROBE_PAYLOAD=json.dumps({'id': ids['sent'], 'value': LEADING_BODY})
        )

        self.assertIsNone(result, 'edit on a sent draft must refuse the write')
        self.assertEqual(self.row(ids['sent']), before, 'and leave the row untouched')

    def test_a_sent_draft_has_no_card_on_the_queue(self):
        # The page lists `waiting` and `approved` only; without this control a
        # card lookup by id could not tell "off the page by design" from "the
        # card went missing".
        cards = self.cards()

        self.assertNotIn(ids['sent'], cards, 'a sent draft is not an open draft')
        self.assertEqual(
            sorted(cards),
            sorted(ids[key] for key in FIXTURE if key != 'sent'),
            'every open draft, and only those, has a card',
        )


class FixtureTest(QueueBodyLeadingNewlineTestCase):
    """Positive control: the seeded bodies really are the shapes claimed.

    Without it, a fixture that lost its leading newlines — or arrived carrying
    CRLF — would let every example above pass for the wrong reason. The schema
    itself needs no separate control: `_seed` inserts into `drafts`, so a
    database that did not load `schema.sql` fails the whole module at setup.
    """

    def test_the_fixture_stores_the_leading_newlines_it_claims_lf_only(self):
        self.assertEqual(sorted(ids), sorted(FIXTURE))
        self.assertEqual(len(_drafts()), len(FIXTURE))
        self.assertEqual(self.row(ids['leading'])['body'], LEADING_BODY)
        self.assertEqual(self.row(ids['double'])['body'], DOUBLE_BODY)
        self.assertEqual(self.row(ids['plain'])['body'], PLAIN_BODY)
        self.assertEqual(self.row(ids['empty'])['body'], '')
        for key in ('leading', 'double', 'plain'):
            self.assertNotIn('\r', self.row(ids[key])['body'], f'{key} must be stored LF-only')


if __name__ == '__main__':
    unittest.main()

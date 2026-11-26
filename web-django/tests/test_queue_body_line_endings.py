"""The line endings «Правка» stores, pinned against a real database.

`social/queue.edit` is the only write path to `drafts.body` in this app: the
queue's `<textarea name="body">` is the one multi-line form input under
`web-django/`, and `DraftsAdmin` is view-only. The HTML form payload rule
rewrites every newline in a submitted `<textarea>` to CRLF, while the DOM
`value` getter the Node screen posts (`web/index.html`'s `saveEdit`) is
LF-only — so storing the payload raw rewrote a draft on a save that edited
nothing, which is what these examples exist to stop.

It matters outside this app: the publisher in the companion repo `scout-ext`
compares the stored body against a textarea's DOM value character-for-character
(`senders/linkedin-connect.mjs` refuses the send on a mismatch) and against a
length limit a carriage return per line can push it over, so a CR in the column
is a refused send rather than a cosmetic difference. `drafts` has no version
history and the body is the only copy of the text.

**The CRLF payload here is written into the fixture deliberately.**
`django.test.Client` is not a browser and performs no newline normalisation, so
these examples state the browser's contribution instead of relying on it. The
arm proving a real browser does rewrite the payload is a manual measurement
(recorded on ticket scout-91: the project's own approved 946-char, 0-CR thread
came back 960 bytes with 14 carriage returns after an unedited save, with the
textarea's DOM value reading 946/0 at that moment), not this suite.

Six of the ten examples are controls, because the risk here is a fix that
over-reaches: an LF body, an empty body and a whitespace-only body must all be
stored exactly as they are today — an empty body is a real send mode on the
publisher side (an invitation *without* a note) — `edit`'s status gate must
still refuse a draft the operator can no longer act on, `skip`'s reason must
not be normalised, since the rule belongs to the body alone, and the seeded
thread is checked to be multi-line and LF-only so the other four cannot pass
for the wrong reason. Those other four are the ones that fail against the
unpatched tree.

The harness is the one `tests/test_queue_transitions.py` uses: a scratch
database built from the repo-root `schema.sql` by `tests/_db.py`, the Django
tables `migrate` creates on top so the page's login gate has real users, and
every example run in a probe subprocess per `tests/_probe.py`. With no
reachable Postgres the examples skip honestly.
"""

import html
import json
import os
import unittest

from . import _db
from ._probe import DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_queue_lf_test_{os.getpid()}'

# Every column `edit` and `skip` can write, plus the ones a mutant could write
# by accident: a comparison over this shape is what "untouched" means.
DRAFT_COLUMNS = 'id, network, kind, status, body, approved_at, sent_at, sent_url, skip_reason'

OPERATOR = 'queue-lf-tests-operator'

# A multi-line thread, LF-only, with the `---` separator `schema.sql` documents
# ("for a thread, tweets separated by a --- line"). No leading or trailing
# whitespace, so a `.strip()` added to the normaliser cannot be caught here —
# that mutant is the whitespace control's job, and each mutant must be caught
# by exactly one example set.
THREAD_BODY = '1/ первый твит про очередь утверждения\n\n---\n\n2/ второй твит, с деталями'

# A single-line draft, used by the controls that do not need several lines.
PLAIN_BODY = 'один абзац без переводов строк'

SKIP_REASON = 'не сейчас — тон не тот'
SENT_URL = 'https://linkedin.example/posts/sent-one'

FIXTURE = [
    ('thread', 'waiting', 'thread', THREAD_BODY),
    ('plain', 'waiting', 'comment', PLAIN_BODY),
    ('sent', 'sent', 'comment', 'уже отправлено'),
]

# The textarea as the template renders it; sliced out of the page so the
# unedited-save example submits what the operator was actually shown.
TEXTAREA_OPEN = '<textarea name="body" aria-label="текст черновика">'
TEXTAREA_CLOSE = '</textarea>'

# The ORM probe: `django.setup()` loads the models, the transition is the only
# thing that talks to the database, and the payload arrives JSON-encoded so a
# carriage return in it is unambiguous.
TRANSITION = """
import json
import os
import django
django.setup()
from social import queue

payload = json.loads(os.environ['PROBE_PAYLOAD'])
action = {'edit': queue.edit, 'skip': queue.skip}[payload['action']]
row = action(payload['id'], payload['value'])
print(json.dumps(None if row is None else {'status': row.status, 'body': row.body}))
"""

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
    for key, status, kind, body in FIXTURE:
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


def _rendered_textarea(page_html, body):
    """The value the browser would read out of the editor for `body`'s card.

    Sliced out of the page rather than assumed, so the unedited-save example
    submits what the operator was shown. Two HTML rules are applied, both of
    them the browser's: character references are resolved, and a single leading
    newline directly after the start tag is dropped (the rule
    `tests/_formfields.py` already carries for the admin's textareas).
    """
    for fragment in page_html.split(TEXTAREA_OPEN)[1:]:
        value = html.unescape(fragment.split(TEXTAREA_CLOSE)[0])
        for newline in ('\r\n', '\n'):
            if value.startswith(newline):
                value = value[len(newline):]
                break
        if value == body:
            return value
    raise AssertionError(f'no editor on /queue/ rendered {body!r}')


class QueueBodyLineEndingsTestCase(unittest.TestCase):
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

    def apply(self, script, action, draft_id, value):
        payload = json.dumps({'action': action, 'id': draft_id, 'value': value})
        return self.probe(script, PROBE_PAYLOAD=payload)

    def stored_body_for(self, draft_id, payload):
        """Submit `payload` as the body of `draft_id`; return what was stored."""
        self.apply(TRANSITION, 'edit', draft_id, payload)
        return self.row(draft_id)['body']


class CrlfPayloadIsNormalisedTest(QueueBodyLineEndingsTestCase):
    """The four examples the defect fails: a submitted body is stored as LF."""

    def test_a_crlf_payload_is_stored_with_lf_line_endings(self):
        stored = self.stored_body_for(ids['thread'], THREAD_BODY.replace('\n', '\r\n'))

        self.assertEqual(stored, THREAD_BODY)
        self.assertNotIn('\r', stored, 'no carriage return may reach the column')

    def test_a_lone_cr_payload_is_stored_with_lf_line_endings(self):
        # Not a shape a browser produces, but this route is reachable by a
        # script or by curl, and one "no carriage returns" answer is easier to
        # assert than two.
        stored = self.stored_body_for(ids['thread'], THREAD_BODY.replace('\n', '\r'))

        self.assertEqual(stored, THREAD_BODY)
        self.assertNotIn('\r', stored, 'a lone carriage return is a line ending too')

    def test_a_threads_separator_line_survives_a_crlf_save(self):
        # `schema.sql`: "for a thread, tweets separated by a --- line". With
        # CRLF stored, the separator line is `\r\n---\r\n` and no consumer
        # splitting on `\n---\n` finds it.
        stored = self.stored_body_for(ids['thread'], THREAD_BODY.replace('\n', '\r\n'))

        self.assertIn('\n---\n', stored, "the thread's separator line must survive the save")

    def test_saving_the_rendered_editor_unchanged_leaves_the_body_byte_identical(self):
        before = self.row(ids['thread'])
        rendered = _rendered_textarea(self.probe(RENDER_QUEUE)['html'], THREAD_BODY)
        self.assertNotIn('\r', rendered, 'the page renders the body as stored, LF-only')
        # The one thing a browser adds, written in by hand: the form payload
        # rule rewrites every newline in a submitted textarea to CRLF, so this
        # is an operator opening «Правка» and saving without typing anything.
        submitted = rendered.replace('\n', '\r\n')
        self.assertNotEqual(submitted, rendered, 'the fixture must carry the browser rule')

        result = self.apply(POST_EDIT, 'edit', ids['thread'], submitted)

        self.assertEqual(result['status'], 302)
        self.assertEqual(result['location'], '/queue/')
        after = self.row(ids['thread'])
        self.assertEqual(
            after['body'], before['body'], 'a save that edited nothing must change nothing'
        )
        self.assertEqual(
            len(after['body'].encode('utf-8')),
            len(before['body'].encode('utf-8')),
            'the stored body must be byte-identical, not merely similar',
        )


class BodyStoredAsSubmittedTest(QueueBodyLineEndingsTestCase):
    """Controls: everything the normaliser must leave exactly as it is."""

    def test_an_lf_payload_is_stored_unchanged(self):
        self.assertEqual(self.stored_body_for(ids['thread'], THREAD_BODY), THREAD_BODY)

    def test_an_empty_payload_is_stored_unchanged(self):
        # An empty body is a meaningful state, not a mistake to repair:
        # `scout-ext`'s `senders/linkedin-connect.mjs` branches on it to send
        # an invitation without a note. Whether the queue should refuse one is
        # a separate decision, deliberately not taken here.
        self.assertEqual(self.stored_body_for(ids['plain'], ''), '')

    def test_a_whitespace_only_payload_is_stored_unchanged(self):
        # `bin/soc.mjs` trims a file body; this route must not, because
        # trimming would turn a whitespace-only edit into the empty-body send
        # mode above. The payload carries no carriage return, so this control
        # fails for one reason only: a `.strip()` in the normaliser.
        whitespace = '   \n\n  '

        self.assertEqual(self.stored_body_for(ids['plain'], whitespace), whitespace)


class NormalisationIsScopedTest(QueueBodyLineEndingsTestCase):
    """Controls: the rule applies to `edit`'s body and to nothing else."""

    def test_edit_still_refuses_a_draft_the_operator_can_no_longer_act_on(self):
        before = self.row(ids['sent'])

        result = self.apply(TRANSITION, 'edit', ids['sent'], THREAD_BODY.replace('\n', '\r\n'))

        self.assertIsNone(result, 'edit on a sent draft must refuse the write')
        self.assertEqual(
            self.row(ids['sent']), before, 'a normalised body must not slip past the status gate'
        )

    def test_skips_reason_is_not_normalised(self):
        # The reason comes from a single-line `<input type="text">`, which
        # cannot carry a newline from a browser at all; a direct caller can
        # still pass one, and it is stored verbatim. Normalising it would be
        # scope this fix does not have.
        reason = f'{SKIP_REASON}\r\nвторая строка'

        result = self.apply(TRANSITION, 'skip', ids['plain'], reason)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(
            self.row(ids['plain'])['skip_reason'], reason, 'the reason is stored as given'
        )


class FixtureTest(QueueBodyLineEndingsTestCase):
    """Positive control: the seeded thread really is multi-line and LF-only.

    Without it, a fixture that lost its newlines — or arrived carrying CRLF —
    would let every example above pass for the wrong reason. The schema itself
    needs no separate control here: `_seed` inserts into `drafts`, so a database
    that did not load `schema.sql` fails the whole module at setup.
    """

    def test_the_fixture_holds_a_multi_line_thread_a_plain_draft_and_a_sent_one(self):
        self.assertEqual(sorted(ids), ['plain', 'sent', 'thread'])
        self.assertEqual(len(_drafts()), len(FIXTURE))
        self.assertIn('\n', self.row(ids['thread'])['body'], 'the thread must be multi-line')
        self.assertNotIn('\r', self.row(ids['thread'])['body'], 'and stored LF-only')


if __name__ == '__main__':
    unittest.main()

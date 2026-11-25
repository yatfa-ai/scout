"""The approval queue's guarded transitions, pinned against a real database.

`social/queue.py` mirrors the four POST routes of `web/server.mjs`, and nothing
in the database enforces what they enforce: `drafts.status` is a plain CHECK
over the five legal values with no trigger and no transition constraint
(`schema.sql`), so a dropped `status=...` precondition is silently legal SQL
that changes what the operator's button does. That is why these examples exist,
and why they need a stored row: "the write was refused and the row is
untouched" is a claim about the row, not about a return value.

Three properties are pinned, the same three as `test/web-approval-gate.test.mjs`
on the Node side:

  1. the per-transition status precondition — approve only from `waiting`,
     unapprove only from `approved`, edit and skip only from `waiting` or
     `approved`;
  2. the refusal — the transition returns None, the row changes no column, and
     the page tells the operator `queue.NO_CHANGE`;
  3. exactly one row — the targeted draft changes and nothing else does, with a
     sibling `waiting` draft in the fixture as the positive control that fails a
     mass-update mutant.

Plus the three parity details of `queue.py`'s docstring (the DB-side
`approved_at` stamp, NULL for an omitted skip reason, and edit de-approving),
the single-statement guard (one UPDATE, the precondition in its WHERE clause,
no row load before the write) and the page's own contract: what `/queue/`
renders, 405 on a GET to a write route, a CSRF token in every form, and an
anonymous request that neither reads the queue nor writes a row.

The database is a scratch one built from `schema.sql` by `tests/_db.py` (see
its docstring for why neither test runner can host this), with the Django
tables `migrate` creates on top so the page's login gate has real users and
sessions. Every example runs in a probe subprocess with `DATABASE_URL` pointed
at that database, the convention of `tests/_probe.py`, so `manage.py test` and
`python -m unittest discover -s tests -t .` both keep working under
`NoDatabaseTestRunner`. With no reachable Postgres the examples skip honestly.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_queue_test_{os.getpid()}'

# Every column the four transitions can write, plus the ones a mutant could
# write by accident: a comparison over this shape is what "untouched" means.
DRAFT_COLUMNS = 'id, network, kind, status, body, approved_at, sent_at, sent_url, skip_reason'

OPERATOR = 'queue-tests-operator'

NEW_BODY = 'переписанный оператором текст'
SKIP_REASON = 'не сейчас — тон не тот'
SENT_URL = 'https://linkedin.example/posts/sent-one'

# One draft per status, plus a second `waiting` one: the sibling a mass update
# would sweep along. The `waiting` row carries every optional field the page
# renders, so the rendering example has something to find.
FIXTURE = [
    ('waiting', 'ожидает утверждения', 'Анна, CTO — про очередь утверждения'),
    ('waiting', 'второй, ждёт своей очереди', 'Борис, инженер — второй черновик'),
    ('draft', 'ещё пишется', 'черновик в работе'),
    ('approved', 'утверждено оператором', 'Вера, основатель — утверждённый текст'),
    ('skipped', 'отклонено ранее', 'Галина — отклонённый текст'),
    ('sent', 'уже отправлено', 'Дмитрий — отправленный текст'),
]
FACTS = 'ссылка на код: lib/db.mjs'
QUESTION = 'тон нормальный?'
TARGET_URL = 'https://linkedin.example/posts/target-one'

# Probes that only touch the ORM: `django.setup()` loads the models, and the
# transition is the only thing that talks to the database.
TRANSITION = """
import json
import os
import django
django.setup()
from django.db import connection
from django.test.utils import CaptureQueriesContext
from social import queue

ACTIONS = {
    'approve': lambda draft_id: queue.approve(draft_id),
    'unapprove': lambda draft_id: queue.unapprove(draft_id),
    'edit': lambda draft_id: queue.edit(draft_id, os.environ['PROBE_BODY']),
    'skip': lambda draft_id: queue.skip(draft_id, os.environ.get('PROBE_REASON')),
}

draft_id = int(os.environ['PROBE_ID'])
with CaptureQueriesContext(connection) as captured:
    row = ACTIONS[os.environ['PROBE_ACTION']](draft_id)

print(json.dumps({
    'row': None if row is None else {'id': row.id, 'status': row.status, 'body': row.body},
    'queries': [query['sql'] for query in captured.captured_queries],
}))
"""

# Probes that go through the page. `setup_test_environment()` is what puts
# `testserver` into ALLOWED_HOSTS, which the settings deliberately keep to the
# two loopback names.
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

OPERATOR = 'queue-tests-operator'


def operator_client(csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    client.force_login(User.objects.get(username=OPERATOR))
    return client
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='queue-tests-operator',
    defaults={'is_staff': True, 'is_active': True},
)
print('ok')
"""

RENDER_QUEUE = HTTP_PRELUDE + """
response = operator_client().get(reverse('queue'))
print(json.dumps({
    'status': response.status_code,
    'html': response.content.decode('utf-8'),
}))
"""

POST_WRITE = HTTP_PRELUDE + """
payload = {'id': os.environ['PROBE_ID']}
for name in ('body', 'reason'):
    key = 'PROBE_' + name.upper()
    if key in os.environ:
        payload[name] = os.environ[key]

csrf = os.environ.get('PROBE_CSRF') == '1'
if os.environ.get('PROBE_ANONYMOUS') == '1':
    client = Client(enforce_csrf_checks=csrf)
else:
    client = operator_client(csrf=csrf)

response = client.post(reverse(os.environ['PROBE_ROUTE']), payload)
out = {'status': response.status_code, 'location': response.headers.get('Location')}
if os.environ.get('PROBE_FOLLOW') == '1' and out['location']:
    out['html'] = client.get(out['location']).content.decode('utf-8')
print(json.dumps(out))
"""

GET_EVERY_ROUTE = HTTP_PRELUDE + """
WRITE_ROUTES = ('queue_approve', 'queue_unapprove', 'queue_edit', 'queue_skip')

staff = operator_client()
anonymous = Client()
out = {
    'staff_write_routes': {name: staff.get(reverse(name)).status_code for name in WRITE_ROUTES},
    'anonymous_queue': {},
}
response = anonymous.get(reverse('queue'))
out['anonymous_queue'] = {
    'status': response.status_code,
    'location': response.headers.get('Location'),
}
print(json.dumps(out))
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
    for status, body, note in FIXTURE:
        row = database.sql(
            """insert into drafts (network, kind, status, body, target_note, target_url,
                                   facts, question, skip_reason)
               values ('linkedin', 'comment', %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (
                status,
                body,
                note,
                TARGET_URL if status == 'waiting' else None,
                FACTS if status == 'waiting' else None,
                QUESTION if status == 'waiting' else None,
                'прошлая причина' if status == 'skipped' else None,
            ),
        )[0]
        # The second `waiting` row is the sibling; the first keeps the name.
        key = 'sibling' if status == 'waiting' and 'waiting' in ids else status
        ids[key] = row['id']

    # The approval and send stamps are set with SQL, because approving is this
    # surface's job and nothing here may produce a `sent` row.
    database.sql(
        'update drafts set approved_at = now() where id = any(%s)',
        ([ids['approved'], ids['sent']],),
    )
    database.sql(
        'update drafts set sent_at = now(), sent_url = %s where id = %s',
        (SENT_URL, ids['sent']),
    )


def _drafts():
    return database.sql(f'select {DRAFT_COLUMNS} from drafts order by id')


class QueueTransitionTestCase(unittest.TestCase):
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

    def transition(self, action, draft_id, **overrides):
        return self.probe(
            TRANSITION, PROBE_ACTION=action, PROBE_ID=str(draft_id), **overrides
        )

    def assertRefused(self, action, status, **overrides):
        """The transition changes nothing and says so, on a row of `status`."""
        draft_id = ids[status]
        before = self.row(draft_id)
        self.assertEqual(before['status'], status, f'the seeded row should be {status}')

        result = self.transition(action, draft_id, **overrides)

        self.assertIsNone(result['row'], f'{action} on {status} must refuse the write')
        self.assertEqual(
            self.row(draft_id), before, f'{action} on a {status} draft must change no column'
        )


class GuardedTransitionsTest(QueueTransitionTestCase):
    def test_approve_moves_a_waiting_draft_to_approved_and_stamps_approved_at(self):
        before = self.row(ids['waiting'])
        self.assertEqual(before['status'], 'waiting')
        self.assertIsNone(before['approved_at'])
        clock = database.sql('select now() as now')[0]['now']

        result = self.transition('approve', ids['waiting'])

        self.assertEqual(result['row'], {
            'id': ids['waiting'], 'status': 'approved', 'body': before['body'],
        })
        row = self.row(ids['waiting'])
        self.assertEqual(row['status'], 'approved')
        self.assertIsNotNone(row['approved_at'], 'approved_at must be stamped')
        self.assertGreaterEqual(
            row['approved_at'], clock, 'the stamp must come from the database clock'
        )
        self.assertEqual(
            row['body'], before['body'], 'approving must not touch the approved text'
        )

    def test_approve_refuses_every_status_but_waiting(self):
        for status in ('draft', 'skipped', 'sent', 'approved'):
            with self.subTest(status=status):
                self.assertRefused('approve', status)

    def test_unapprove_returns_an_approved_draft_to_waiting_and_clears_the_stamp(self):
        self.assertIsNotNone(self.row(ids['approved'])['approved_at'])

        result = self.transition('unapprove', ids['approved'])

        self.assertEqual(result['row']['status'], 'waiting')
        row = self.row(ids['approved'])
        self.assertEqual(row['status'], 'waiting')
        self.assertIsNone(row['approved_at'], 'unapprove must clear the approval stamp')

    def test_unapprove_refuses_every_status_but_approved(self):
        for status in ('draft', 'skipped', 'sent', 'waiting'):
            with self.subTest(status=status):
                self.assertRefused('unapprove', status)

    def test_edit_de_approves_the_draft_it_rewrites(self):
        result = self.transition('edit', ids['approved'], PROBE_BODY=NEW_BODY)

        self.assertEqual(result['row'], {
            'id': ids['approved'], 'status': 'waiting', 'body': NEW_BODY,
        })
        row = self.row(ids['approved'])
        self.assertEqual(row['body'], NEW_BODY)
        # The operator must not be able to approve text A and have text B sent.
        self.assertEqual(row['status'], 'waiting', 'an edited approval goes back to the queue')
        self.assertIsNone(row['approved_at'], 'the old approval must not survive the edit')

    def test_edit_rewrites_a_waiting_draft_and_leaves_it_waiting(self):
        result = self.transition('edit', ids['waiting'], PROBE_BODY=NEW_BODY)

        self.assertEqual(result['row']['status'], 'waiting')
        self.assertEqual(self.row(ids['waiting'])['body'], NEW_BODY)

    def test_edit_refuses_a_draft_the_operator_can_no_longer_act_on(self):
        for status in ('sent', 'skipped', 'draft'):
            with self.subTest(status=status):
                self.assertRefused('edit', status, PROBE_BODY=NEW_BODY)

    def test_skip_hides_a_draft_and_stores_the_reason_it_was_given(self):
        result = self.transition('skip', ids['waiting'], PROBE_REASON=SKIP_REASON)

        self.assertEqual(result['row']['status'], 'skipped')
        row = self.row(ids['waiting'])
        self.assertEqual(row['status'], 'skipped')
        self.assertEqual(
            row['skip_reason'], SKIP_REASON, 'the reason the operator gave must be stored'
        )

    def test_skip_stores_null_when_no_reason_is_given(self):
        # Both shapes the page can produce: an empty field, and no field at all.
        self.transition('skip', ids['waiting'], PROBE_REASON='')
        self.assertIsNone(
            self.row(ids['waiting'])['skip_reason'], 'an empty reason is stored as NULL'
        )

        self.transition('skip', ids['sibling'])
        self.assertIsNone(self.row(ids['sibling'])['skip_reason'])

    def test_skip_works_on_an_approved_draft_and_refuses_a_sent_one(self):
        self.assertEqual(self.transition('skip', ids['approved'])['row']['status'], 'skipped')
        self.assertRefused('skip', 'sent', PROBE_REASON=SKIP_REASON)

    def test_approve_changes_exactly_one_row(self):
        before = _drafts()
        self.assertEqual(
            self.row(ids['sibling'])['status'],
            'waiting',
            'the sibling must be waiting too, so a mass approve would move it as well',
        )

        self.transition('approve', ids['waiting'])

        after = _drafts()
        self.assertEqual(
            [row['id'] for row in after],
            [row['id'] for row in before],
            'the transition must neither insert nor delete a row',
        )
        for was, now in zip(before, after):
            if now['id'] == ids['waiting']:
                self.assertEqual(now['status'], 'approved')
                continue
            with self.subTest(draft=now['id']):
                self.assertEqual(now, was, f'draft {now["id"]} must be untouched')


class OneGuardedStatementTest(QueueTransitionTestCase):
    """The precondition lives in the WHERE clause, never in a load-then-save."""

    # Per transition: the statuses its WHERE clause must name, and what its SET
    # clause must do to approved_at. `Now()` renders as `STATEMENT_TIMESTAMP()`
    # on PostgreSQL — the statement's own clock, which is the DB-side stamp
    # `server.mjs`'s `now()` asks for.
    DB_SIDE_CLOCK = 'STATEMENT_TIMESTAMP()'
    EXPECTED = {
        'approve': (['waiting'], DB_SIDE_CLOCK),
        'unapprove': (['approved'], 'NULL'),
        'edit': (['waiting', 'approved'], 'NULL'),
        'skip': (['waiting', 'approved'], None),
    }

    def updates(self, action, draft_id, **overrides):
        queries = self.transition(action, draft_id, **overrides)['queries']
        self.assertTrue(queries, 'the transition must issue at least one statement')
        self.assertTrue(
            queries[0].lstrip().upper().startswith('UPDATE'),
            f'{action} must write first: no row is loaded before the guarded UPDATE, '
            f'got {queries[0]!r}',
        )
        updates = [q for q in queries if q.lstrip().upper().startswith('UPDATE')]
        self.assertEqual(
            len(updates), 1, f'{action} must be exactly one UPDATE, got {updates}'
        )
        return updates[0]

    def test_each_transition_is_one_update_whose_where_clause_carries_the_precondition(self):
        for action, (statuses, stamp) in self.EXPECTED.items():
            with self.subTest(action=action):
                statement = self.updates(action, ids['waiting'], PROBE_BODY=NEW_BODY)
                head, _, where = statement.partition(' WHERE ')
                self.assertTrue(where, f'{action} must carry a WHERE clause: {statement!r}')
                self.assertIn('status', where, f'{action} must guard on status: {where!r}')
                for status in statuses:
                    self.assertIn(
                        f"'{status}'", where, f'{action} must admit only {statuses}: {where!r}'
                    )
                if stamp is not None:
                    self.assertIn(
                        stamp,
                        head.upper(),
                        f'{action} must set approved_at to {stamp} DB-side: {head!r}',
                    )

    def test_approve_does_not_stamp_approved_at_from_python(self):
        # `Now()` renders as a SQL clock function; `timezone.now()` would render
        # as a literal timestamp parameter, which is the substitution this pins
        # against — the stamp must be the database's, not the web process's.
        statement = self.updates('approve', ids['waiting'])
        head = statement.partition(' WHERE ')[0]
        self.assertIn(self.DB_SIDE_CLOCK, head.upper())
        self.assertNotIn('+00:00', head, f'the stamp must not be a Python timestamp: {head!r}')


class QueuePageTest(QueueTransitionTestCase):
    def setUp(self):
        super().setUp()
        self.page = self.probe(RENDER_QUEUE)
        self.assertEqual(self.page['status'], 200)
        self.html = self.page['html']

    def test_the_queue_renders_every_waiting_and_approved_draft(self):
        for key in ('waiting', 'sibling', 'approved'):
            with self.subTest(draft=key):
                self.assertIn(self.row(ids[key])['body'], self.html)
        self.assertIn('Анна, CTO — про очередь утверждения', self.html, 'target_note is shown')
        self.assertIn(FACTS, self.html, 'the facts block is shown when present')
        self.assertIn(QUESTION, self.html, 'the question block is shown when present')
        self.assertIn(TARGET_URL, self.html, 'target_url is shown as a link')
        self.assertIn('утверждено', self.html, 'an approved row carries its stamp')
        self.assertIn('LinkedIn', self.html, 'the network is shown')
        self.assertIn('комментарий', self.html, 'the kind is shown')

    def test_no_draft_skipped_or_sent_row_appears(self):
        for status in ('draft', 'skipped', 'sent'):
            with self.subTest(status=status):
                self.assertNotIn(self.row(ids[status])['body'], self.html)

    def test_the_buttons_follow_the_status_of_the_card(self):
        for label in ('Отправляй', 'Правка', 'Пропустить', 'Отозвать'):
            with self.subTest(label=label):
                self.assertIn(label, self.html)
        # Two waiting drafts, each offering approve + edit + skip; one approved
        # draft offering unapprove.
        self.assertEqual(self.html.count('Отправляй</button>'), 2)
        self.assertEqual(self.html.count('Отозвать</button>'), 1)

    def test_the_page_leaks_no_template_syntax(self):
        # A multi-line `{# ... #}` is not a Django comment and renders verbatim;
        # so does a misspelled tag. Either would put developer prose in front of
        # the operator.
        for marker in ('{#', '{%', '{{'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.html)

    def test_every_form_carries_a_csrf_token(self):
        forms = self.html.count('<form')
        self.assertEqual(forms, 7, 'three forms per waiting card, one for the approved one')
        self.assertEqual(
            self.html.count('name="csrfmiddlewaretoken"'),
            forms,
            'every form must carry a CSRF token',
        )

    def test_a_post_without_a_csrf_token_is_refused_and_writes_nothing(self):
        before = self.row(ids['waiting'])

        result = self.probe(
            POST_WRITE, PROBE_ROUTE='queue_approve', PROBE_ID=str(ids['waiting']), PROBE_CSRF='1'
        )

        self.assertEqual(result['status'], 403, 'the CSRF middleware must refuse the write')
        self.assertEqual(self.row(ids['waiting']), before)


class QueueRoutesTest(QueueTransitionTestCase):
    def test_a_get_on_a_write_route_is_405(self):
        result = self.probe(GET_EVERY_ROUTE)
        self.assertEqual(
            result['staff_write_routes'],
            {name: 405 for name in ('queue_approve', 'queue_unapprove', 'queue_edit', 'queue_skip')},
        )

    def test_an_anonymous_request_neither_reads_the_queue_nor_writes_a_row(self):
        before = self.row(ids['waiting'])

        page = self.probe(GET_EVERY_ROUTE)['anonymous_queue']
        self.assertEqual(page['status'], 302, 'the queue is staff-only')
        self.assertIn('/admin/login/', page['location'])

        write = self.probe(
            POST_WRITE,
            PROBE_ROUTE='queue_approve',
            PROBE_ID=str(ids['waiting']),
            PROBE_ANONYMOUS='1',
        )
        self.assertEqual(write['status'], 302)
        self.assertIn('/admin/login/', write['location'])
        self.assertEqual(
            self.row(ids['waiting']), before, 'an anonymous POST must reach no transition'
        )

    def test_approving_through_the_page_moves_the_row_and_redirects_to_the_queue(self):
        result = self.probe(
            POST_WRITE, PROBE_ROUTE='queue_approve', PROBE_ID=str(ids['waiting'])
        )

        self.assertEqual(result['status'], 302)
        self.assertEqual(result['location'], '/queue/')
        self.assertEqual(self.row(ids['waiting'])['status'], 'approved')

    def test_editing_through_the_page_rewrites_the_body_and_de_approves(self):
        result = self.probe(
            POST_WRITE,
            PROBE_ROUTE='queue_edit',
            PROBE_ID=str(ids['approved']),
            PROBE_BODY=NEW_BODY,
        )

        self.assertEqual(result['status'], 302)
        row = self.row(ids['approved'])
        self.assertEqual(row['body'], NEW_BODY)
        self.assertEqual(row['status'], 'waiting')
        self.assertIsNone(row['approved_at'])

    def test_skipping_through_the_page_stores_an_empty_reason_as_null(self):
        self.probe(
            POST_WRITE, PROBE_ROUTE='queue_skip', PROBE_ID=str(ids['waiting']), PROBE_REASON=''
        )

        row = self.row(ids['waiting'])
        self.assertEqual(row['status'], 'skipped')
        self.assertIsNone(row['skip_reason'])

    def test_a_refused_transition_is_shown_to_the_operator(self):
        before = self.row(ids['sent'])

        result = self.probe(
            POST_WRITE,
            PROBE_ROUTE='queue_approve',
            PROBE_ID=str(ids['sent']),
            PROBE_FOLLOW='1',
        )

        self.assertEqual(result['status'], 302)
        self.assertEqual(self.row(ids['sent']), before, 'the refused approve changed no column')
        self.assertIn(
            'ничего не изменилось (статус не тот)',
            result['html'],
            'a silent refusal would leave the operator pressing a dead button',
        )

    def test_an_unusable_id_is_a_refusal_rather_than_a_server_error(self):
        before = _drafts()

        result = self.probe(POST_WRITE, PROBE_ROUTE='queue_approve', PROBE_ID='', PROBE_FOLLOW='1')

        self.assertEqual(result['status'], 302)
        self.assertIn('ничего не изменилось (статус не тот)', result['html'])
        self.assertEqual(_drafts(), before)


class ScratchSchemaTest(QueueTransitionTestCase):
    """Positive control: the harness really did build the social schema.

    Without this an empty or half-loaded database could make every refusal
    example above pass for the wrong reason.
    """

    def test_schema_sql_loaded_all_six_tables(self):
        tables = database.tables()
        for table in ('people', 'drafts', 'interactions', 'questions', 'ledger', 'leads'):
            with self.subTest(table=table):
                self.assertIn(table, tables)

    def test_the_fixture_holds_one_draft_per_status_plus_a_sibling(self):
        self.assertEqual(
            sorted(ids),
            ['approved', 'draft', 'sent', 'sibling', 'skipped', 'waiting'],
        )
        self.assertEqual(len(_drafts()), len(FIXTURE))


if __name__ == '__main__':
    unittest.main()

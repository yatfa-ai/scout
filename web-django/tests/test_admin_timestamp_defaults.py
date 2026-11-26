"""The four `DEFAULT now()` columns of `schema.sql`, mirrored in the models.

`schema.sql` gives exactly four columns a database-side default — `people
.created_at`, `drafts.created_at`, `leads.created_at` and `leads.updated_at`,
each `timestamptz NOT NULL DEFAULT now()`. `inspectdb` cannot see a DB-side
default, so the adopted models declared all four as a bare `DateTimeField()`,
and a `NOT NULL` field with no default is a *required* admin form field whose
value Django always sends. Two consequences, both of them product defects on
the surface knowledge scout-3 decision 3 calls the ops surface:

  1. an operator could not add a person or a lead at all without hand-typing a
     machine timestamp (the leads add form demanded two);
  2. every admin save rewrote `created_at` from the form's own rendered value —
     losing its sub-second precision — and left `updated_at` frozen, so a lead
     advanced through the admin did not move under `LeadsAdmin.ordering =
     ('-updated_at',)` while `bin/lead.mjs` maintains that column on both of
     its write paths.

`models.py` now carries `db_default=Now()` + `editable=False` on the three
`created_at` columns and `auto_now=True` on `leads.updated_at`. These examples
pin the three behaviours a future `inspectdb` re-run — or a field edit that
drops one of those arguments — would silently undo. Nothing here is a claim
about a configuration tuple alone: an add that "works" is a row in the
database, and "the lead rises in the changelist" is the order of the rendered
page.

Two halves, the convention of the sibling modules:

  1. metadata, no database — the add forms' own field census and the four
     fields' declarations, read out of `admin.site._registry` in a probe
     subprocess (`django.setup()` needs no Postgres). The three `NOT NULL`
     time columns with *no* database default (`interactions.ts`,
     `questions.date`, `ledger.at` — two of them `text`, one a `date`) are the
     positive control: they must stay editable and required, so a blanket
     `editable=False` cannot pass here.
  2. rendered writes, against a scratch database built from `schema.sql` by
     `tests/_db.py` — the add forms posted without a timestamp, the edit's
     effect on both stamps, and the `-updated_at` changelist order around that
     edit. A second, more recently touched lead is seeded as the control:
     without it the ordering assertion passes vacuously.

With no reachable Postgres the second half skips honestly and the suite stays
green, exactly as in `tests/test_queue_transitions.py`.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_timestamp_test_{os.getpid()}'

OPERATOR = 'timestamp-tests-operator'

# --- what the forms must and must not ask for ------------------------------

# The whole population of DB-defaulted columns, with the declaration each one
# must carry. `db_default=Now()` mirrors `DEFAULT now()` and keeps the stamp
# database-side; `updated_at` needs an on-update clock, which `db_default` has
# no form of, so it is the one app-side stamp.
DB_DEFAULTED = {
    'People': {'created_at': 'db_default'},
    'Drafts': {'created_at': 'db_default'},
    'Leads': {'created_at': 'db_default', 'updated_at': 'auto_now'},
}

# The `NOT NULL` time columns `schema.sql` gives no default: operator-supplied by
# design, so they must stay editable and required. The control half.
OPERATOR_SUPPLIED = {
    'Interactions': 'ts',
    'Questions': 'date',
    'Ledger': 'at',
}

# The admin add forms, field for field. Pinned whole rather than by membership:
# a timestamp creeping back is the regression, and losing a real field would be
# one too.
ADD_FORM_FIELDS = {
    'People': [
        'id', 'name', 'network', 'profile', 'headline', 'followers', 'segment',
        'relevance', 'first_seen', 'status',
    ],
    'Leads': ['person', 'stage', 'source_url', 'pain', 'next_step'],
}
ADD_FORM_REQUIRED = {
    'People': ['id', 'name', 'network', 'status'],
    'Leads': ['person', 'stage'],
}

# The timestamps stay where the operator scans them. `editable=False` removes a
# field from the default *detail*-page fieldsets, and it would be easy to
# believe it costs the changelist column too: it does not, and that is an
# acceptance criterion of this change.
CHANGELIST_TIMESTAMPS = {
    'People': ['created_at'],
    'Drafts': ['created_at'],
    'Leads': ['created_at', 'updated_at'],
}

# --- fixture ---------------------------------------------------------------

ANNA = 'li:anna'
BORIS = 'li:boris'

# The person the add example creates, and then removes again.
PROBE_PERSON = 'li:timestamp-probe'
PROBE_PERSON_NAME = 'Проба, человек без отметки времени'

# The lead under test: created and last touched a week before the control, with
# sub-second precision — the digits the old form-rendered value truncated away.
LEAD_CREATED = '2026-11-18 22:48:40.495785+00'
LEAD_UPDATED = '2026-11-18 22:48:40.495785+00'
# The control lead, touched later: before the edit it sits on top of the
# `-updated_at` changelist, so "the edited lead is first" is a real claim.
CONTROL_UPDATED = '2026-11-22 09:15:00.123456+00'

LEAD_COLUMNS = 'id, person, stage, source_url, pain, next_step, created_at, updated_at'

PAIN_TEXT = 'каждое согласование — это переписка на три дня'
NEXT_STEP = 'прислать разбор очереди'

# --- probes ----------------------------------------------------------------

READ_DECLARATIONS = """
import json
import django
django.setup()
from django.contrib import admin
from django.db.models import NOT_PROVIDED
from django.db.models.functions import Now
from social.models import Drafts, Interactions, Leads, Ledger, People, Questions


class _User:
    is_active = is_staff = is_superuser = True

    def has_perm(self, *args, **kwargs):
        return True


class _Request:
    user = _User()
    GET = {}
    META = {}


request = _Request()


def field(model, name):
    declared = model._meta.get_field(name)
    db_default = declared.db_default
    return {
        'editable': declared.editable,
        'db_default': None if db_default is NOT_PROVIDED else type(db_default).__name__,
        'auto_now': getattr(declared, 'auto_now', False),
        'null': declared.null,
    }


def read(model):
    model_admin = admin.site._registry[model]
    # `get_form` with no instance is the add form: the one an operator meets
    # first, and the one that refused a row without a timestamp.
    form = model_admin.get_form(request)()
    return {
        'add_form_fields': list(form.fields),
        'add_form_required': [name for name, f in form.fields.items() if f.required],
        'list_display': list(model_admin.list_display),
        'ordering': list(model_admin.ordering or []),
        'fields': {
            declared.name: field(model, declared.name)
            for declared in model._meta.fields
        },
    }


print(json.dumps({
    m.__name__: read(m)
    for m in (People, Drafts, Leads, Interactions, Questions, Ledger)
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
from django.urls import reverse

OPERATOR = 'timestamp-tests-operator'

ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.S)
TAG = re.compile(r'<[^>]+>')


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username=OPERATOR))
    return client


def text_of(fragment):
    return entities.unescape(TAG.sub(' ', fragment)).strip()


def rows_of(body):
    # Cells only. A row's checkbox carries the model's __str__ in its
    # aria-label, so the page text is not evidence about a column.
    if '<tbody>' not in body:
        return []
    table = body.split('<tbody>')[1].split('</tbody>')[0]
    return [[text_of(cell) for cell in CELL.findall(row)] for row in ROW.findall(table)]
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='timestamp-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

# One admin POST, add or change, with the payload a browser would send: the
# form's own fields and nothing else. No timestamp is ever in it.
ADMIN_POST = HTTP_PRELUDE + """
payload = json.loads(os.environ['PROBE_PAYLOAD'])
args = json.loads(os.environ.get('PROBE_ARGS', '[]'))
response = operator_client().post(reverse(os.environ['PROBE_ROUTE'], args=args), payload)
print(json.dumps({
    'status': response.status_code,
    'location': response.headers.get('Location'),
    # A required-field refusal is a 200 with the form re-rendered, not an
    # error status: the marker is what tells the two apart.
    'required_field_error': 'Обязательное поле' in response.content.decode('utf-8'),
}))
"""

# The leads changelist in its rendered order, by the `id` column.
LEADS_ORDER = HTTP_PRELUDE + """
response = operator_client().get(reverse('admin:social_leads_changelist'))
body = response.content.decode('utf-8')
rows = rows_of(body)
print(json.dumps({
    'status': response.status_code,
    # Cell 0 is the action checkbox; `id` is the first list_display column.
    'ids': [int(row[1]) for row in rows],
    'widths': sorted({len(row) for row in rows}),
}))
"""

# What the INSERT actually sends for `created_at`. `DEFAULT` is the database
# filling the column; a timestamp literal would be the app filling it, which is
# the substitution this pins against.
INSERT_SQL = """
import json
import django
django.setup()
from django.db import connection
from django.test.utils import CaptureQueriesContext
from social.models import Leads, People

out = {}
with CaptureQueriesContext(connection) as captured:
    person = People.objects.create(
        id='sql:probe', name='SQL-проба', network='linkedin'
    )
out['people'] = [query['sql'] for query in captured.captured_queries]
with CaptureQueriesContext(connection) as captured:
    lead = Leads.objects.create(person=person, stage='candidate')
out['leads'] = [query['sql'] for query in captured.captured_queries]

Leads.objects.filter(pk=lead.pk).delete()
People.objects.filter(pk=person.pk).delete()
print(json.dumps(out))
"""

# --- scratch database lifecycle -------------------------------------------

database = None
lead_id = None
control_id = None
baseline = []


def setUpModule():
    global database, baseline
    database = _db.scratch_database(SCRATCH_DB)
    if database is None:
        return
    try:
        _seed()
        # The admin's login gate needs real users and sessions. `migrate`
        # creates only the `django_*`/`auth_*` tables — every social model is
        # unmanaged — so it cannot touch the schema just loaded from schema.sql.
        result = run_probe(
            MIGRATE_AND_CREATE_OPERATOR, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET
        )
        assert result.returncode == 0, result.stderr
        baseline = _leads()
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _seed():
    global lead_id, control_id
    for person_id, name in ((ANNA, 'Анна Петрова'), (BORIS, 'Борис Смирнов')):
        database.sql(
            'insert into people (id, name, network) values (%s, %s, %s)',
            (person_id, name, 'linkedin'),
        )
    # The stamps are written explicitly here, which the database default would
    # otherwise decide: the examples compare against these exact values.
    lead_id = database.sql(
        """insert into leads (person, stage, pain, next_step, created_at, updated_at)
           values (%s, 'candidate', %s, %s, %s, %s) returning id""",
        (ANNA, PAIN_TEXT, NEXT_STEP, LEAD_CREATED, LEAD_UPDATED),
    )[0]['id']
    control_id = database.sql(
        """insert into leads (person, stage, next_step, created_at, updated_at)
           values (%s, 'talking', 'написать ещё раз', %s, %s) returning id""",
        (BORIS, LEAD_CREATED, CONTROL_UPDATED),
    )[0]['id']


def _leads():
    return database.sql(f'select {LEAD_COLUMNS} from leads order by id')


def _lead(row_id):
    rows = database.sql(f'select {LEAD_COLUMNS} from leads where id = %s', (row_id,))
    assert len(rows) == 1, f'lead {row_id} must exist'
    return rows[0]


def _person(person_id):
    rows = database.sql(
        'select id, name, status, created_at from people where id = %s', (person_id,)
    )
    assert len(rows) == 1, f'person {person_id} must exist'
    return rows[0]


# --- the metadata half -----------------------------------------------------


class ModelDeclarationTest(unittest.TestCase):
    """No database: what the fields declare and what the add forms ask for."""

    def setUp(self):
        result = run_probe(
            READ_DECLARATIONS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.admins = json.loads(result.stdout)

    def test_the_add_forms_ask_for_no_machine_timestamp(self):
        for model, fields in ADD_FORM_FIELDS.items():
            with self.subTest(model=model):
                self.assertEqual(self.admins[model]['add_form_fields'], fields)
                for column in ('created_at', 'updated_at'):
                    self.assertNotIn(
                        column,
                        self.admins[model]['add_form_fields'],
                        f'{model}.{column} is the database\'s to fill, not the operator\'s',
                    )

    def test_no_timestamp_is_a_required_add_form_field(self):
        # The defect itself: a required `created_at` is why an add without one
        # returned the form again and wrote no row.
        for model, required in ADD_FORM_REQUIRED.items():
            with self.subTest(model=model):
                self.assertEqual(self.admins[model]['add_form_required'], required)

    def test_each_db_defaulted_column_mirrors_schema_sql(self):
        for model, columns in DB_DEFAULTED.items():
            for column, kind in columns.items():
                with self.subTest(model=model, column=column):
                    declared = self.admins[model]['fields'][column]
                    self.assertFalse(
                        declared['editable'],
                        f'{model}.{column} must leave the forms, or its value wins over the '
                        f'database default',
                    )
                    self.assertFalse(declared['null'], 'the column is NOT NULL in schema.sql')
                    if kind == 'db_default':
                        self.assertEqual(
                            declared['db_default'],
                            'Now',
                            f'{model}.{column} must mirror DEFAULT now() database-side',
                        )
                        self.assertFalse(declared['auto_now'])
                    else:
                        self.assertTrue(
                            declared['auto_now'],
                            f'{model}.{column} must be stamped on every save',
                        )
                        self.assertIsNone(
                            declared['db_default'],
                            'db_default has no on-update form: auto_now is the whole stamp',
                        )

    def test_the_operator_supplied_timestamps_stay_editable(self):
        # Positive control: `interactions.ts`, `questions.date` and `ledger.at`
        # are NOT NULL with no database default, so they are the operator's to
        # type. A blanket `editable=False` would pass every example above and
        # fail here.
        for model, column in OPERATOR_SUPPLIED.items():
            with self.subTest(model=model, column=column):
                self.assertTrue(self.admins[model]['fields'][column]['editable'])
                self.assertIn(column, self.admins[model]['add_form_fields'])
                self.assertIn(column, self.admins[model]['add_form_required'])

    def test_the_timestamps_are_still_changelist_columns(self):
        # `editable=False` takes a field out of the detail page's default
        # fieldsets; the changelist is where the operator scans these, and it
        # must keep them.
        for model, columns in CHANGELIST_TIMESTAMPS.items():
            for column in columns:
                with self.subTest(model=model, column=column):
                    self.assertIn(column, self.admins[model]['list_display'])

    def test_leads_still_order_by_the_column_this_change_maintains(self):
        # The ordering is what makes a frozen `updated_at` visible: pinned here
        # so the two halves of the defect cannot drift apart.
        self.assertEqual(self.admins['Leads']['ordering'], ['-updated_at'])


# --- the rendered half -----------------------------------------------------


class AdminWriteTestCase(unittest.TestCase):
    """Shared fixture handling: skip without a database, restore after each example."""

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def tearDown(self):
        if database is None:
            return
        # Anything an example added, and then the two seeded leads as they were.
        database.sql('delete from leads where person = %s', (PROBE_PERSON,))
        database.sql('delete from people where id = %s', (PROBE_PERSON,))
        for row in baseline:
            database.sql(
                """update leads set stage = %s, source_url = %s, pain = %s, next_step = %s,
                                    created_at = %s, updated_at = %s
                   where id = %s""",
                (
                    row['stage'],
                    row['source_url'],
                    row['pain'],
                    row['next_step'],
                    row['created_at'],
                    row['updated_at'],
                    row['id'],
                ),
            )
        database.sql(
            'update people set name = %s where id = %s', ('Анна Петрова', ANNA)
        )

    # --- helpers ---------------------------------------------------------

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def post(self, route, payload, args=()):
        return self.probe(
            ADMIN_POST,
            PROBE_ROUTE=route,
            PROBE_ARGS=json.dumps(list(args)),
            PROBE_PAYLOAD=json.dumps(payload),
        )

    def assertSaved(self, result, what):
        self.assertFalse(
            result['required_field_error'],
            f'{what} was refused for a missing required field',
        )
        self.assertEqual(result['status'], 302, f'{what} must save and redirect')

    def lead_payload(self, person=ANNA, stage='candidate', **overrides):
        """Exactly what the change form renders, which is what a browser sends."""
        payload = {
            'person': person,
            'stage': stage,
            'source_url': '',
            'pain': PAIN_TEXT,
            'next_step': NEXT_STEP,
            '_save': 'Сохранить',
        }
        payload.update(overrides)
        return payload

    def person_payload(self, person_id, name, **overrides):
        payload = {
            'id': person_id,
            'name': name,
            'network': 'linkedin',
            'profile': '',
            'headline': '',
            'followers': '',
            'segment': '',
            'relevance': '',
            'first_seen': '',
            'status': 'наблюдаем',
            '_save': 'Сохранить',
        }
        payload.update(overrides)
        return payload


class AddWithoutATimestampTest(AdminWriteTestCase):
    def test_adding_a_person_needs_no_timestamp_and_the_database_stamps_the_row(self):
        clock = database.sql('select now() as now')[0]['now']

        result = self.post(
            'admin:social_people_add',
            self.person_payload(PROBE_PERSON, PROBE_PERSON_NAME),
        )

        self.assertSaved(result, 'adding a person')
        row = _person(PROBE_PERSON)
        self.assertEqual(row['name'], PROBE_PERSON_NAME)
        self.assertGreaterEqual(
            row['created_at'], clock, 'the stamp must come from the database clock'
        )
        self.assertNotEqual(
            row['created_at'],
            row['created_at'].replace(microsecond=0),
            'a DB-side `now()` keeps sub-second precision; a truncated stamp means the '
            'value came through the form instead',
        )

    def test_adding_a_lead_needs_neither_timestamp(self):
        self.post(
            'admin:social_people_add', self.person_payload(PROBE_PERSON, PROBE_PERSON_NAME)
        )
        clock = database.sql('select now() as now')[0]['now']

        result = self.post('admin:social_leads_add', self.lead_payload(person=PROBE_PERSON))

        self.assertSaved(result, 'adding a lead')
        rows = database.sql(
            f'select {LEAD_COLUMNS} from leads where person = %s', (PROBE_PERSON,)
        )
        self.assertEqual(len(rows), 1, 'the add must create exactly one lead')
        self.assertEqual(rows[0]['stage'], 'candidate')
        for column in ('created_at', 'updated_at'):
            with self.subTest(column=column):
                self.assertGreaterEqual(rows[0][column], clock)

    def test_the_insert_leaves_created_at_to_the_database(self):
        # `DEFAULT` is the database filling the column, which is what
        # `DEFAULT now()` in schema.sql is for. A timestamp literal here would
        # mean the app took the stamp over — the same substitution
        # `tests/test_queue_transitions.py` pins for `approved_at`.
        statements = self.probe(INSERT_SQL)
        for table in ('people', 'leads'):
            with self.subTest(table=table):
                inserts = [
                    sql for sql in statements[table] if sql.lstrip().upper().startswith('INSERT')
                ]
                self.assertEqual(len(inserts), 1, f'expected one INSERT, got {inserts}')
                statement = inserts[0]
                self.assertIn('"created_at"', statement)
                values = statement.partition(' VALUES ')[2]
                self.assertIn(
                    'DEFAULT',
                    values,
                    f'created_at must be left to the database: {statement!r}',
                )


class EditThroughTheAdminTest(AdminWriteTestCase):
    def test_editing_a_lead_preserves_created_at_and_bumps_updated_at(self):
        before = _lead(lead_id)
        self.assertEqual(before['stage'], 'candidate')

        result = self.post(
            'admin:social_leads_change', self.lead_payload(stage='connected'), args=[lead_id]
        )

        self.assertSaved(result, 'advancing a lead')
        after = _lead(lead_id)
        self.assertEqual(after['stage'], 'connected', 'the edit must land')
        self.assertEqual(
            after['created_at'],
            before['created_at'],
            'a correction must not rewrite when the lead was created, not even its '
            'sub-second digits',
        )
        self.assertGreater(
            after['updated_at'],
            before['updated_at'],
            'advancing a lead must move the freshness signal `bin/lead.mjs` maintains',
        )

    def test_the_edited_lead_rises_to_the_top_of_the_changelist(self):
        before = self.probe(LEADS_ORDER)
        self.assertEqual(before['status'], 200)
        self.assertEqual(
            before['ids'],
            [control_id, lead_id],
            'the control lead was touched later, so it must start on top — without it '
            'the assertion below would pass whatever the edit did',
        )

        self.post(
            'admin:social_leads_change', self.lead_payload(stage='connected'), args=[lead_id]
        )

        after = self.probe(LEADS_ORDER)
        self.assertEqual(
            after['ids'],
            [lead_id, control_id],
            'the just-advanced lead must be the first row under -updated_at',
        )

    def test_editing_a_person_preserves_created_at(self):
        before = _person(ANNA)
        new_name = 'Анна Петрова (исправлено)'

        result = self.post(
            'admin:social_people_change', self.person_payload(ANNA, new_name), args=[ANNA]
        )

        self.assertSaved(result, 'correcting a person')
        after = _person(ANNA)
        self.assertEqual(after['name'], new_name, 'the correction must land')
        self.assertEqual(
            after['created_at'],
            before['created_at'],
            'a correction must leave `created_at` byte-for-byte as it was',
        )


class ScratchSchemaTest(AdminWriteTestCase):
    """Positive control: the harness really did build the social schema.

    Without this an empty or half-loaded database could make the examples above
    pass for the wrong reason.
    """

    def test_schema_sql_loaded_the_tables_these_examples_write(self):
        tables = database.tables()
        for table in ('people', 'leads'):
            with self.subTest(table=table):
                self.assertIn(table, tables)

    def test_the_fixture_holds_the_lead_under_test_and_its_control(self):
        rows = _leads()
        self.assertEqual([row['id'] for row in rows], [lead_id, control_id])
        self.assertLess(
            rows[0]['updated_at'],
            rows[1]['updated_at'],
            'the control must be the more recently touched of the two',
        )


if __name__ == '__main__':
    unittest.main()

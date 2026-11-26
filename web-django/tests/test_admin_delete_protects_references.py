"""Deleting a referenced row through the admin refuses; it does not crash.

`schema.sql` gives none of its six foreign keys an `ON DELETE` clause, so every
one of them is `NO ACTION`: the database's answer to deleting a row another
table still references is *refuse*. `inspectdb` emitted `models.DO_NOTHING` for
all six, which means "issue no related query and raise nothing" — so Django's
admin collector saw no obstacle, the confirmation page promised the delete and
listed none of the rows standing in the way, the view issued the `DELETE`, and
Postgres refused it. With `DEBUG` off (what the project pins) the operator of
the ops surface knowledge scout-3 decision 3 names got Django's bare
`Server Error (500)` page, which names neither the row nor the reason. No data
was lost — the database held — so the whole defect was on the surface.

`models.PROTECT` is the same refusal expressed where Django can act on it:
`admin.utils.NestedObjects.collect` catches `ProtectedError` into its
`protected` list, and `ModelAdmin.delete_view` / `actions.delete_selected` then
refuse *before* touching the database and name the protecting rows.

Two halves, the convention of the sibling modules:

  1. metadata, no database — a census of every foreign key on the six models,
     with the set pinned *whole*: a future `inspectdb` re-run that re-introduces
     a `DO_NOTHING` key, or drops one of these six, fails here. ⚠️
     `Leads.person` is a `OneToOneField`, so a census filtering with
     `type(field) is models.ForeignKey` would silently miss it — and it is the
     one key whose column is `NOT NULL` (`person text NOT NULL REFERENCES
     people(id)`), the one for which no `SET_NULL` alternative exists at all;
     the census asserts that declared type too, so the trap stays named.
  2. rendered writes, against a scratch database built from `schema.sql` by
     `tests/_db.py` — every acceptance arm as a **triple**: the status code, the
     presence of the confirm button in the rendered HTML, and the rows still in
     the database. A status alone is not evidence: a 200 that silently deleted
     the row would pass it, and "the row survived" alone is satisfied by the
     unpatched 500, where the *database* refused the statement. The unreferenced
     control is the other half of the teeth: without it "nothing crashes" could
     be satisfied by refusing every delete.

Teeth, measured: with all six keys back on `DO_NOTHING` the person arm, the
bulk arm and the interactions arm each answer 500 (`drafts_target_person_fkey`,
`interactions_in_reply_to_fkey`) and twelve of the thirteen examples fail.
Reverting a *single* key is caught by the census half, not necessarily by the
rendered half: the protected person here is referenced three times over, so one
key going back to `DO_NOTHING` still leaves the other two to refuse the delete.

⚠️ Each example drives exactly ONE admin request per probe process, which is
also what the unpatched baseline needs: measured, the next delete in the same
process — even of an unreferenced row — fails on the admin log's own
`content_type_id` foreign key, because the `ContentType` row the log needs was
created inside the transaction the `IntegrityError` then rolled back while the
in-memory content-type cache kept its id (`ContentType.objects.filter(
app_label='social')` reads `[]` on a fresh database — those rows are created
lazily by the first admin write, not by `migrate`). That is a probe artifact,
not a product defect.

With no reachable Postgres the second half skips honestly and the suite stays
green, exactly as in `tests/test_queue_transitions.py`.
"""

import json
import os
import re
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_delete_test_{os.getpid()}'

OPERATOR = 'delete-tests-operator'

# --- what the foreign keys must declare -----------------------------------

# Every foreign key in `social/models.py`, with the target it points at and the
# declared field class. Pinned as a whole mapping rather than by membership:
# an added key must be considered here, and a dropped one is a regression too.
# `Leads.person` is the `OneToOneField` (schema.sql carries the unique index
# `leads_person_uniq` over `leads(person)`), and the only `NOT NULL` column of
# the six.
FOREIGN_KEYS = {
    'Drafts.target_person': {'target': 'People', 'class': 'ForeignKey', 'null': True},
    'Interactions.in_reply_to': {'target': 'Interactions', 'class': 'ForeignKey', 'null': True},
    'Interactions.draft': {'target': 'Drafts', 'class': 'ForeignKey', 'null': True},
    'Questions.asker': {'target': 'People', 'class': 'ForeignKey', 'null': True},
    'Questions.source': {'target': 'Interactions', 'class': 'ForeignKey', 'null': True},
    'Leads.person': {'target': 'People', 'class': 'OneToOneField', 'null': False},
}

# --- fixture ---------------------------------------------------------------

# The protected person: a lead, a draft and a question all reference her.
ANNA = 'li:anna'
# The control: referenced by nothing, so her delete must still go through.
ZOYA = 'li:zoya'

# The protected interaction (`i-1`) and the two rows that reference it: a reply
# through `interactions.in_reply_to` and a question through `questions.source`.
SOURCE_INTERACTION = 'i-1'
REPLY_INTERACTION = 'i-2'
QUESTION = 'q-1'

PEOPLE_IDS = [ANNA, ZOYA]
INTERACTION_IDS = [SOURCE_INTERACTION, REPLY_INTERACTION]

# --- probes ----------------------------------------------------------------

READ_FOREIGN_KEYS = """
import json
import django
django.setup()
from django.db import models
from social.models import Drafts, Interactions, Leads, Ledger, People, Questions

# Every model of the app, so a new foreign key anywhere shows up in the census.
MODELS = (People, Drafts, Interactions, Questions, Ledger, Leads)

keys = {}
for model in MODELS:
    for declared in model._meta.fields:
        # `isinstance`, not `type(...) is`: OneToOneField subclasses ForeignKey,
        # and `Leads.person` is a OneToOneField.
        if not isinstance(declared, models.ForeignKey):
            continue
        on_delete = declared.remote_field.on_delete
        keys[f'{model.__name__}.{declared.name}'] = {
            'target': declared.related_model.__name__,
            'class': type(declared).__name__,
            'null': declared.null,
            'db_column': declared.db_column,
            'on_delete': getattr(on_delete, '__name__', repr(on_delete)),
            'is_protect': on_delete is models.PROTECT,
            'is_do_nothing': on_delete is models.DO_NOTHING,
        }

print(json.dumps(keys))
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='delete-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

# `setup_test_environment()` is what puts `testserver` into ALLOWED_HOSTS,
# which the settings deliberately keep to the two loopback names. It is also
# what makes `response.context` available, so the obstacle lists the template
# renders can be read structurally instead of grepped out of the page.
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

OPERATOR = 'delete-tests-operator'


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username=OPERATOR))
    return client


def context_list(response, key):
    # None when the response rendered no such context (a redirect has none).
    try:
        value = response.context[key]
    except (KeyError, TypeError, AttributeError):
        return None
    return [str(item) for item in (value or [])]
"""

# Exactly one admin request, GET or POST, reported as the triple the examples
# assert on: the status, whether the rendered HTML carries the confirm button,
# and the obstacles the page names. An IntegrityError is caught and reported as
# the 500 it is on the surface, so the unpatched baseline fails with a readable
# message rather than a subprocess traceback.
ADMIN_REQUEST = HTTP_PRELUDE + """
route = os.environ['PROBE_ROUTE']
args = json.loads(os.environ.get('PROBE_ARGS', '[]'))
payload = json.loads(os.environ.get('PROBE_PAYLOAD', 'null'))

url = reverse(route, args=args)
client = operator_client()
try:
    response = client.post(url, payload) if payload is not None else client.get(url)
except Exception as error:
    print(json.dumps({
        'status': 500,
        'exception': f'{type(error).__name__}: {error}',
        'confirm_button': None,
        'protected': None,
        'perms_lacking': None,
        'location': None,
    }))
else:
    body = response.content.decode('utf-8')
    print(json.dumps({
        'status': response.status_code,
        'exception': None,
        # The confirm button itself, read out of the rendered page: a page-level
        # grep for the refusal's Russian text is not evidence, and the refusal
        # is worded two ways (protected objects vs. missing permissions).
        'confirm_button': 'name="post"' in body,
        'protected': context_list(response, 'protected'),
        'perms_lacking': context_list(response, 'perms_lacking'),
        'location': response.headers.get('Location'),
    }))
"""

# --- scratch database lifecycle -------------------------------------------

database = None
lead_id = None
draft_id = None

# The admin change URL of each row that protects something, as the rendered
# obstacle list links to it. Filled once the fixture has its generated ids.
PROTECTING_URLS = {}


def setUpModule():
    global database
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
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _seed():
    global lead_id, draft_id
    for person_id, name in ((ANNA, 'Анна Петрова'), (ZOYA, 'Зоя Одинокая')):
        database.sql(
            'insert into people (id, name, network) values (%s, %s, %s)',
            (person_id, name, 'linkedin'),
        )
    lead_id = database.sql(
        "insert into leads (person, stage) values (%s, 'commented') returning id", (ANNA,)
    )[0]['id']
    draft_id = database.sql(
        """insert into drafts (network, kind, status, target_person, body)
           values ('linkedin', 'comment', 'waiting', %s, %s) returning id""",
        (ANNA, 'Анна, про очередь утверждения'),
    )[0]['id']
    database.sql(
        """insert into interactions (id, ts, network, direction, kind, text)
           values (%s, '2026-11-20 10:00', 'linkedin', 'in', 'comment', %s)""",
        (SOURCE_INTERACTION, 'а как вы это согласуете?'),
    )
    database.sql(
        """insert into interactions (id, ts, network, direction, kind, in_reply_to, text)
           values (%s, '2026-11-21 09:30', 'linkedin', 'out', 'reply', %s, %s)""",
        (REPLY_INTERACTION, SOURCE_INTERACTION, 'через очередь утверждения'),
    )
    database.sql(
        """insert into questions (id, date, network, asker, source, question)
           values (%s, '2026-11-20', 'linkedin', %s, %s, %s)""",
        (QUESTION, ANNA, SOURCE_INTERACTION, 'как вы согласуете ответы?'),
    )
    PROTECTING_URLS.update(
        lead=f'/admin/social/leads/{lead_id}/change/',
        draft=f'/admin/social/drafts/{draft_id}/change/',
        question=f'/admin/social/questions/{QUESTION}/change/',
        reply=f'/admin/social/interactions/{REPLY_INTERACTION}/change/',
    )


def _ids(table):
    return [row['id'] for row in database.sql(f'select id from {table} order by id')]


# --- the metadata half -----------------------------------------------------


class ForeignKeyDeleteBehaviourTest(unittest.TestCase):
    """No database: what every foreign key of the app declares for `on_delete`."""

    def setUp(self):
        result = run_probe(
            READ_FOREIGN_KEYS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.keys = json.loads(result.stdout)

    def test_the_census_is_exactly_the_six_foreign_keys_of_schema_sql(self):
        # Whole-set, not membership: a re-run of `inspectdb` that adds a key
        # (with `DO_NOTHING` again) or a hand edit that drops one both fail here
        # instead of leaving the new state unexamined.
        self.assertEqual(
            {name: {k: v for k, v in key.items() if k in ('target', 'class', 'null')}
             for name, key in self.keys.items()},
            FOREIGN_KEYS,
        )

    def test_every_foreign_key_protects_the_row_it_references(self):
        for name in FOREIGN_KEYS:
            with self.subTest(foreign_key=name):
                key = self.keys[name]
                self.assertTrue(
                    key['is_protect'],
                    f'{name} declares on_delete={key["on_delete"]}; schema.sql has no '
                    f'ON DELETE on any of its six REFERENCES clauses, so the database '
                    f'refuses — PROTECT is that refusal where Django can act on it',
                )
                self.assertFalse(
                    key['is_do_nothing'],
                    f'{name} back on DO_NOTHING: the admin would promise the delete and '
                    f'answer 500',
                )

    def test_the_one_to_one_key_is_part_of_the_census(self):
        # `Leads.person` is a OneToOneField, so a census filtering with
        # `type(field) is models.ForeignKey` would omit it — and it is the one
        # NOT NULL column of the six, so it is also the one key that could not
        # have been given `SET_NULL` instead.
        key = self.keys['Leads.person']
        self.assertEqual(key['class'], 'OneToOneField')
        self.assertFalse(key['null'], 'leads.person is `text NOT NULL` in schema.sql')
        self.assertTrue(key['is_protect'])

    def test_the_columns_the_keys_write_are_the_schema_sql_ones(self):
        # The delete behaviour is the only thing this change touches: the
        # columns must be exactly what they were.
        self.assertEqual(
            {name: key['db_column'] for name, key in self.keys.items()},
            {
                'Drafts.target_person': 'target_person',
                'Interactions.in_reply_to': 'in_reply_to',
                'Interactions.draft': 'draft_id',
                'Questions.asker': 'asker',
                'Questions.source': 'source',
                'Leads.person': 'person',
            },
        )


# --- the rendered half -----------------------------------------------------


class AdminDeleteTestCase(unittest.TestCase):
    """Shared probe handling: skip without a database, restore a deleted row."""

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def tearDown(self):
        if database is None:
            return
        # Only the control example deletes anything; put it back.
        if ZOYA not in _ids('people'):
            database.sql(
                'insert into people (id, name, network) values (%s, %s, %s)',
                (ZOYA, 'Зоя Одинокая', 'linkedin'),
            )

    # --- helpers ---------------------------------------------------------

    def request(self, route, args=(), payload=None):
        """One admin request in its own process (see the module docstring)."""
        overrides = {'PROBE_ROUTE': route, 'PROBE_ARGS': json.dumps(list(args))}
        if payload is not None:
            overrides['PROBE_PAYLOAD'] = json.dumps(payload)
        result = run_probe(
            ADMIN_REQUEST, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def confirm_delete(self, route, row_id):
        return self.request(route, args=[row_id], payload={'post': 'yes'})

    def obstacle_urls(self, result):
        """The admin change URLs the refusal links to, over both obstacle lists.

        A protected object renders as a link to its change page; the permissions
        list carries bare model names, so it contributes URLs only where the
        same row is also in `protected`.
        """
        named = (result['protected'] or []) + (result['perms_lacking'] or [])
        return set(re.findall(r'href="([^"]+)"', ' '.join(named)))

    def assertRefused(self, result, what):
        self.assertIsNone(result['exception'], f'{what} raised: {result["exception"]}')
        self.assertEqual(
            result['status'],
            200,
            f'{what} must be refused on the page, never crash or redirect',
        )
        self.assertFalse(
            result['confirm_button'],
            f'{what} must render no confirm button: the delete cannot succeed',
        )


class DeletingAProtectedPersonTest(AdminDeleteTestCase):
    def test_the_delete_page_refuses_and_names_the_rows_in_the_way(self):
        result = self.request('admin:social_people_delete', args=[ANNA])

        self.assertRefused(result, 'the delete page for a referenced person')
        self.assertEqual(
            self.obstacle_urls(result),
            {PROTECTING_URLS['lead'], PROTECTING_URLS['draft'], PROTECTING_URLS['question']},
            'the page must name the lead, the draft and the question that reference her',
        )
        self.assertEqual(_ids('people'), PEOPLE_IDS, 'rendering a page deletes nothing')

    def test_confirming_the_delete_refuses_and_keeps_the_row(self):
        # The arm that answered 500: the confirm button was rendered, the view
        # issued the DELETE and Postgres refused it.
        result = self.confirm_delete('admin:social_people_delete', ANNA)

        self.assertRefused(result, 'confirming a referenced person')
        self.assertIn(
            PROTECTING_URLS['lead'],
            self.obstacle_urls(result),
            'the refusal must still name what protects her',
        )
        self.assertEqual(_ids('people'), PEOPLE_IDS, 'the person must still be there')

    def test_the_bulk_action_refuses_and_keeps_the_row(self):
        result = self.request(
            'admin:social_people_changelist',
            payload={'action': 'delete_selected', '_selected_action': [ANNA], 'post': 'yes'},
        )

        self.assertRefused(result, 'the bulk delete of a referenced person')
        self.assertIn(PROTECTING_URLS['lead'], self.obstacle_urls(result))
        self.assertEqual(_ids('people'), PEOPLE_IDS, 'the person must still be there')


class DeletingAProtectedInteractionTest(AdminDeleteTestCase):
    def test_the_delete_page_names_both_rows_that_reference_it(self):
        result = self.request('admin:social_interactions_delete', args=[SOURCE_INTERACTION])

        self.assertRefused(result, 'the delete page for a referenced interaction')
        self.assertEqual(
            self.obstacle_urls(result),
            {PROTECTING_URLS['reply'], PROTECTING_URLS['question']},
            'both the replying interaction (in_reply_to) and the question (source) '
            'protect it, and both must be named',
        )
        self.assertEqual(_ids('interactions'), INTERACTION_IDS)

    def test_confirming_the_delete_refuses_and_keeps_the_row(self):
        # The `interactions_in_reply_to_fkey` 500.
        result = self.confirm_delete('admin:social_interactions_delete', SOURCE_INTERACTION)

        self.assertRefused(result, 'confirming a referenced interaction')
        self.assertIn(PROTECTING_URLS['reply'], self.obstacle_urls(result))
        self.assertEqual(
            _ids('interactions'), INTERACTION_IDS, 'the interaction must still be there'
        )


class DeletingAnUnreferencedRowTest(AdminDeleteTestCase):
    """The control: `PROTECT` must refuse only what the database refuses."""

    def test_the_delete_page_still_offers_the_confirm_button(self):
        result = self.request('admin:social_people_delete', args=[ZOYA])

        self.assertIsNone(result['exception'])
        self.assertEqual(result['status'], 200)
        self.assertTrue(
            result['confirm_button'],
            'an unreferenced person must still be deletable — without this the '
            '"no confirm button" assertions above would pass for a surface that '
            'refuses everything',
        )
        self.assertEqual(result['protected'], [])
        self.assertEqual(result['perms_lacking'], [])

    def test_confirming_the_delete_removes_the_row(self):
        result = self.confirm_delete('admin:social_people_delete', ZOYA)

        self.assertIsNone(result['exception'])
        self.assertEqual(result['status'], 302, 'the delete must go through')
        self.assertEqual(result['location'], '/admin/social/people/')
        self.assertEqual(_ids('people'), [ANNA], 'the unreferenced person must be gone')


class ScratchSchemaTest(AdminDeleteTestCase):
    """Positive control: the harness really did build the fixture it asserts on.

    Without this an empty or half-loaded database could make the examples above
    pass for the wrong reason — a delete of a row that was never there refuses
    nothing.
    """

    def test_schema_sql_loaded_the_tables_these_examples_write(self):
        tables = database.tables()
        for table in ('people', 'leads', 'drafts', 'interactions', 'questions'):
            with self.subTest(table=table):
                self.assertIn(table, tables)

    def test_the_fixture_holds_a_protected_row_and_its_unreferenced_control(self):
        self.assertEqual(_ids('people'), PEOPLE_IDS)
        self.assertEqual(_ids('interactions'), INTERACTION_IDS)
        referencing_anna = (
            database.sql('select count(*) as n from leads where person = %s', (ANNA,))[0]['n']
            + database.sql(
                'select count(*) as n from drafts where target_person = %s', (ANNA,)
            )[0]['n']
            + database.sql('select count(*) as n from questions where asker = %s', (ANNA,))[0]['n']
        )
        self.assertEqual(referencing_anna, 3, 'three rows must reference the protected person')
        self.assertEqual(
            database.sql(
                'select count(*) as n from people where id = %s and id not in ('
                '  select person from leads union all'
                '  select target_person from drafts where target_person is not null union all'
                '  select asker from questions where asker is not null)',
                (ZOYA,),
            )[0]['n'],
            1,
            'the control person must be referenced by nothing',
        )


if __name__ == '__main__':
    unittest.main()

"""An existing row's primary key is not an editable admin field.

`people`, `interactions` and `questions` carry an operator-supplied `text
PRIMARY KEY`, so Django rendered the key as an ordinary field on the *change*
form as well as the add form — on origin/main as a 10-row `required`
`<textarea>`. Editing it there does not rename the row. Django runs no
uniqueness check on a primary key once the instance exists
(`Model._perform_unique_checks` skips the pk unless `_state.adding`), and
`Model._save_table` then issues `UPDATE … WHERE id = <the submitted value>`:

  * no row matches, the save falls through to an INSERT, and the edit lands on
    a **copy** while every reference still points at the original;
  * a *different* row matches and that row's columns are **overwritten** with
    this form's values.

The admin reports success either way, and the overwrite leaves the row count
untouched, so no count-based surface reveals it. Measured on this project's own
data: `q-003` resubmitted with its id changed to `q-004` — one slipped digit —
replaced `q-004`'s recorded market question. That is the correction knowledge
scout-3 decision 3 puts on this surface in the first place, against decision
2's requirement that existing data survive untouched.

`social.admin.FrozenPrimaryKeyAdmin` makes the key readonly as soon as there is
a row, which takes it out of the generated form entirely, so a submitted value
cannot reach the instance.

Two halves, the convention of the sibling modules:

  1. metadata, no database — each admin's add and change forms read out of
     `admin.site._registry` in a probe subprocess (`django.setup()` needs no
     Postgres). `drafts`, `ledger` and `leads` are the control: their
     `BigAutoField` key reaches no form, so freezing must not give their detail
     pages a key row they never had. That half also pins *why* the guard reads
     `pk.formfield() is None` — `pk.editable` is `True` on all six models and
     discriminates nothing.
  2. rendered writes, against a scratch database built from `schema.sql` by
     `tests/_db.py` — the change page's own HTML, both destructive arms, both
     add-form arms, and an ordinary edit on each of the five writable admins.

The write examples **submit what the page rendered** (`tests/_formfields.py`)
and put the key back by hand. A hand-built payload would prove nothing here:
the whole guard is that the field is absent from the form, so a test that
types the field names itself cannot tell a frozen key from a missing template.

With no reachable Postgres the second half skips honestly and the suite stays
green, exactly as in `tests/test_queue_transitions.py`.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_frozen_pk_test_{os.getpid()}'

OPERATOR = 'frozen-pk-tests-operator'

# --- the census this change is about --------------------------------------

# The key is the operator's to type, so it must stay on the add form and leave
# the change form. `schema.sql` declares all three as `text PRIMARY KEY`.
OPERATOR_SUPPLIED_KEY = {
    'People': 'people_form',
    'Interactions': 'interactions_form',
    'Questions': 'questions_form',
}

# The control: `bigserial PRIMARY KEY` in `schema.sql`, which reaches no form
# at all, so neither page may grow a key row. `Drafts` is view-only anyway
# (`DraftsAdmin`), and is here because the census is about every registered
# admin rather than only the writable ones.
DATABASE_GENERATED_KEY = ('Drafts', 'Ledger', 'Leads')

# --- fixture ---------------------------------------------------------------

ANNA = 'li:anna'
ANNA_NAME = 'Анна Петрова'
BORIS = 'li:boris'
BORIS_NAME = 'Борис Смирнов'

# The slug an operator would "correct" to. On origin/main this is the id the
# copy is written under.
ANNA_CORRECTED = 'li:anna-petrova'
ANNA_NEW_NAME = 'Анна Петрова (Yatfa)'

# Two questions one keystroke apart, which is this project's real id scheme
# (`q-001`…`q-007`).
Q_EDITED = 'q-003'
Q_NEIGHBOUR = 'q-004'
Q_EDITED_TEXT = 'как у вас устроено согласование постов?'
Q_NEIGHBOUR_TEXT = 'сколько стоит внедрение и кто его ведёт?'
Q_NEW = 'q-900'
Q_NEW_TEXT = 'кто отвечает за тон голоса в ответах?'

# The same shape one table over: hand-minted sequentials.
I_EDITED = 'li-20261023-01'
I_NEIGHBOUR = 'li-20261023-02'
I_EDITED_TEXT = 'спасибо, разберём на следующей неделе'
I_NEIGHBOUR_TEXT = 'а можно пример по нашей отрасли?'

LEDGER_TOPIC = 'очередь согласований'

# --- probes ----------------------------------------------------------------

# A request complete enough to assemble an admin form with no database: both
# `has_perm` and `has_perms` are consulted while Django decides whether a
# related field gets its "add another" control, and `request=None` raises
# instead (`formfield_for_dbfield` reads `request.user`).
READ_DECLARATIONS = """
import json
import django
django.setup()
from django.contrib import admin
from social.models import Drafts, Interactions, Leads, Ledger, People, Questions


class _User:
    is_active = is_staff = is_superuser = True

    def has_perm(self, *args, **kwargs):
        return True

    def has_perms(self, *args, **kwargs):
        return True


class _Request:
    user = _User()
    GET = {}
    META = {}


request = _Request()


def read(model):
    model_admin = admin.site._registry[model]
    key = model._meta.pk
    # An unsaved instance is enough to select the change form: `get_form`
    # branches on `obj is None`, and nothing here touches a row.
    unsaved = model()
    add_form = model_admin.get_form(request)()
    change_form = model_admin.get_form(request, unsaved)()
    return {
        'pk': key.name,
        'pk_type': type(key).__name__,
        # The two candidate discriminators, measured side by side.
        'pk_editable': key.editable,
        'pk_reaches_a_form': key.formfield() is not None,
        'add_form_fields': list(add_form.fields),
        'add_form_required': [name for name, f in add_form.fields.items() if f.required],
        'change_form_fields': list(change_form.fields),
        'readonly_on_add': list(model_admin.get_readonly_fields(request, None)),
        'readonly_on_change': list(model_admin.get_readonly_fields(request, unsaved)),
        # What the detail page lays out: `ModelAdmin.get_fields` is the form's
        # fields plus `readonly_fields`, which is why a frozen key still shows.
        'change_fieldset_fields': [
            name
            for _, options in model_admin.get_fieldsets(request, unsaved)
            for name in options['fields']
        ],
        'list_editable': list(model_admin.list_editable),
    }


print(json.dumps({
    m.__name__: read(m)
    for m in (People, Drafts, Interactions, Questions, Ledger, Leads)
}))
"""

# `setup_test_environment()` is what puts `testserver` into ALLOWED_HOSTS,
# which the settings deliberately keep to the two loopback names.
HTTP_PRELUDE = """
import json
import os
import re
import django
django.setup()
from django.test.utils import setup_test_environment
setup_test_environment()
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests._formfields import form_payload, readonly_values

ERRORLIST = re.compile(r'<ul class="errorlist[^"]*"[^>]*>(.*?)</ul>', re.S)
ITEM = re.compile(r'<li[^>]*>(.*?)</li>', re.S)
TAG = re.compile(r'<[^>]+>')


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username='frozen-pk-tests-operator'))
    return client


def errors_of(body):
    \"\"\"The form errors the page is showing, as plain text.

    A refused admin save is a 200 with the form re-rendered, not an error
    status, so the examples need the page's own complaint to tell a refusal
    from a save.
    \"\"\"
    out = []
    for block in ERRORLIST.findall(body):
        for item in ITEM.findall(block):
            text = ' '.join(TAG.sub(' ', item).split())
            if text:
                out.append(text)
    return out
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='frozen-pk-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

# What one admin page renders: the form's own fields, and the values it shows
# as readonly instead of as inputs.
RENDER_FORM = HTTP_PRELUDE + """
args = json.loads(os.environ.get('PROBE_ARGS', '[]'))
response = operator_client().get(reverse(os.environ['PROBE_ROUTE'], args=args))
body = response.content.decode('utf-8')
print(json.dumps({
    'status': response.status_code,
    'rendered_fields': list(form_payload(body, os.environ['PROBE_FORM_ID'])),
    'readonly_values': readonly_values(body),
}))
"""

# Render a form, submit exactly what it rendered with the given fields put back
# by hand, and report what the page said. This is the shape the guard requires:
# a readonly field is simply absent from the form, so only a payload taken off
# the page can show that re-adding the key by hand changes nothing.
RENDER_AND_POST = HTTP_PRELUDE + """
args = json.loads(os.environ.get('PROBE_ARGS', '[]'))
overrides = json.loads(os.environ['PROBE_OVERRIDES'])

client = operator_client()
url = reverse(os.environ['PROBE_ROUTE'], args=args)
page = client.get(url)
body = page.content.decode('utf-8')
rendered = form_payload(body, os.environ['PROBE_FORM_ID'])

payload = dict(rendered)
payload.update(overrides)
# A browser sends the name of the button the operator clicked, and no other.
payload['_save'] = 'Сохранить'

response = client.post(url, payload)
print(json.dumps({
    'get_status': page.status_code,
    'rendered_fields': list(rendered),
    'readonly_values': readonly_values(body),
    'status': response.status_code,
    'location': response.headers.get('Location'),
    'errors': (
        errors_of(response.content.decode('utf-8')) if response.status_code == 200 else []
    ),
}))
"""

# --- scratch database lifecycle -------------------------------------------

database = None
draft_id = None
lead_id = None
ledger_id = None


def setUpModule():
    global database
    database = _db.scratch_database(SCRATCH_DB)
    if database is None:
        return
    try:
        # The admin's login gate needs real users and sessions. `migrate`
        # creates only the `django_*`/`auth_*` tables — every social model is
        # unmanaged — so it cannot touch the schema just loaded from schema.sql.
        result = run_probe(
            MIGRATE_AND_CREATE_OPERATOR, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET
        )
        assert result.returncode == 0, result.stderr
        _reseed()
        # Positive control on the harness itself: without it an empty or
        # half-loaded database would let the examples below pass for the wrong
        # reason.
        tables = database.tables()
        for table in ('people', 'interactions', 'questions', 'leads', 'ledger', 'drafts'):
            assert table in tables, f'schema.sql did not load {table}'
        assert [row['id'] for row in _people()] == [ANNA, BORIS]
        assert [row['id'] for row in _questions()] == [Q_EDITED, Q_NEIGHBOUR]
        assert [row['id'] for row in _interactions()] == [I_EDITED, I_NEIGHBOUR]
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _reseed():
    """The fixture as every example expects to find it.

    Rebuilt rather than patched back up: these examples write under *new* keys
    by design, so "undo the columns I changed" cannot describe the damage an
    unguarded save does.
    """
    global draft_id, lead_id, ledger_id
    # Referencing tables first; none of the six `REFERENCES` in `schema.sql`
    # carries an `ON DELETE`, so the order is the database's requirement.
    for table in ('questions', 'leads', 'interactions', 'drafts', 'ledger', 'people'):
        database.sql(f'delete from {table}')

    for person_id, name, headline in (
        (ANNA, ANNA_NAME, 'операционный директор'),
        (BORIS, BORIS_NAME, 'руководитель продукта'),
    ):
        database.sql(
            'insert into people (id, name, network, profile, headline) values '
            '(%s, %s, %s, %s, %s)',
            (person_id, name, 'linkedin', f'https://www.linkedin.com/in/{name}', headline),
        )

    # A draft, a question and an interaction that all name `li:anna`: what a
    # forked person row leaves behind, pointing at the id nobody corrected.
    draft_id = database.sql(
        """insert into drafts (network, kind, status, target_person, body)
           values ('linkedin', 'comment', 'waiting', %s, %s) returning id""",
        (ANNA, 'ответ про очередь согласований'),
    )[0]['id']
    for interaction_id, text in ((I_EDITED, I_EDITED_TEXT), (I_NEIGHBOUR, I_NEIGHBOUR_TEXT)):
        database.sql(
            """insert into interactions (id, ts, network, direction, kind, person, text)
               values (%s, %s, 'linkedin', 'in', 'comment', %s, %s)""",
            (interaction_id, '2026-10-23 10:00', ANNA, text),
        )
    for question_id, text in ((Q_EDITED, Q_EDITED_TEXT), (Q_NEIGHBOUR, Q_NEIGHBOUR_TEXT)):
        database.sql(
            """insert into questions (id, date, network, asker, question, topic)
               values (%s, %s, 'linkedin', %s, %s, 'процесс')""",
            (question_id, '2026-11-01', ANNA, text),
        )
    lead_id = database.sql(
        """insert into leads (person, stage, pain, next_step)
           values (%s, 'candidate', %s, 'прислать разбор очереди') returning id""",
        (ANNA, 'каждое согласование — это переписка на три дня'),
    )[0]['id']
    ledger_id = database.sql(
        """insert into ledger (at, network, topic, outcome)
           values ('2026-11-02 09:00', 'linkedin', %s, 'опубликовано') returning id""",
        (LEDGER_TOPIC,),
    )[0]['id']


def _people():
    return database.sql('select id, name, headline from people order by id')


def _questions():
    return database.sql('select id, date, network, question, topic from questions order by id')


def _interactions():
    return database.sql('select id, ts, network, person, text from interactions order by id')


def _row(table, row_id):
    rows = database.sql(f'select * from {table} where id = %s', (row_id,))
    assert len(rows) == 1, f'{table} {row_id!r} must exist'
    return rows[0]


# --- the metadata half -----------------------------------------------------


class AdminKeyDeclarationTest(unittest.TestCase):
    """No database: which form each admin's primary key reaches."""

    def setUp(self):
        result = run_probe(
            READ_DECLARATIONS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.admins = json.loads(result.stdout)

    def test_an_operator_supplied_key_leaves_the_change_form(self):
        for model in OPERATOR_SUPPLIED_KEY:
            with self.subTest(model=model):
                declared = self.admins[model]
                self.assertNotIn(
                    declared['pk'],
                    declared['change_form_fields'],
                    f'{model}: a submitted key must not be able to reach the instance',
                )
                self.assertIn(
                    declared['pk'],
                    declared['readonly_on_change'],
                    f'{model}: the key is what must be frozen, not some other column',
                )
                # Frozen, not hidden: the operator still needs to see which row
                # they are editing.
                self.assertIn(
                    declared['pk'],
                    declared['change_fieldset_fields'],
                    f'{model}: the key must still be laid out on the detail page',
                )
                # The changelist is not a second edit path for it.
                self.assertEqual(declared['list_editable'], [])

    def test_the_add_form_still_asks_for_the_key(self):
        # There is no generated value to fall back on: a `text PRIMARY KEY` row
        # cannot be created without the operator typing its id.
        for model in OPERATOR_SUPPLIED_KEY:
            with self.subTest(model=model):
                declared = self.admins[model]
                self.assertIn(declared['pk'], declared['add_form_fields'])
                self.assertIn(declared['pk'], declared['add_form_required'])
                self.assertEqual(
                    declared['readonly_on_add'],
                    [],
                    f'{model}: nothing is frozen on the add form',
                )

    def test_a_database_generated_key_is_left_alone(self):
        # The control, and the reason the guard asks whether the key reaches a
        # form rather than whether it is `editable`: `editable` is True on all
        # six keys, `BigAutoField` included (`AutoFieldMixin.formfield` returns
        # None instead), so freezing on it would put a readonly `id` row on
        # these three detail pages, which never had one.
        for model in self.admins:
            with self.subTest(model=model):
                self.assertTrue(
                    self.admins[model]['pk_editable'],
                    'premise: `editable` does not discriminate, so it cannot be the test',
                )
        self.assertEqual(
            sorted(m for m, d in self.admins.items() if d['pk_reaches_a_form']),
            sorted(OPERATOR_SUPPLIED_KEY),
            '`formfield() is None` is what splits the six keys',
        )
        for model in DATABASE_GENERATED_KEY:
            with self.subTest(model=model):
                declared = self.admins[model]
                self.assertEqual(declared['pk_type'], 'BigAutoField')
                self.assertEqual(
                    declared['readonly_on_change'],
                    [],
                    f'{model}: nothing to freeze — the key reaches no form',
                )
                self.assertNotIn(declared['pk'], declared['change_fieldset_fields'])


# --- the rendered half -----------------------------------------------------


class AdminKeyWriteTestCase(unittest.TestCase):
    """Shared fixture handling: skip without a database, reseed after each example."""

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def tearDown(self):
        if database is not None:
            _reseed()

    # --- helpers ---------------------------------------------------------

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def render(self, route, form_id, args=()):
        result = self.probe(
            RENDER_FORM,
            PROBE_ROUTE=route,
            PROBE_FORM_ID=form_id,
            PROBE_ARGS=json.dumps(list(args)),
        )
        self.assertEqual(result['status'], 200, f'{route} must render')
        return result

    def submit(self, route, form_id, overrides, args=()):
        """Render a form, put `overrides` back into what it rendered, submit."""
        result = self.probe(
            RENDER_AND_POST,
            PROBE_ROUTE=route,
            PROBE_FORM_ID=form_id,
            PROBE_ARGS=json.dumps(list(args)),
            PROBE_OVERRIDES=json.dumps(overrides),
        )
        self.assertEqual(result['get_status'], 200, f'{route} must render')
        return result

    def assertSaved(self, result, what):
        self.assertEqual(
            result['status'],
            302,
            f'{what} must save and redirect; the page said {result["errors"]}',
        )

    def assertRefused(self, result, what):
        # A refused admin save is a 200 with the form re-rendered, which reads
        # like success to a status check alone.
        self.assertEqual(result['status'], 200, f'{what} must be refused, not saved')
        self.assertTrue(result['errors'], f'{what} must say why it was refused')


class ChangeFormRendersTheKeyTest(AdminKeyWriteTestCase):
    def test_the_change_page_shows_the_key_and_offers_no_input_for_it(self):
        rendered = self.render(
            'admin:social_questions_change', 'questions_form', args=[Q_EDITED]
        )

        self.assertNotIn(
            'id',
            rendered['rendered_fields'],
            'the key must not be an input on the change page — on origin/main it was a '
            'required 10-row textarea',
        )
        self.assertIn(
            Q_EDITED,
            rendered['readonly_values'],
            'the key must still be shown, so the operator can see which row this is',
        )
        # The rest of the form is untouched: freezing one field must not cost
        # the page its editable columns.
        for column in ('date', 'network', 'question', 'topic', 'yatfa_answer'):
            with self.subTest(column=column):
                self.assertIn(column, rendered['rendered_fields'])


class ResubmittingUnderAnotherKeyTest(AdminKeyWriteTestCase):
    def test_a_question_resubmitted_under_a_neighbours_key_leaves_that_row_alone(self):
        # The measured defect: `q-003` resubmitted as `q-004` overwrote
        # `q-004`'s recorded market question, row count unchanged.
        before = _row('questions', Q_NEIGHBOUR)

        result = self.submit(
            'admin:social_questions_change',
            'questions_form',
            {'id': Q_NEIGHBOUR},
            args=[Q_EDITED],
        )

        self.assertSaved(result, "resubmitting a question with the neighbour's id")
        after = _row('questions', Q_NEIGHBOUR)
        # The neighbour, not the whole table: the row actually being saved
        # legitimately changes, because this save path writes '' over a
        # previously-NULL nullable TextField on origin/main too.
        for column in ('date', 'network', 'question', 'topic'):
            with self.subTest(column=column):
                self.assertEqual(
                    after[column],
                    before[column],
                    f'{Q_NEIGHBOUR}.{column} belongs to {Q_NEIGHBOUR}, not to the form '
                    f'that was open',
                )
        self.assertEqual(
            _row('questions', Q_EDITED)['question'],
            Q_EDITED_TEXT,
            'the edited row keeps its own question as well',
        )
        self.assertEqual(len(_questions()), 2, 'neither arm may change the row count')

    def test_an_interaction_resubmitted_under_another_key_leaves_both_rows_alone(self):
        # The same surface one table over: `interactions` ids are hand-minted
        # sequentials, so the neighbour is one keystroke away here too. Without
        # this example a guard applied to `PeopleAdmin` alone would pass.
        before = _row('interactions', I_NEIGHBOUR)

        result = self.submit(
            'admin:social_interactions_change',
            'interactions_form',
            {'id': I_NEIGHBOUR},
            args=[I_EDITED],
        )

        self.assertSaved(result, "resubmitting an interaction with the neighbour's id")
        after = _row('interactions', I_NEIGHBOUR)
        for column in ('ts', 'network', 'person', 'text'):
            with self.subTest(column=column):
                self.assertEqual(after[column], before[column])
        self.assertEqual(_row('interactions', I_EDITED)['text'], I_EDITED_TEXT)
        self.assertEqual(len(_interactions()), 2)

    def test_correcting_a_person_slug_cannot_fork_the_row(self):
        # `people` ids are LinkedIn slugs, so the realistic arm here is the
        # copy: on origin/main this save wrote a *new* row carrying the
        # correction and left `li:anna` — the id the draft, the question, the
        # interaction and the lead all name — exactly as it was.
        result = self.submit(
            'admin:social_people_change',
            'people_form',
            {'id': ANNA_CORRECTED, 'name': ANNA_NEW_NAME},
            args=[ANNA],
        )

        self.assertSaved(result, 'correcting a person')
        self.assertEqual(
            [row['id'] for row in _people()],
            [ANNA, BORIS],
            'a correction must not add a row under the submitted key',
        )
        self.assertEqual(
            _row('people', ANNA)['name'],
            ANNA_NEW_NAME,
            'the correction must land on the row every reference points at',
        )
        # What a fork would have stranded.
        self.assertEqual(_row('drafts', draft_id)['target_person'], ANNA)
        self.assertEqual(_row('questions', Q_EDITED)['asker'], ANNA)
        self.assertEqual(_row('interactions', I_EDITED)['person'], ANNA)
        self.assertEqual(_row('leads', lead_id)['person'], ANNA)


class AddFormStillTakesTheKeyTest(AdminKeyWriteTestCase):
    def test_adding_a_question_still_takes_a_hand_typed_key(self):
        # Nothing generates this id, so freezing it on the add form too would
        # make the table unwritable from the admin.
        result = self.submit(
            'admin:social_questions_add',
            'questions_form',
            {
                'id': Q_NEW,
                'date': '15.11.2026',
                'network': 'linkedin',
                'question': Q_NEW_TEXT,
            },
        )

        self.assertIn('id', result['rendered_fields'], 'the add form must offer the key')
        self.assertSaved(result, 'adding a question')
        self.assertEqual(_row('questions', Q_NEW)['question'], Q_NEW_TEXT)

    def test_adding_a_question_under_an_existing_key_is_still_refused(self):
        # The add form's own uniqueness check (`_perform_unique_checks` keeps
        # the pk while `_state.adding`) is what the change form never ran, and
        # it must survive this change.
        result = self.submit(
            'admin:social_questions_add',
            'questions_form',
            {
                'id': Q_EDITED,
                'date': '15.11.2026',
                'network': 'linkedin',
                'question': Q_NEW_TEXT,
            },
        )

        self.assertRefused(result, 'adding a question under an existing key')
        self.assertEqual(len(_questions()), 2, 'a refused add must write no row')
        self.assertEqual(
            _row('questions', Q_EDITED)['question'],
            Q_EDITED_TEXT,
            'and must not overwrite the row whose key it reused',
        )


class OrdinaryEditsStillSaveTest(AdminKeyWriteTestCase):
    def test_ordinary_edits_still_save_on_every_writable_admin(self):
        # The regression this change could cause: a `get_readonly_fields` that
        # froze too much, or raised on the two keys that reach no form, would
        # take the whole ops surface down. One column per writable admin, each
        # submitted as the page rendered it.
        edits = (
            # model, admin route, form id, table, row key, column, new value
            ('People', 'admin:social_people_change', 'people_form',
             'people', ANNA, 'headline', 'операционный директор, Yatfa'),
            ('Interactions', 'admin:social_interactions_change', 'interactions_form',
             'interactions', I_EDITED, 'note', 'ответили в тот же день'),
            ('Questions', 'admin:social_questions_change', 'questions_form',
             'questions', Q_EDITED, 'yatfa_answer', 'показываем очередь согласований'),
            # The two generated keys are only known once the fixture exists.
            ('Ledger', 'admin:social_ledger_change', 'ledger_form',
             'ledger', ledger_id, 'outcome', 'опубликовано, 12 реакций'),
            ('Leads', 'admin:social_leads_change', 'leads_form',
             'leads', lead_id, 'next_step', 'созвон в четверг'),
        )
        for model, route, form_id, table, key, column, value in edits:
            with self.subTest(model=model):
                result = self.submit(route, form_id, {column: value}, args=[key])

                self.assertSaved(result, f'editing {model}')
                self.assertEqual(
                    _row(table, key)[column],
                    value,
                    f'{model}: the edit must land on the row',
                )


if __name__ == '__main__':
    unittest.main()

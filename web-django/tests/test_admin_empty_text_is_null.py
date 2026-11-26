"""An empty admin textarea over a nullable `text` column stores NULL, not `''`.

A change form submits *every* field, so an operator correcting one column also
rewrites the row's unset ones — and on origin/main each of those rewrites
turned `NULL` into `''`. Measured on this project's own backup rows:
submitting a change form exactly as it rendered, with nothing edited, changed
two columns on a person (`followers`, `relevance`), one on a question
(`yatfa_answer`), two on an interaction (`parent_url`, `note`) and one on a
lead (`pain`). The add form did it too — a new person was born with all five
optional columns `''`.

`schema.sql` gives a nullable `text` column exactly one "no value" state,
`NULL`, and the rest of the project agrees (`makeFlag` in `lib/cli.mjs`
defaults an absent flag to `null`, `bin/import.mjs` binds `?? null` on every
optional column, `queue.skip` writes `skip_reason=reason or None`, and the
backup carries zero empty strings). The admin was the only writer producing
one.

What makes the write worth a guard is that it is invisible on the page that
made it — `admin.utils.display_for_field` renders everything in
`field.empty_values` as the empty marker, so a changelist cell cannot tell
`NULL` from `''` — while the machinery the operator navigates by can:

  * `AllValuesFieldListFilter` offers "no value" as `?segment__isnull=True`,
    and `segment` is in `PeopleAdmin.list_filter`. An unedited save moves the
    saved person out of that worklist and behind a second choice, `?segment=`,
    whose rendered label is the empty string — an invisible link in the
    «сегмент» sidebar.
  * Postgres sorts NULLs last and `''` first, so the two halves land at
    opposite ends of an ascending sort.

The cause is a framework asymmetry: `CharField.formfield` passes
`empty_value=None` for a nullable column and `TextField.formfield` does not
(Django 5.2.19). `inspectdb` emits `TextField` for every `text` column, which
is why this project meets the gap on 22 columns at once.
`social.admin.EmptyTextIsNullAdmin` passes for `TextField` what Django already
passes for `CharField`.

Two halves, the convention of the sibling modules:

  1. the census, no database — every field of every registered admin's add and
     change form read out of `admin.site._registry` in a probe subprocess
     (`django.setup()` needs no Postgres) and asserted by **what it cleans an
     empty submission to**, never by the `empty_value` attribute it carries.
     Reading the attribute reports `Interactions.stats` as affected, because
     `forms.JSONField` inherits `empty_value = ''` while its `to_python`
     returns None regardless.
  2. rendered writes, against a scratch database built from `schema.sql` by
     `tests/_db.py` — the unedited save, the clearing arm, the whitespace-only
     arm, an ordinary edit, a required column left empty, the add form, and
     the «сегмент» worklist the defect was measured on.

The write examples **submit what the page rendered** (`tests/_formfields.py`).
A hand-built payload would be testing the test author's typing: the claim is
about the value the admin's own form sends for a field nobody touched.

Three controls, because this surface has already produced three over-matches
of the same class (`pk.editable` in the frozen-key slice, `forms.JSONField`
here, and `auth.User` below):

  * already-NULL — `Interactions.stats`, the nullable foreign keys and
    `People.first_seen` cleaned an empty submission to None before this change
    and must still;
  * stays-required — `People.name`, `People.network`, `People.status`,
    `Interactions.network`, `Questions.question` and `Ledger.topic`;
  * out of scope — `DraftsAdmin` is view-only and must not inherit the mixin,
    and `auth.User`'s `email`/`first_name`/`last_name` still clean to `''`:
    they are `null=False`, so `''` is the only legitimate "no value" state
    there and writing NULL would *be* the defect. Asked across every writable
    admin's change form, "which field cleans an empty submission to `''`?"
    answered 25 before this change — the 22 plus those three — and widening
    the mixin to close that gap would invert the fix.

With no reachable Postgres the second half skips honestly and the suite stays
green, exactly as in `tests/test_queue_transitions.py`.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_empty_text_test_{os.getpid()}'

OPERATOR = 'empty-text-tests-operator'

# --- the census this change is about --------------------------------------

# The 22 nullable `text` columns reachable from a writable admin form, by
# table. Written out rather than derived, so the census is a claim and not a
# restatement of the code it checks; `test_the_census_is_still_the_whole_set`
# reconciles it against `social/models.py` so a new nullable column cannot go
# unguarded without failing here first.
NULLABLE_TEXT_COLUMNS = {
    'People': ('profile', 'headline', 'followers', 'segment', 'relevance'),
    'Interactions': ('person', 'post_url', 'parent_url', 'text', 'draft_ref', 'note'),
    'Questions': ('asker_role', 'context', 'url', 'topic', 'yatfa_answer', 'status'),
    'Ledger': ('url', 'outcome'),
    'Leads': ('source_url', 'pain', 'next_step'),
}

CENSUS_TOTAL = 22

# Control: these cleaned an empty submission to None before this change, and a
# guard aimed at `TextField` must leave them exactly as they were.
# `Interactions.stats` is the `forms.JSONField` trap — it *carries*
# `empty_value = ''` and still cleans to None.
ALREADY_NULL = {
    'People': ('first_seen',),
    'Interactions': ('stats', 'draft', 'in_reply_to'),
    'Questions': ('asker', 'source'),
}

# Control: a non-null column's only "no value" state is `''`, and every one of
# this project's is required, so an empty submission is a form error. Nothing
# here may become nullable by accident.
STAYS_REQUIRED = {
    'People': ('id', 'name', 'network', 'status'),
    'Interactions': ('id', 'ts', 'network', 'direction', 'kind'),
    'Questions': ('id', 'date', 'network', 'question'),
    'Ledger': ('at', 'network', 'topic'),
    'Leads': ('person', 'stage'),
}

# Control: view-only, so it builds no form and its six nullable columns are
# unreachable from the admin. The approval queue owns every write to `drafts`.
VIEW_ONLY = 'Drafts'

# Control: the registry-wide over-match. `CharField`/`EmailField` and
# `null=False`, so `''` is correct there.
AUTH_USER_EMPTY_STRING_FIELDS = ('email', 'first_name', 'last_name')

# --- where each admin's form lives -----------------------------------------

# model, admin route stem, form id, table
WRITABLE_ADMINS = (
    ('People', 'people', 'people_form', 'people'),
    ('Interactions', 'interactions', 'interactions_form', 'interactions'),
    ('Questions', 'questions', 'questions_form', 'questions'),
    ('Ledger', 'ledger', 'ledger_form', 'ledger'),
    ('Leads', 'leads', 'leads_form', 'leads'),
)

# `auto_now`, so an unedited save legitimately moves it (`bin/lead.mjs`
# maintains the same column on both of its write paths).
NOT_PRESERVED_BY_A_SAVE = {'leads': ('updated_at',)}

# --- fixture ---------------------------------------------------------------

ANNA = 'li:anna'
BORIS = 'li:boris'
VERA = 'li:vera'

ANNA_NAME = 'Анна Петрова'
ANNA_PROFILE = 'https://www.linkedin.com/in/anna-petrova'
ANNA_HEADLINE = 'операционный директор'
ANNA_FIRST_SEEN = '2026-10-01'

# `segment` is the column the defect was measured on: two of the three people
# have none, which is the «сегмент: Пусто» worklist.
BORIS_SEGMENT = 'ICP'
WITHOUT_SEGMENT = (ANNA, VERA)

I_ROW = 'li-20261023-01'
I_TEXT = 'спасибо, разберём на следующей неделе'
I_POST_URL = 'https://www.linkedin.com/feed/update/urn:li:activity:1/'
I_STATS = {'likes': 3}

Q_ROW = 'q-003'
Q_TEXT = 'как у вас устроено согласование постов?'
Q_CONTEXT = 'спросили в комментариях под постом про очередь'
Q_URL = 'https://www.linkedin.com/feed/update/urn:li:activity:2/'
Q_TOPIC = 'процесс'

LEDGER_TOPIC = 'очередь согласований'
LEDGER_URL = 'https://www.linkedin.com/feed/update/urn:li:activity:3/'

LEAD_SOURCE_URL = 'https://www.linkedin.com/feed/update/urn:li:activity:4/'
LEAD_PAIN = 'каждое согласование — это переписка на три дня'

# What an add form must be given before it will save at all. Every other field
# comes off the page as it rendered, which is the point of the example.
ADD_REQUIRED = {
    'People': {'id': 'li:new-person', 'name': 'Новый человек', 'network': 'linkedin'},
    'Interactions': {
        'id': 'li-20261126-09',
        'ts': '2026-11-26 10:00',
        'network': 'linkedin',
        'direction': 'in',
        'kind': 'comment',
    },
    'Questions': {
        'id': 'q-900',
        'date': '26.11.2026',
        'network': 'linkedin',
        'question': 'кто отвечает за тон голоса в ответах?',
    },
    'Ledger': {'at': '2026-11-26 09:00', 'network': 'linkedin', 'topic': 'новая запись'},
    # `leads_person_uniq` is one lead per person, so the new lead names the
    # person who has none.
    'Leads': {'person': VERA},
}

# The key the add example then reads the row back by.
ADDED_ROW_LOOKUP = {
    'People': ('people', 'id', 'li:new-person'),
    'Interactions': ('interactions', 'id', 'li-20261126-09'),
    'Questions': ('questions', 'id', 'q-900'),
    'Ledger': ('ledger', 'topic', 'новая запись'),
    'Leads': ('leads', 'person', VERA),
}

# --- probes ----------------------------------------------------------------

# A request complete enough to assemble an admin form with no database: both
# `has_perm` and `has_perms` are consulted while Django decides whether a
# related field gets its "add another" control, and `request=None` raises
# instead (`formfield_for_dbfield` reads `request.user`).
READ_CENSUS = """
import json
import django
django.setup()
from django import forms
from django.contrib import admin
from django.db import models


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


def verdict(form_field):
    \"\"\"What this field does with an empty submission.

    The prescribed census: what the field *cleans*, never the `empty_value`
    attribute it carries. `forms.JSONField` subclasses `forms.CharField` and
    inherits `empty_value = ''` while its `to_python` returns None regardless,
    so the attribute reports `Interactions.stats` as affected and the
    behaviour does not.
    \"\"\"
    try:
        cleaned = form_field.clean('')
    except forms.ValidationError:
        return 'required'
    if cleaned is None:
        return 'null'
    if cleaned == '':
        return 'empty-string'
    return 'other: %r' % (cleaned,)


def read(model, model_admin):
    # An unsaved instance is enough to select the change form: `get_form`
    # branches on `obj is None`, and nothing here touches a row.
    unsaved = model()
    add = model_admin.get_form(request)()
    change = model_admin.get_form(request, unsaved)()
    return {
        'mixin_applied': 'EmptyTextIsNullAdmin' in [
            cls.__name__ for cls in type(model_admin).__mro__
        ],
        # How `drafts` is detectable as view-only without a database.
        'view_only': (
            type(model_admin).has_change_permission
            is not admin.ModelAdmin.has_change_permission
        ),
        'add': {name: verdict(field) for name, field in add.fields.items()},
        'change': {name: verdict(field) for name, field in change.fields.items()},
        # The reconciliation half: what `social/models.py` actually declares.
        'nullable_text_columns': sorted(
            field.name
            for field in model._meta.get_fields()
            if isinstance(field, models.TextField) and field.null
        ),
        'column_types': {
            field.name: [type(field).__name__, bool(field.null)]
            for field in model._meta.get_fields()
            if isinstance(field, models.Field)
        },
    }


admins = {
    model.__name__: read(model, model_admin)
    for model, model_admin in admin.site._registry.items()
}

# The registry-wide tally, scoped the way the over-match below needs it: every
# WRITABLE admin's change form, skipping the fields a browser cannot submit
# (`auth.User.password` is a disabled `ReadOnlyPasswordHashField`).
empty_string_cleaners = sorted(
    '%s.%s.%s' % (model._meta.app_label, model.__name__, name)
    for model, model_admin in admin.site._registry.items()
    if not admins[model.__name__]['view_only']
    for name, field in model_admin.get_form(request, model())().fields.items()
    if not field.disabled and verdict(field) == 'empty-string'
)

print(json.dumps(
    {'admins': admins, 'empty_string_cleaners': empty_string_cleaners},
    ensure_ascii=False,
))
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

from tests._formfields import form_payload

ERRORLIST = re.compile(r'<ul class="errorlist[^"]*"[^>]*>(.*?)</ul>', re.S)
ITEM = re.compile(r'<li[^>]*>(.*?)</li>', re.S)
TAG = re.compile(r'<[^>]+>')
# The raw primary key of each row the changelist is showing: the action
# checkbox carries it unquoted, while the row's own link carries the admin's
# quoted form (`li_3Aanna`).
SELECTED_ACTION = re.compile(r'name="_selected_action" value="([^"]*)"')
# One filter's block of the sidebar, selected by the header the operator sees.
FILTER_BLOCK = re.compile(r'<details data-filter-title="сегмент".*?</details>', re.S)
CHOICE = re.compile(r'<a href="([^"]*)"[^>]*>(.*?)</a>', re.S)


def operator_client():
    client = Client()
    client.force_login(User.objects.get(username='empty-text-tests-operator'))
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
    username='empty-text-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

# Render a form, put `PROBE_OVERRIDES` back into exactly what it rendered, and
# submit that. Everything the operator did not touch therefore carries the
# value the admin's own widget produced, which is the whole claim here.
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
    'rendered_values': rendered,
    'status': response.status_code,
    'errors': (
        errors_of(response.content.decode('utf-8')) if response.status_code == 200 else []
    ),
}, ensure_ascii=False))
"""

# The «сегмент» worklist: who the "no value" filter choice is showing, and
# which choices the sidebar offers at all.
READ_SEGMENT_WORKLIST = HTTP_PRELUDE + """
client = operator_client()
url = reverse('admin:social_people_changelist')
worklist = client.get(url + '?segment__isnull=True')
plain = client.get(url)

block = FILTER_BLOCK.search(plain.content.decode('utf-8'))
choices = [
    {'href': href, 'label': ' '.join(TAG.sub(' ', label).split())}
    for href, label in CHOICE.findall(block.group(0) if block else '')
]
print(json.dumps({
    'worklist_status': worklist.status_code,
    'worklist_ids': SELECTED_ACTION.findall(worklist.content.decode('utf-8')),
    'segment_filter_found': block is not None,
    'segment_choices': choices,
}, ensure_ascii=False))
"""

# --- scratch database lifecycle -------------------------------------------

database = None
ledger_id = None
lead_id = None
draft_id = None


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
        # reason, and every example here is about the difference between NULL
        # and `''`.
        tables = database.tables()
        for table in ('people', 'interactions', 'questions', 'leads', 'ledger', 'drafts'):
            assert table in tables, f'schema.sql did not load {table}'
        seeded = _row('people', ANNA)
        assert seeded['profile'] == ANNA_PROFILE, 'the fixture must carry set columns'
        assert seeded['segment'] is None, 'and unset ones, as NULL rather than as ""'
        assert [
            row['id'] for row in database.sql('select id from people where segment is null '
                                              'order by id')
        ] == sorted(WITHOUT_SEGMENT)
    except Exception:
        database.drop()
        database = None
        raise


def tearDownModule():
    if database is not None:
        database.drop()


def _reseed():
    """The fixture as every example expects to find it.

    Every row carries a *mix*: some nullable columns set, some NULL. That mix
    is what the defect needs — an unedited save is only destructive where a
    column has no value, and the clearing arm is only meaningful where one
    does.
    """
    global ledger_id, lead_id, draft_id
    # Referencing tables first; none of the six `REFERENCES` in `schema.sql`
    # carries an `ON DELETE`, so the order is the database's requirement.
    for table in ('questions', 'leads', 'interactions', 'drafts', 'ledger', 'people'):
        database.sql(f'delete from {table}')

    database.sql(
        """insert into people (id, name, network, profile, headline, first_seen)
           values (%s, %s, 'linkedin', %s, %s, %s)""",
        (ANNA, ANNA_NAME, ANNA_PROFILE, ANNA_HEADLINE, ANNA_FIRST_SEEN),
    )
    database.sql(
        """insert into people (id, name, network, segment)
           values (%s, 'Борис Смирнов', 'linkedin', %s)""",
        (BORIS, BORIS_SEGMENT),
    )
    database.sql(
        "insert into people (id, name, network) values (%s, 'Вера Ильина', 'linkedin')",
        (VERA,),
    )

    draft_id = database.sql(
        """insert into drafts (network, kind, status, target_person, body)
           values ('linkedin', 'comment', 'waiting', %s, %s) returning id""",
        (ANNA, 'ответ про очередь согласований'),
    )[0]['id']
    database.sql(
        """insert into interactions (id, ts, network, direction, kind, person, post_url,
                                     text, draft_id, stats)
           values (%s, '2026-10-23 10:00', 'linkedin', 'in', 'comment', %s, %s, %s, %s, %s)""",
        (I_ROW, ANNA, I_POST_URL, I_TEXT, draft_id, json.dumps(I_STATS)),
    )
    database.sql(
        """insert into questions (id, date, network, asker, question, context, url, topic)
           values (%s, '2026-11-01', 'linkedin', %s, %s, %s, %s, %s)""",
        (Q_ROW, ANNA, Q_TEXT, Q_CONTEXT, Q_URL, Q_TOPIC),
    )
    ledger_id = database.sql(
        """insert into ledger (at, network, topic, url)
           values ('2026-11-02 09:00', 'linkedin', %s, %s) returning id""",
        (LEDGER_TOPIC, LEDGER_URL),
    )[0]['id']
    lead_id = database.sql(
        """insert into leads (person, stage, source_url, pain)
           values (%s, 'candidate', %s, %s) returning id""",
        (ANNA, LEAD_SOURCE_URL, LEAD_PAIN),
    )[0]['id']


def _row(table, row_id, column='id'):
    rows = database.sql(f'select * from {table} where {column} = %s', (row_id,))
    assert len(rows) == 1, f'{table} {column}={row_id!r} must exist'
    return rows[0]


def _seeded_key(model):
    """The key of the fixture row each example edits, per admin."""
    return {
        'People': ANNA,
        'Interactions': I_ROW,
        'Questions': Q_ROW,
        'Ledger': ledger_id,
        'Leads': lead_id,
    }[model]


# --- the census half -------------------------------------------------------


class EmptySubmissionCensusTest(unittest.TestCase):
    """No database: what each admin field does with an empty submission."""

    def setUp(self):
        result = run_probe(
            READ_CENSUS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        census = json.loads(result.stdout)
        self.admins = census['admins']
        self.empty_string_cleaners = census['empty_string_cleaners']

    def test_every_nullable_text_column_cleans_an_empty_submission_to_null(self):
        # The census itself, on both forms: the change form is where an
        # unedited save rewrites a row's unset columns, and the add form is
        # where a new row is born with them.
        for model, columns in NULLABLE_TEXT_COLUMNS.items():
            declared = self.admins[model]
            self.assertTrue(
                declared['mixin_applied'],
                f'{model}Admin must inherit the mixin, or its columns go unguarded',
            )
            for form in ('add', 'change'):
                for column in columns:
                    with self.subTest(model=model, form=form, column=column):
                        self.assertEqual(
                            declared[form][column],
                            'null',
                            f'{model}.{column}: an empty {form} form submission must '
                            f'store nothing, not the empty string',
                        )

    def test_the_census_is_still_the_whole_set(self):
        # The reconciliation: the 22 columns above are what `social/models.py`
        # declares as nullable `TextField` on the five writable admins. A new
        # one must be added here rather than silently going unguarded.
        for model, columns in NULLABLE_TEXT_COLUMNS.items():
            with self.subTest(model=model):
                self.assertEqual(
                    self.admins[model]['nullable_text_columns'],
                    sorted(columns),
                    f'{model}: the census and the model declarations disagree',
                )
        self.assertEqual(
            sum(len(columns) for columns in NULLABLE_TEXT_COLUMNS.values()),
            CENSUS_TOTAL,
            'the census is 22 columns: People 5, Interactions 6, Questions 6, '
            'Ledger 2, Leads 3',
        )

    def test_the_columns_that_already_stored_null_are_untouched(self):
        # Control. `Interactions.stats` is the `forms.JSONField` trap: it
        # carries `empty_value = ''` and cleans to None, so a census that read
        # the attribute would report it as affected and a guard built from
        # that census would "fix" a field that was never broken.
        for model, columns in ALREADY_NULL.items():
            for column in columns:
                with self.subTest(model=model, column=column):
                    self.assertEqual(
                        self.admins[model]['change'][column],
                        'null',
                        f'{model}.{column} cleaned to None before this change',
                    )
        self.assertEqual(
            self.admins['Interactions']['column_types']['stats'],
            ['JSONField', True],
            'premise: `stats` is a JSONField, which is why the attribute lies about it',
        )

    def test_the_required_columns_stay_required(self):
        # Control. A non-null column's only "no value" state is `''`, so
        # nothing here may start accepting an empty submission.
        for model, columns in STAYS_REQUIRED.items():
            for column in columns:
                with self.subTest(model=model, column=column):
                    self.assertEqual(
                        self.admins[model]['add'][column],
                        'required',
                        f'{model}.{column} is NOT NULL: an empty submission is an error',
                    )
                    self.assertFalse(
                        self.admins[model]['column_types'][column][1],
                        f'premise: {model}.{column} must be declared NOT NULL',
                    )

    def test_the_view_only_drafts_admin_is_left_out(self):
        # `DraftsAdmin` builds no form at all — the approval queue owns every
        # write to `drafts` — so its six nullable columns are unreachable from
        # the admin and the mixin must not reach them either.
        drafts = self.admins[VIEW_ONLY]
        self.assertTrue(drafts['view_only'], 'premise: DraftsAdmin is view-only')
        self.assertFalse(
            drafts['mixin_applied'],
            'DraftsAdmin must not inherit the mixin: it is not a write surface',
        )
        self.assertEqual(
            len(drafts['nullable_text_columns']),
            6,
            'premise: the six columns the census subtracts',
        )
        for column in drafts['nullable_text_columns']:
            with self.subTest(column=column):
                self.assertEqual(
                    drafts['change'][column],
                    'empty-string',
                    f'drafts.{column}: unchanged, because nothing here submits a form',
                )

    def test_the_auth_user_admin_keeps_its_empty_strings(self):
        # The over-match this surface invites. Asked across every *writable*
        # admin's change form — skipping the fields a browser cannot submit,
        # which is `auth.User.password`, a disabled
        # `ReadOnlyPasswordHashField` — "which field cleans an empty
        # submission to `''`?" answered 25 before this change: the 22 plus
        # these three. They are `CharField`/`EmailField` and `null=False`, so
        # `''` is the right stored value there and writing NULL would be the
        # defect — widening the mixin to cover them would invert this fix.
        user = self.admins['User']
        self.assertFalse(
            user['mixin_applied'], 'UserAdmin must not inherit the mixin'
        )
        for column in AUTH_USER_EMPTY_STRING_FIELDS:
            with self.subTest(column=column):
                self.assertEqual(user['change'][column], 'empty-string')
                self.assertFalse(
                    user['column_types'][column][1],
                    f'premise: auth User.{column} is NOT NULL',
                )

    def test_those_three_are_all_that_is_left_cleaning_to_the_empty_string(self):
        # The other side of the same census, and the assertion that keeps the
        # over-match from being "fixed": after this change the only writable
        # admin fields still cleaning an empty submission to `''` are those
        # three `auth.User` columns — 25 before, three after, and the 22 in
        # between are exactly this ticket's census.
        self.assertEqual(
            self.empty_string_cleaners,
            sorted(f'auth.User.{column}' for column in AUTH_USER_EMPTY_STRING_FIELDS),
            'a social column still storing `""` is the defect; a NULL in one of these '
            'three would be the inverse of it',
        )


# --- the rendered half -----------------------------------------------------


class EmptyTextWriteTestCase(unittest.TestCase):
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

    def change(self, stem, form_id, model, overrides):
        return self.submit(
            f'admin:social_{stem}_change', form_id, overrides, args=[_seeded_key(model)]
        )

    def add(self, stem, form_id, overrides):
        return self.submit(f'admin:social_{stem}_add', form_id, overrides)

    def assertSaved(self, result, what):
        self.assertEqual(
            result['status'],
            302,
            f'{what} must save and redirect; the page said {result["errors"]}',
        )

    def assertStoredNull(self, table, key, columns, what, column='id'):
        row = _row(table, key, column=column)
        for name in columns:
            with self.subTest(column=name):
                self.assertIsNone(
                    row[name],
                    f'{table}.{name}: {what} must store nothing — found {row[name]!r}, '
                    f'which hides the row from the "no value" filter choice',
                )


class AnUneditedSaveLeavesTheRowAloneTest(EmptyTextWriteTestCase):
    def test_submitting_a_change_form_with_nothing_edited_changes_no_column(self):
        # The measured defect, on every writable admin: the operator opens a
        # row, presses save, and the row's *unset* columns are rewritten from
        # `NULL` to `''` — two on a person, one on a question, two on an
        # interaction, one on a lead.
        for model, stem, form_id, table in WRITABLE_ADMINS:
            with self.subTest(model=model):
                key = _seeded_key(model)
                before = _row(table, key)

                result = self.change(stem, form_id, model, {})

                self.assertSaved(result, f'an unedited save of {model}')
                after = _row(table, key)
                moved = NOT_PRESERVED_BY_A_SAVE.get(table, ())
                for column in before:
                    if column in moved:
                        continue
                    with self.subTest(column=column):
                        self.assertEqual(
                            after[column],
                            before[column],
                            f'{table}.{column}: an unedited save must leave the row '
                            f'byte-identical',
                        )

    def test_a_stored_value_survives_an_unedited_save(self):
        # The other half of the same claim: the guard must not turn a real
        # value into NULL on the way back through the form.
        result = self.change('people', 'people_form', 'People', {})

        self.assertSaved(result, 'an unedited save of a person')
        row = _row('people', ANNA)
        self.assertEqual(row['profile'], ANNA_PROFILE)
        self.assertEqual(row['headline'], ANNA_HEADLINE)
        self.assertEqual(row['name'], ANNA_NAME)
        self.assertEqual(str(row['first_seen']), ANNA_FIRST_SEEN)


class ClearingAColumnStoresNothingTest(EmptyTextWriteTestCase):
    def test_clearing_every_nullable_column_stores_null(self):
        # The operator's own gesture: empty the textarea and save. All 22
        # columns, five submissions — each one clearing its table's whole set
        # at once, including the ones that were already NULL.
        for model, stem, form_id, table in WRITABLE_ADMINS:
            with self.subTest(model=model):
                columns = NULLABLE_TEXT_COLUMNS[model]
                result = self.change(
                    stem, form_id, model, {column: '' for column in columns}
                )

                self.assertSaved(result, f'clearing {model}')
                self.assertStoredNull(
                    table, _seeded_key(model), columns, 'a cleared column'
                )

    def test_a_whitespace_only_submission_stores_null_too(self):
        # `forms.CharField` strips before testing emptiness, so a textarea
        # holding spaces and a newline is the same gesture — the answer
        # `links.web_href` already gives a whitespace-only URL.
        for model, stem, form_id, table in WRITABLE_ADMINS:
            with self.subTest(model=model):
                columns = NULLABLE_TEXT_COLUMNS[model]
                result = self.change(
                    stem, form_id, model, {column: '  \n ' for column in columns}
                )

                self.assertSaved(result, f'blanking {model} with whitespace')
                self.assertStoredNull(
                    table, _seeded_key(model), columns, 'a whitespace-only column'
                )


class AddedRowsAreBornWithoutEmptyStringsTest(EmptyTextWriteTestCase):
    def test_a_new_row_stores_null_in_every_column_the_operator_left_empty(self):
        # The add form submits every field too, so on origin/main a new person
        # was born with all five optional columns `''` — outside the «Пусто»
        # worklist from the moment it was created.
        for model, stem, form_id, _table in WRITABLE_ADMINS:
            with self.subTest(model=model):
                result = self.add(stem, form_id, ADD_REQUIRED[model])

                self.assertSaved(result, f'adding {model}')
                table, column, key = ADDED_ROW_LOOKUP[model]
                self.assertStoredNull(
                    table,
                    key,
                    NULLABLE_TEXT_COLUMNS[model],
                    'a column the add form never filled',
                    column=column,
                )


class OrdinaryWritesStillBehaveTest(EmptyTextWriteTestCase):
    def test_an_ordinary_edit_still_saves(self):
        # The regression this change could cause: a `formfield_for_dbfield`
        # that mishandled its kwargs would take the whole ops surface down.
        edits = (
            ('People', 'people', 'people_form', 'people', 'segment', 'ICP'),
            ('Interactions', 'interactions', 'interactions_form', 'interactions',
             'note', 'ответили в тот же день'),
            ('Questions', 'questions', 'questions_form', 'questions',
             'yatfa_answer', 'показываем очередь согласований'),
            ('Ledger', 'ledger', 'ledger_form', 'ledger', 'outcome', 'опубликовано'),
            ('Leads', 'leads', 'leads_form', 'leads', 'next_step', 'созвон в четверг'),
        )
        for model, stem, form_id, table, column, value in edits:
            with self.subTest(model=model):
                result = self.change(stem, form_id, model, {column: value})

                self.assertSaved(result, f'editing {model}')
                self.assertEqual(
                    _row(table, _seeded_key(model))[column],
                    value,
                    f'{model}.{column}: the edit must land on the row',
                )

    def test_a_required_column_left_empty_is_still_refused(self):
        # `People.name` is NOT NULL in `schema.sql`. The guard must not turn
        # its empty submission into a NULL the database would reject with a
        # 500 — it stays a form error, and the row stays as it was.
        before = _row('people', ANNA)

        result = self.change('people', 'people_form', 'People', {'name': ''})

        # A refused admin save is a 200 with the form re-rendered, which reads
        # like success to a status check alone.
        self.assertEqual(result['status'], 200, 'an empty name must be refused')
        self.assertTrue(result['errors'], 'the page must say why it was refused')
        self.assertEqual(
            _row('people', ANNA), before, 'a refused save must write nothing'
        )


class TheNoValueWorklistSurvivesASaveTest(EmptyTextWriteTestCase):
    """«сегмент: Пусто» is the operator's worklist of people still to segment."""

    def worklist(self):
        result = self.probe(READ_SEGMENT_WORKLIST)
        self.assertEqual(result['worklist_status'], 200, 'the worklist must render')
        self.assertTrue(
            result['segment_filter_found'],
            'premise: «сегмент» is in PeopleAdmin.list_filter, which is what makes '
            'the worklist reachable',
        )
        return result

    def test_a_saved_person_stays_in_the_no_value_worklist(self):
        before = self.worklist()
        self.assertEqual(
            sorted(before['worklist_ids']),
            sorted(WITHOUT_SEGMENT),
            'premise: two of the three seeded people have no segment',
        )

        self.assertSaved(
            self.change('people', 'people_form', 'People', {}),
            'an unedited save of a person with no segment',
        )

        after = self.worklist()
        self.assertEqual(
            sorted(after['worklist_ids']),
            sorted(WITHOUT_SEGMENT),
            '?segment__isnull=True must still show every person with no segment: on '
            'origin/main an unedited save dropped the saved one out of it (6 → 5 on '
            'the real data)',
        )

    def test_the_segment_filter_offers_no_blank_labelled_choice(self):
        self.assertSaved(
            self.change('people', 'people_form', 'People', {}), 'an unedited save'
        )

        choices = self.worklist()['segment_choices']
        self.assertTrue(
            any('segment__isnull=True' in choice['href'] for choice in choices),
            f'the "no value" choice must be offered; got {choices}',
        )
        for choice in choices:
            with self.subTest(choice=choice['href']):
                self.assertTrue(
                    choice['label'],
                    'a choice with an empty label is an invisible link in the sidebar: '
                    f'{choice}',
                )
                self.assertFalse(
                    choice['href'].endswith('segment=')
                    or 'segment=&' in choice['href'],
                    f'an empty-string segment must not become its own choice: {choice}',
                )


if __name__ == '__main__':
    unittest.main()

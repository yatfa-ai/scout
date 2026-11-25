"""The READ surface: every pane of the Node screen is readable in a changelist.

`web/index.html` is the parity reference (knowledge scout-2), and four of its
six panes show content that lives in no `list_display` by default: the reply
text and the person's name for «Ответы нам», the question and our answer for
«Вопросы рынка», the headline and the message count for «Люди», the headline
and the pain for «Лиды». Without those columns an operator who drops the Node
reader opens one detail page per row to read a single reply, so the columns are
the deliverable and these examples are what pins them.

Two halves, for two kinds of claim:

  1. metadata, no database — the six `list_display` sets (the four this slice
     changes, plus `Ledger` and `Drafts`, which must not change), `direction`
     first in the interactions filter, and a Russian header plus an ordering
     field on every new column. Read out of `admin.site._registry` in a probe
     subprocess, the precedent of `tests/test_drafts_admin_view_only.py`;
     `django.setup()` needs no Postgres.
  2. rendered pages, against a scratch database built from `schema.sql` by
     `tests/_db.py` — because "the reply is readable" is a claim about a cell
     of a rendered changelist, not about a configuration tuple. With no
     reachable Postgres these skip honestly and the suite stays green.

⚠️ Every claim about CONTENT is asserted on extracted CELLS, never on the page
text. A changelist page contains a row's `__str__` in the checkbox's
`aria-label`, so grepping the HTML for a question's text returns true while the
text sits in no visible column — the exact false pass this module exists to
rule out. Those examples therefore go through `rows_of()`, and the row width is
checked against `list_display` so a broken extractor cannot pass either.
"""

import json
import os
import unittest

from . import _db
from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

SCRATCH_DB = f'scout_django_admin_read_test_{os.getpid()}'

OPERATOR = 'admin-read-tests-operator'

# --- the columns under test ------------------------------------------------

# The listing each changelist must offer. Pinned whole rather than by
# membership: losing a column is as much a regression as never adding one.
LIST_DISPLAY = {
    'Interactions': [
        'id', 'ts', 'network', 'direction', 'kind', 'person_name', 'draft', 'text_preview',
    ],
    'Questions': [
        'id', 'date', 'network', 'question_preview', 'asker', 'to_us', 'topic',
        'answer_preview', 'status',
    ],
    'People': [
        'id', 'name', 'network', 'headline_preview', 'segment', 'status',
        'interaction_count', 'first_seen', 'created_at',
    ],
    'Leads': [
        'id', 'person', 'stage', 'person_headline', 'pain_preview', 'next_step',
        'created_at', 'updated_at',
    ],
    # «Ушло» already matched the ledger pane column for column: unchanged.
    'Ledger': ['id', 'at', 'network', 'topic', 'url', 'outcome'],
    # Slice 1's listing, kept view-only by slice 2: unchanged.
    'Drafts': [
        'id', 'network', 'kind', 'status', 'target_person', 'created_at', 'sent_at',
    ],
}

# The columns this slice adds, per model, with the field each one must sort by.
NEW_COLUMNS = {
    'Interactions': {'person_name': 'person_label', 'text_preview': 'text'},
    'Questions': {'question_preview': 'question', 'answer_preview': 'yatfa_answer'},
    'People': {'headline_preview': 'headline', 'interaction_count': 'interaction_total'},
    'Leads': {'person_headline': 'person__headline', 'pain_preview': 'pain'},
}

MODEL_URLS = {
    'Interactions': 'interactions',
    'Questions': 'questions',
    'People': 'people',
    'Leads': 'leads',
    'Ledger': 'ledger',
    'Drafts': 'drafts',
}

CYRILLIC = set('абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ')

# --- fixture ---------------------------------------------------------------

PEOPLE = [
    # id, name, network, headline
    ('li:anna', 'Анна Петрова', 'linkedin', 'CTO в среднем продукте — живёт в согласованиях'),
    ('li:boris', 'Борис Смирнов', 'linkedin', 'инженер данных, пишет про пайплайны'),
    # No headline and no interaction: the "nothing stored" and the zero.
    ('x:vera', 'Вера Кузнецова', 'x', None),
]
ANNA_HEADLINE = PEOPLE[0][3]

# An id that is in no `people` row. Real in this data: `interactions.person` is
# free-form text with no foreign key, so the name must fall back to the id.
GHOST = 'rd:ghost'

REPLY_TEXT = 'Да, именно это и болит: согласование занимает дни.'
LONG_REPLY = (
    'Длинный ответ, который заведомо не влезает в одну ячейку списка, потому что '
    'в нём больше девяноста символов подряд и это ровно тот случай, который надо обрезать.'
)
OUT_TEXT = 'наш комментарий под их постом'
BORIS_TEXT = 'коротко: интересно, расскажи подробнее'

INTERACTIONS = [
    # id, direction, kind, person, text
    ('i-anna-in', 'in', 'reply', 'li:anna', REPLY_TEXT),
    ('i-ghost-in', 'in', 'comment', GHOST, 'пишет кто-то, кого мы ещё не записали'),
    ('i-anon-in', 'in', 'reply', None, LONG_REPLY),
    ('i-anna-out', 'out', 'comment', 'li:anna', OUT_TEXT),
    ('i-boris-in', 'in', 'reply', 'li:boris', BORIS_TEXT),
]
# Expected message count per person: `select count(*) ... where person = id`.
EXPECTED_COUNTS = {'li:anna': 2, 'li:boris': 1, 'x:vera': 0}

QUESTION_TEXT = 'Как считать окупаемость агентов, если они пишут код?'
ANSWER_TEXT = 'Есть ledger и очередь утверждений — считаем по отправленному.'
UNANSWERED_QUESTION = 'Кто отвечает, когда агент ошибается?'

QUESTIONS = [
    # id, asker, to_us, question, answer, topic
    ('q-roi', 'li:anna', True, QUESTION_TEXT, ANSWER_TEXT, 'ROI'),
    ('q-blame', 'li:boris', False, UNANSWERED_QUESTION, None, 'ответственность'),
]

PAIN_TEXT = 'каждое согласование — это переписка на три дня'

LEADS = [
    # person, stage, pain, next_step
    ('li:anna', 'talking', PAIN_TEXT, 'прислать разбор очереди'),
    ('x:vera', 'candidate', None, 'прокомментировать её пост'),
]

DRAFT_BODY = 'черновик для проверки, что drafts остались только для чтения'

# «Ушло» renders ledger rows, and `LedgerAdmin` already matched that pane
# column for column before this slice: one row, so the listing has something
# to render and the parity claim is checked rather than assumed.
LEDGER = ('2026-11-19 12:00', 'linkedin', 'очередь утверждений', 'отклик: три комментария')

# How many rows the volume example adds per table: enough to fill the
# changelist page, so a per-row query would be unmissable.
BULK_ROWS = 120
BULK_PREFIX = 'bulk'

# --- probes ----------------------------------------------------------------

READ_ADMIN_LISTING = """
import json
import django
django.setup()
from django.contrib import admin
from social.models import Drafts, Interactions, Leads, Ledger, People, Questions


def read(model):
    model_admin = admin.site._registry[model]
    columns = {}
    for name in model_admin.list_display:
        column = getattr(model_admin, name, None)
        if callable(column):
            columns[name] = {
                'description': getattr(column, 'short_description', None),
                'ordering': getattr(column, 'admin_order_field', None),
            }
    return {
        'list_display': list(model_admin.list_display),
        'list_filter': list(model_admin.list_filter),
        'list_select_related': list(getattr(model_admin, 'list_select_related', None) or []),
        'ordering': list(model_admin.ordering or []),
        'columns': columns,
    }


print(json.dumps({
    m.__name__: read(m)
    for m in (Interactions, Questions, People, Leads, Ledger, Drafts)
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

OPERATOR = 'admin-read-tests-operator'

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
    # aria-label, so the page text is not evidence that a column exists.
    if '<tbody>' not in body:
        return []
    table = body.split('<tbody>')[1].split('</tbody>')[0]
    return [[text_of(cell) for cell in CELL.findall(row)] for row in ROW.findall(table)]


def changelist_url(model):
    return reverse('admin:social_%s_changelist' % model)


def changelist(model, query='', client=None):
    client = client or operator_client()
    response = client.get(changelist_url(model) + query)
    body = response.content.decode('utf-8')
    return {'status': response.status_code, 'rows': rows_of(body), 'html': body}
"""

MIGRATE_AND_CREATE_OPERATOR = """
import django
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth.models import User
User.objects.update_or_create(
    username='admin-read-tests-operator',
    defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
)
print('ok')
"""

RENDER_CHANGELIST = HTTP_PRELUDE + """
print(json.dumps(changelist(os.environ['PROBE_MODEL'], os.environ.get('PROBE_QUERY', ''))))
"""

# One warm-up request, then the measured one: the session and user lookups are
# the same in both volumes, so what is left to vary is the row count.
QUERY_COUNT = HTTP_PRELUDE + """
from django.db import connection
from django.test.utils import CaptureQueriesContext

client = operator_client()
out = {}
for model in json.loads(os.environ['PROBE_MODELS']):
    url = changelist_url(model)
    client.get(url)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
    out[model] = {
        'status': response.status_code,
        'queries': len(captured.captured_queries),
        'rows': len(rows_of(response.content.decode('utf-8'))),
    }
print(json.dumps(out))
"""

# What the operator can actually click: every sortable header Django renders,
# with its label, keyed by the list_display column it belongs to.
SORT_EVERY_COLUMN = HTTP_PRELUDE + """
model = os.environ['PROBE_MODEL']
client = operator_client()
url = changelist_url(model)
head = client.get(url).content.decode('utf-8').split('<thead>')[1].split('</thead>')[0]

out = {}
for header in re.findall(r'<th[^>]*>.*?</th>', head, re.S):
    column = re.search(r'column-(\\w+)', header)
    if column is None:
        continue
    link = re.search(r'href="(\\?o=[^"]*)"', header)
    out[column.group(1)] = {
        'label': text_of(header),
        'sortable': 'sortable' in header.split('>', 1)[0],
        'status': client.get(url + entities.unescape(link.group(1))).status_code if link else None,
    }
print(json.dumps(out))
"""

POST_TO_DRAFTS = HTTP_PRELUDE + """
client = operator_client()
add = client.post(reverse('admin:social_drafts_add'), {})
change = client.post(
    reverse('admin:social_drafts_change', args=[os.environ['PROBE_ID']]), {}
)
listing = client.get(changelist_url('drafts'))
print(json.dumps({
    'add': add.status_code,
    'change': change.status_code,
    'listing': listing.status_code,
}))
"""

# --- scratch database lifecycle -------------------------------------------

database = None
draft_id = None


def setUpModule():
    global database
    database = _db.scratch_database(SCRATCH_DB)
    if database is None:
        return
    try:
        _seed()
        # The admin's login gate needs real users and sessions. `migrate`
        # creates only the `django_*`/`auth_*` tables — every social model is
        # unmanaged — so it cannot touch the schema just loaded.
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
    global draft_id
    for person_id, name, network, headline in PEOPLE:
        database.sql(
            """insert into people (id, name, network, profile, headline, segment, first_seen)
               values (%s, %s, %s, %s, %s, 'founder', '2026-11-01')""",
            (person_id, name, network, f'https://example.test/{person_id}', headline),
        )
    draft_id = database.sql(
        """insert into drafts (network, kind, status, body) values
           ('linkedin', 'comment', 'waiting', %s) returning id""",
        (DRAFT_BODY,),
    )[0]['id']
    for interaction_id, direction, kind, person, text in INTERACTIONS:
        database.sql(
            """insert into interactions (id, ts, network, direction, kind, person, text, draft_id)
               values (%s, '2026-11-20 10:00', 'linkedin', %s, %s, %s, %s, %s)""",
            (interaction_id, direction, kind, person, text,
             draft_id if direction == 'out' else None),
        )
    for question_id, asker, to_us, question, answer, topic in QUESTIONS:
        database.sql(
            """insert into questions (id, date, network, asker, to_us, question, context,
                                      yatfa_answer, topic, status)
               values (%s, '2026-11-20', 'linkedin', %s, %s, %s, 'контекст вопроса', %s, %s,
                       'открыт')""",
            (question_id, asker, to_us, question, answer, topic),
        )
    for person, stage, pain, next_step in LEADS:
        database.sql(
            'insert into leads (person, stage, pain, next_step) values (%s, %s, %s, %s)',
            (person, stage, pain, next_step),
        )
    database.sql(
        """insert into ledger (at, network, topic, url, outcome)
           values (%s, %s, %s, 'https://example.test/post', %s)""",
        (LEDGER[0], LEDGER[1], LEDGER[2], LEDGER[3]),
    )


def _add_bulk_rows(volume):
    """`volume` more people, each with one interaction and one lead."""
    database.sql(
        """insert into people (id, name, network, headline, first_seen)
           select %s || ':' || g, 'Массовый ' || g, 'linkedin', 'заголовок ' || g, '2026-11-01'
           from generate_series(1, %s) g""",
        (BULK_PREFIX, volume),
    )
    database.sql(
        """insert into interactions (id, ts, network, direction, kind, person, text, draft_id)
           select %s || '-i:' || g, '2026-11-21 10:00', 'linkedin', 'in', 'reply',
                  %s || ':' || g, 'массовый текст ' || g, %s
           from generate_series(1, %s) g""",
        (BULK_PREFIX, BULK_PREFIX, draft_id, volume),
    )
    database.sql(
        """insert into leads (person, stage, pain, next_step)
           select %s || ':' || g, 'candidate', 'массовая боль ' || g, 'шаг'
           from generate_series(1, %s) g""",
        (BULK_PREFIX, volume),
    )


def _remove_bulk_rows():
    database.sql('delete from leads where person like %s', (f'{BULK_PREFIX}:%',))
    database.sql('delete from interactions where person like %s', (f'{BULK_PREFIX}:%',))
    database.sql('delete from people where id like %s', (f'{BULK_PREFIX}:%',))


# --- the metadata half -----------------------------------------------------


class AdminListingTest(unittest.TestCase):
    """No database: the configuration the rendered half then exercises."""

    def setUp(self):
        result = run_probe(
            READ_ADMIN_LISTING, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.admins = json.loads(result.stdout)

    def test_every_changelist_offers_exactly_the_pinned_columns(self):
        self.assertEqual(
            {name: self.admins[name]['list_display'] for name in LIST_DISPLAY},
            LIST_DISPLAY,
        )

    def test_the_content_columns_are_new_rather_than_already_there(self):
        # Positive control for the whole module: if `person_name` and friends
        # were ordinary model fields, every example below would pass without
        # this slice existing. They are admin callables, so they cannot.
        for model, columns in NEW_COLUMNS.items():
            for column in columns:
                with self.subTest(model=model, column=column):
                    self.assertIn(column, self.admins[model]['columns'])

    def test_each_new_column_carries_a_russian_header_and_sorts_by_a_field(self):
        for model, columns in NEW_COLUMNS.items():
            for column, ordering in columns.items():
                with self.subTest(model=model, column=column):
                    declared = self.admins[model]['columns'][column]
                    self.assertTrue(
                        CYRILLIC.intersection(declared['description'] or ''),
                        f'{model}.{column} must carry a Russian header, got '
                        f'{declared["description"]!r}',
                    )
                    self.assertEqual(
                        declared['ordering'],
                        ordering,
                        f'{model}.{column} must stay sortable',
                    )

    def test_the_direction_filter_comes_first_on_interactions(self):
        # «Ответы нам» is `direction = 'in'`: one click, not a default view.
        self.assertEqual(
            self.admins['Interactions']['list_filter'],
            ['direction', 'network', 'kind'],
        )

    def test_leads_load_the_person_with_the_lead(self):
        self.assertEqual(self.admins['Leads']['list_select_related'], ['person'])

    def test_interactions_load_the_draft_with_the_interaction(self):
        # The `draft` column renders the related draft: without this it is one
        # query per row that has one.
        self.assertEqual(self.admins['Interactions']['list_select_related'], ['draft'])

    def test_leads_keep_their_own_ordering(self):
        # Replicating the Node stage-priority CASE would override the
        # operator's column clicks; that decision is not ours to make.
        self.assertEqual(self.admins['Leads']['ordering'], ['-updated_at'])


# --- the rendered half -----------------------------------------------------


class ChangelistTestCase(unittest.TestCase):
    """Shared fixture handling: skip without a database."""

    model = None

    def setUp(self):
        if database is None:
            self.skipTest(_db.NO_DATABASE)

    def probe(self, script, **overrides):
        result = run_probe(
            script, DATABASE_URL=database.url, SECRET_KEY=DUMMY_SECRET, **overrides
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def changelist(self, model, query=''):
        page = self.probe(RENDER_CHANGELIST, PROBE_MODEL=MODEL_URLS[model], PROBE_QUERY=query)
        self.assertEqual(page['status'], 200)
        self.assertTrue(page['rows'], f'the {model} changelist rendered no row')
        # The extractor control: one cell per column, plus the action checkbox.
        expected = len(LIST_DISPLAY[model]) + 1
        for row in page['rows']:
            self.assertEqual(
                len(row), expected, f'{model} row has {len(row)} cells, expected {expected}'
            )
        return page

    def cell(self, model, row, column):
        """One row's value for a `list_display` column."""
        return row[LIST_DISPLAY[model].index(column) + 1]

    def row_with(self, page, model, column, value):
        matches = [r for r in page['rows'] if self.cell(model, r, column) == value]
        self.assertEqual(
            len(matches), 1, f'expected exactly one {model} row with {column} == {value!r}'
        )
        return matches[0]


class InboundPaneTest(ChangelistTestCase):
    """«Ответы нам»: the reply text and who wrote it, in the changelist."""

    def setUp(self):
        super().setUp()
        self.page = self.changelist('Interactions')

    def test_the_reply_text_is_a_cell_of_the_interactions_changelist(self):
        row = self.row_with(self.page, 'Interactions', 'id', 'i-anna-in')
        self.assertEqual(
            self.cell('Interactions', row, 'person_name'),
            'Анна Петрова',
            'the reply must say who wrote it, not which id wrote it',
        )
        self.assertEqual(
            self.cell('Interactions', row, 'text_preview'),
            REPLY_TEXT,
            'the reply must be readable without opening the detail page',
        )

    def test_the_person_shown_is_the_resolved_name_rather_than_the_stored_id(self):
        names = {self.cell('Interactions', r, 'person_name') for r in self.page['rows']}
        self.assertIn('Анна Петрова', names)
        self.assertIn('Борис Смирнов', names)
        self.assertNotIn('li:anna', names, 'the raw id must not be shown for a known person')

    def test_an_id_with_no_people_row_falls_back_to_the_stored_id(self):
        # `rd:ghost` is in no `people` row: the pane shows `person_name || person`.
        row = self.row_with(self.page, 'Interactions', 'id', 'i-ghost-in')
        self.assertEqual(self.cell('Interactions', row, 'person_name'), GHOST)

    def test_an_interaction_with_no_person_shows_the_admin_empty_marker(self):
        row = self.row_with(self.page, 'Interactions', 'id', 'i-anon-in')
        self.assertEqual(self.cell('Interactions', row, 'person_name'), '-')

    def test_a_long_reply_is_truncated_rather_than_dropped(self):
        row = self.row_with(self.page, 'Interactions', 'id', 'i-anon-in')
        shown = self.cell('Interactions', row, 'text_preview')
        self.assertTrue(shown.endswith('…'), f'a long reply must be marked as cut: {shown!r}')
        self.assertLessEqual(len(shown), 91)
        self.assertTrue(LONG_REPLY.startswith(shown[:40]))

    def test_the_direction_filter_narrows_the_list_to_the_replies_to_us(self):
        inbound = self.changelist('Interactions', '?direction=in')
        directions = {self.cell('Interactions', r, 'direction') for r in inbound['rows']}
        self.assertEqual(directions, {'нам'}, 'one click must leave only the inbound replies')
        self.assertEqual(len(inbound['rows']), 4)
        texts = {self.cell('Interactions', r, 'text_preview') for r in inbound['rows']}
        self.assertNotIn(OUT_TEXT, texts, 'our own comment is not an answer to us')


class QuestionsPaneTest(ChangelistTestCase):
    """«Вопросы рынка»: the question and what we have to say about it."""

    def setUp(self):
        super().setUp()
        self.page = self.changelist('Questions')

    def test_the_question_and_our_answer_are_cells_rather_than_page_text(self):
        row = self.row_with(self.page, 'Questions', 'id', 'q-roi')
        self.assertEqual(self.cell('Questions', row, 'question_preview'), QUESTION_TEXT)
        self.assertEqual(self.cell('Questions', row, 'answer_preview'), ANSWER_TEXT)

    def test_a_question_with_no_answer_shows_the_admin_empty_marker(self):
        row = self.row_with(self.page, 'Questions', 'id', 'q-blame')
        self.assertEqual(self.cell('Questions', row, 'question_preview'), UNANSWERED_QUESTION)
        self.assertEqual(
            self.cell('Questions', row, 'answer_preview'),
            '-',
            'nothing stored must not look like a stored empty answer',
        )


class PeoplePaneTest(ChangelistTestCase):
    """«Люди»: the headline and the message count the Node pane calls `n`."""

    def setUp(self):
        super().setUp()
        self.page = self.changelist('People')

    def test_the_headline_is_a_cell_of_the_people_changelist(self):
        row = self.row_with(self.page, 'People', 'id', 'li:anna')
        self.assertEqual(self.cell('People', row, 'headline_preview'), ANNA_HEADLINE)

    def test_a_person_with_no_headline_shows_the_admin_empty_marker(self):
        row = self.row_with(self.page, 'People', 'id', 'x:vera')
        self.assertEqual(self.cell('People', row, 'headline_preview'), '-')

    def test_the_message_count_matches_the_count_in_the_database_for_every_row(self):
        for person_id, expected in EXPECTED_COUNTS.items():
            with self.subTest(person=person_id):
                counted = database.sql(
                    'select count(*) as n from interactions where person = %s', (person_id,)
                )[0]['n']
                self.assertEqual(counted, expected, 'the fixture should hold this count')
                row = self.row_with(self.page, 'People', 'id', person_id)
                self.assertEqual(
                    self.cell('People', row, 'interaction_count'),
                    str(expected),
                    f'{person_id} must show its own message count',
                )

    def test_a_person_nobody_has_written_to_shows_a_zero(self):
        row = self.row_with(self.page, 'People', 'id', 'x:vera')
        self.assertEqual(
            self.cell('People', row, 'interaction_count'),
            '0',
            'no messages is a zero, not an empty cell',
        )


class LeadsPaneTest(ChangelistTestCase):
    """«Лиды»: who they are and the pain in their own words."""

    def setUp(self):
        super().setUp()
        self.page = self.changelist('Leads')

    def test_the_headline_and_the_pain_are_cells_of_the_leads_changelist(self):
        row = self.row_with(self.page, 'Leads', 'person', 'Анна Петрова (linkedin)')
        self.assertEqual(self.cell('Leads', row, 'person_headline'), ANNA_HEADLINE)
        self.assertEqual(self.cell('Leads', row, 'pain_preview'), PAIN_TEXT)

    def test_a_lead_with_no_pain_and_no_headline_shows_the_empty_marker(self):
        row = self.row_with(self.page, 'Leads', 'person', 'Вера Кузнецова (x)')
        self.assertEqual(self.cell('Leads', row, 'person_headline'), '-')
        self.assertEqual(self.cell('Leads', row, 'pain_preview'), '-')


class LedgerPaneTest(ChangelistTestCase):
    """«Ушло» was already covered: the ledger listing is untouched."""

    def test_the_ledger_changelist_still_shows_the_four_columns_the_pane_shows(self):
        page = self.changelist('Ledger')
        row = self.row_with(page, 'Ledger', 'at', LEDGER[0])
        # when / network / topic / outcome — the Node pane's own four columns.
        self.assertEqual(self.cell('Ledger', row, 'network'), LEDGER[1])
        self.assertEqual(self.cell('Ledger', row, 'topic'), LEDGER[2])
        self.assertEqual(self.cell('Ledger', row, 'outcome'), LEDGER[3])


class SortableColumnsTest(ChangelistTestCase):
    """Every new column is a header the operator can click."""

    def test_each_new_column_is_sortable_and_its_sort_link_answers_200(self):
        for model, columns in NEW_COLUMNS.items():
            headers = self.probe(SORT_EVERY_COLUMN, PROBE_MODEL=MODEL_URLS[model])
            for column in columns:
                with self.subTest(model=model, column=column):
                    header = headers.get(column)
                    self.assertIsNotNone(header, f'{model} renders no {column} header')
                    self.assertTrue(header['sortable'], f'{model}.{column} is not sortable')
                    self.assertEqual(
                        header['status'],
                        200,
                        f'sorting {model} by {column} must not break the page',
                    )
                    self.assertTrue(
                        CYRILLIC.intersection(header['label']),
                        f'{model}.{column} header is not Russian: {header["label"]!r}',
                    )


class NoQueryPerRowTest(ChangelistTestCase):
    """The resolved name, the count and the headline ride the main query."""

    MODELS = ['interactions', 'people', 'leads']

    def test_the_changelists_issue_the_same_queries_at_a_full_page_of_rows(self):
        before = self.probe(QUERY_COUNT, PROBE_MODELS=json.dumps(self.MODELS))
        _add_bulk_rows(BULK_ROWS)
        try:
            after = self.probe(QUERY_COUNT, PROBE_MODELS=json.dumps(self.MODELS))
        finally:
            _remove_bulk_rows()

        for model in self.MODELS:
            with self.subTest(model=model):
                self.assertEqual(after[model]['status'], 200)
                # Positive control: the volume really did grow, so an equal
                # query count is a statement about this page and not about an
                # insert that silently did nothing.
                self.assertGreater(
                    after[model]['rows'],
                    before[model]['rows'],
                    f'the {model} page must render more rows after the bulk insert',
                )
                self.assertEqual(
                    after[model]['queries'],
                    before[model]['queries'],
                    f'the {model} changelist grew a query per row: '
                    f'{before[model]["queries"]} → {after[model]["queries"]}',
                )

        self.assertEqual(
            database.sql(
                'select count(*) as n from people where id like %s', (f'{BULK_PREFIX}:%',)
            )[0]['n'],
            0,
            'the volume rows must be gone again, so the fixture is what the other examples read',
        )


class DraftsStayViewOnlyTest(ChangelistTestCase):
    """Widening the READ surface wrote no new door into the approval queue."""

    def test_an_add_or_change_post_to_drafts_is_refused_and_the_listing_still_renders(self):
        result = self.probe(POST_TO_DRAFTS, PROBE_ID=str(draft_id))
        self.assertEqual(result['add'], 403, 'the admin must not create a draft')
        self.assertEqual(result['change'], 403, 'the admin must not rewrite a draft')
        self.assertEqual(result['listing'], 200, 'drafts stay inspectable')
        row = database.sql('select status, body from drafts where id = %s', (draft_id,))[0]
        self.assertEqual(row, {'status': 'waiting', 'body': DRAFT_BODY})

    def test_the_drafts_changelist_offers_no_bulk_action(self):
        page = self.probe(RENDER_CHANGELIST, PROBE_MODEL='drafts')
        self.assertEqual(page['status'], 200)
        self.assertNotIn('name="action"', page['html'], 'no bulk action over the queue')


class ScratchSchemaTest(ChangelistTestCase):
    """Positive control: the harness really did build and seed the schema."""

    def test_schema_sql_loaded_all_six_tables(self):
        for table in ('people', 'drafts', 'interactions', 'questions', 'ledger', 'leads'):
            with self.subTest(table=table):
                self.assertIn(table, database.tables())

    def test_the_fixture_holds_the_rows_the_examples_read(self):
        counts = {
            'people': len(PEOPLE),
            'interactions': len(INTERACTIONS),
            'questions': len(QUESTIONS),
            'leads': len(LEADS),
            'ledger': 1,
            'drafts': 1,
        }
        for table, expected in counts.items():
            with self.subTest(table=table):
                self.assertEqual(
                    database.sql(f'select count(*) as n from {table}')[0]['n'], expected
                )


if __name__ == '__main__':
    unittest.main()

"""The READ surface: every pane of the Node screen is readable in a changelist.

`web/index.html` is the parity reference (knowledge scout-2), and four of its
six panes show content that lives in no `list_display` by default: the reply
text and the person's name for «Ответы нам», the question and our answer for
«Вопросы рынка», the headline and the message count for «Люди», the headline
and the pain for «Лиды». Without those columns an operator who drops the Node
reader opens one detail page per row to read a single reply, so the columns are
the deliverable and these examples are what pins them.

Reading a row is half of it. The parity reference also puts six anchors in
front of the operator, five of them on tables this admin owns — the person's
profile in «Люди» and «Лиды», the question's source in «Вопросы рынка», the
thread for «Ответы нам», the publication in «Ушло» — and `ClickThroughTest`
is what pins those. (The sixth, the drafts pane's «куда», is served by
`/queue/`.) `LedgerPaneTest` carried the claim that «Ушло» "already matched
the ledger pane column for column"; it matched the four *values*, and the
pane's topic is a link while a `TextField` column renders as text, so that
claim is corrected here rather than inherited.

Two halves, for two kinds of claim:

  1. metadata, no database — the six `list_display` sets (the five this slice
     and its predecessor change, plus `Drafts`, which must not change),
     `direction` first in the interactions filter, and a Russian header plus
     an ordering field on every new column. Read out of
     `admin.site._registry` in a probe subprocess, the precedent of
     `tests/test_drafts_admin_view_only.py`; `django.setup()` needs no
     Postgres.
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

⚠️ Every claim about an ANCHOR is asserted on the extracted `href`, never on
cell text — `rows_of()` strips exactly the attribute the claim is about — so
`ClickThroughTest` reads cell MARKUP through its own probe. Both of its
helpers check the row width first, because a cell is addressed by its
`list_display` index plus the action-checkbox offset: against an admin that is
missing the column, those offsets shift by one and an unchecked read silently
asserts on a *neighbouring* cell, which is how a marker claim can pass without
the column existing at all. The width check alone is not enough on «Ушло»,
where the link column *replaced* a text column and the row is therefore just
as wide as before, so the marker example reads the same column's live link
first as its own positive control.

`NoQueryPerRowTest` sits in that second half, but its claim is about COST
rather than content: a changelist must not grow a query with its page. It
covers all five changelists that resolve something per row — the two annotated
values (the interaction's resolved person name, the person's message count)
and the four `list_select_related` foreign-key columns (`interactions.draft`,
`leads.person`, `questions.asker`, `drafts.target_person`) — and
`_add_bulk_rows` populates each of those foreign keys, because a page of rows
with a null one measures nothing (see the warning on that helper). The link
columns ride it too: four of the five read their own row's column and the
fifth reads the `people` row `list_select_related` already loads, so no
anchor may add a fetch.
"""

import json
import os
import re
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
        'id', 'ts', 'network', 'direction', 'kind', 'person_name', 'post_url_link',
        'draft', 'text_preview',
    ],
    'Questions': [
        'id', 'date', 'network', 'question_preview', 'url_link', 'asker_or_role', 'to_us',
        'topic', 'answer_preview', 'status',
    ],
    'People': [
        'id', 'name', 'profile_link', 'network', 'headline_preview', 'segment', 'status',
        'interaction_count', 'first_seen', 'created_at',
    ],
    'Leads': [
        'id', 'person', 'person_profile_link', 'stage', 'person_headline', 'pain_preview',
        'next_step', 'created_at', 'updated_at',
    ],
    # «Ушло»'s own anchor: `url_link` REPLACES the plain `url` column it used
    # to carry, so the address the pane makes clickable is clickable here too.
    'Ledger': ['id', 'at', 'network', 'topic', 'url_link', 'outcome'],
    # Slice 1's listing, kept view-only by slice 2: unchanged.
    'Drafts': [
        'id', 'network', 'kind', 'status', 'target_person', 'created_at', 'sent_at',
    ],
}

# Every column these two slices add, per model, with the field each one must
# sort by: the content previews and the resolved values first, then the five
# anchors and the «спросил» fallback.
NEW_COLUMNS = {
    'Interactions': {
        'person_name': 'person_label',
        'text_preview': 'text',
        'post_url_link': 'post_url',
    },
    'Questions': {
        'question_preview': 'question',
        'answer_preview': 'yatfa_answer',
        'url_link': 'url',
        'asker_or_role': 'asker',
    },
    'People': {
        'headline_preview': 'headline',
        'interaction_count': 'interaction_total',
        'profile_link': 'profile',
    },
    'Leads': {
        'person_headline': 'person__headline',
        'pain_preview': 'pain',
        'person_profile_link': 'person__profile',
    },
    'Ledger': {'url_link': 'url'},
}

MODEL_URLS = {
    'Interactions': 'interactions',
    'Questions': 'questions',
    'People': 'people',
    'Leads': 'leads',
    'Ledger': 'ledger',
    'Drafts': 'drafts',
}

# Django renders the action checkbox as a row's first cell only on a changelist
# that offers an action, and `DraftsAdmin` offers none: dropping its delete
# permission empties `get_actions`. So a drafts row is one cell narrower than
# every other model's, and the width control and the cell offsets below read
# that difference from here rather than assuming a checkbox everywhere.
ACTION_CHECKBOX = {model: 0 if model == 'Drafts' else 1 for model in LIST_DISPLAY}

CYRILLIC = set('абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ')

# --- fixture ---------------------------------------------------------------

PEOPLE = [
    # id, name, network, headline, profile
    (
        'li:anna',
        'Анна Петрова',
        'linkedin',
        'CTO в среднем продукте — живёт в согласованиях',
        # The one term in the fixture that appears only in a profile, so
        # searching for it proves the profile itself is searchable.
        'https://example.test/profile/anna-petrova',
    ),
    (
        'li:boris',
        'Борис Смирнов',
        'linkedin',
        'инженер данных, пишет про пайплайны',
        # A real profile, stored the way a person types one. `people.profile`
        # is free-text, so this is not a URL and must not become an `href`.
        'linkedin.com/in/boris',
    ),
    # No headline, no profile and no interaction: the "nothing stored" cells
    # and the zero.
    ('x:vera', 'Вера Кузнецова', 'x', None, None),
    # The two values the scheme check and the escaping exist for. Nothing
    # stops an operator pasting either into a free-text column.
    ('li:script', 'Сергей Скриптов', 'linkedin', None, 'javascript:alert(1)'),
    (
        'li:quoted',
        'Кирилл Кавычкин',
        'linkedin',
        None,
        'https://example.test/q"onmouseover="alert(1)',
    ),
]
ANNA_HEADLINE = PEOPLE[0][3]
ANNA_PROFILE = PEOPLE[0][4]
# The term that is in Anna's profile and in no other column of any row.
PROFILE_ONLY_TERM = 'anna-petrova'
NO_SCHEME_PROFILE = PEOPLE[1][4]
SCRIPT_PROFILE = PEOPLE[3][4]
QUOTED_PROFILE = PEOPLE[4][4]

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

# «Ответы нам» renders «ветка» → `i.post_url`: the thread the reply sits in,
# which is where answering it happens.
THREAD_URL = 'https://example.test/thread/anna'

INTERACTIONS = [
    # id, direction, kind, person, text, post_url
    ('i-anna-in', 'in', 'reply', 'li:anna', REPLY_TEXT, THREAD_URL),
    ('i-ghost-in', 'in', 'comment', GHOST, 'пишет кто-то, кого мы ещё не записали',
     'https://example.test/thread/ghost'),
    # No person and no thread: the two empty markers on one row.
    ('i-anon-in', 'in', 'reply', None, LONG_REPLY, None),
    ('i-anna-out', 'out', 'comment', 'li:anna', OUT_TEXT, 'https://example.test/thread/ours'),
    ('i-boris-in', 'in', 'reply', 'li:boris', BORIS_TEXT, None),
]
# Expected message count per person: `select count(*) ... where person = id`.
EXPECTED_COUNTS = {'li:anna': 2, 'li:boris': 1, 'x:vera': 0}

QUESTION_TEXT = 'Как считать окупаемость агентов, если они пишут код?'
ANSWER_TEXT = 'Есть ledger и очередь утверждений — считаем по отправленному.'
UNANSWERED_QUESTION = 'Кто отвечает, когда агент ошибается?'

ANONYMOUS_QUESTION = 'А кто это вообще спросил — мы не записали.'
# `questions.asker_role` is the free text the pane falls back to with
# `q.asker_name || q.asker_role`.
ANONYMOUS_ASKER_ROLE = 'основатель стартапа'
UNATTRIBUTED_QUESTION = 'И этот — без человека и без роли.'

# «Вопросы рынка» anchors the question itself to `q.url`.
QUESTION_URL = 'https://example.test/question/roi'

QUESTIONS = [
    # id, asker, asker_role, to_us, question, answer, topic, url
    ('q-roi', 'li:anna', None, True, QUESTION_TEXT, ANSWER_TEXT, 'ROI', QUESTION_URL),
    ('q-blame', 'li:boris', None, False, UNANSWERED_QUESTION, None, 'ответственность',
     'https://example.test/question/blame'),
    # No asker, but a role: `questions.asker` is nullable, so loading the
    # person with the question must keep the join a LEFT OUTER one and this
    # row must still be on the page — with the role in «спросил», which is
    # what the Node pane shows, and no link in «ссылка».
    ('q-anon', None, ANONYMOUS_ASKER_ROLE, False, ANONYMOUS_QUESTION, None, 'без автора', None),
    # Neither: nothing is stored about who asked, so the column has nothing to
    # fall back to and the admin's own marker is the honest cell.
    ('q-void', None, None, False, UNATTRIBUTED_QUESTION, None, 'без автора и роли', None),
]

PAIN_TEXT = 'каждое согласование — это переписка на три дня'

LEADS = [
    # person, stage, pain, next_step
    ('li:anna', 'talking', PAIN_TEXT, 'прислать разбор очереди'),
    ('x:vera', 'candidate', None, 'прокомментировать её пост'),
]

DRAFT_BODY = 'черновик для проверки, что drafts остались только для чтения'
# A second draft, addressed to someone: `drafts.target_person` is nullable, so
# the fixture draft above shows the empty marker and this one shows the
# person's own label — loading the person with the draft must leave both cells
# exactly as they were.
TARGETED_DRAFT_BODY = 'черновик с адресатом: ячейка показывает человека, а не его id'
TARGETED_DRAFT_PERSON = 'li:anna'

# «Ушло» renders ledger rows, and it anchors each row's topic to `l.url`:
# `LedgerAdmin` matched that pane's four *values* before this slice, with the
# address itself as escaped text. Two rows, so both the anchor and the missing
# link are rendered rather than assumed.
LEDGER = ('2026-11-19 12:00', 'linkedin', 'очередь утверждений', 'отклик: три комментария')
LEDGER_URL = 'https://example.test/post'
LEDGER_NO_URL = ('2026-11-18 09:00', 'x', 'тред без ссылки', 'тишина')

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

# A click-through claim is a claim about an `href`, and `rows_of()` strips
# exactly that attribute away, so this returns each row's cells as MARKUP.
# The stripped cells ride along so a row can still be found by its id, and
# the width control in `ClickThroughTest.markup_rows` reads both.
RENDER_CELL_MARKUP = HTTP_PRELUDE + """
client = operator_client()
response = client.get(changelist_url(os.environ['PROBE_MODEL']))
body = response.content.decode('utf-8')
table = body.split('<tbody>')[1].split('</tbody>')[0] if '<tbody>' in body else ''
print(json.dumps({
    'status': response.status_code,
    'rows': [
        {'text': [text_of(cell) for cell in CELL.findall(row)], 'markup': CELL.findall(row)}
        for row in ROW.findall(table)
    ],
    # The page as a whole, for the claim that a rejected scheme reaches no
    # `href` anywhere on it rather than merely not reaching its own cell.
    'html': body,
}))
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
targeted_draft_id = None


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
    global draft_id, targeted_draft_id
    for person_id, name, network, headline, profile in PEOPLE:
        database.sql(
            """insert into people (id, name, network, profile, headline, segment, first_seen)
               values (%s, %s, %s, %s, %s, 'founder', '2026-11-01')""",
            (person_id, name, network, profile, headline),
        )
    draft_id = database.sql(
        """insert into drafts (network, kind, status, body) values
           ('linkedin', 'comment', 'waiting', %s) returning id""",
        (DRAFT_BODY,),
    )[0]['id']
    targeted_draft_id = database.sql(
        """insert into drafts (network, kind, status, target_person, body) values
           ('linkedin', 'comment', 'waiting', %s, %s) returning id""",
        (TARGETED_DRAFT_PERSON, TARGETED_DRAFT_BODY),
    )[0]['id']
    for interaction_id, direction, kind, person, text, post_url in INTERACTIONS:
        database.sql(
            """insert into interactions (id, ts, network, direction, kind, person, post_url,
                                         text, draft_id)
               values (%s, '2026-11-20 10:00', 'linkedin', %s, %s, %s, %s, %s, %s)""",
            (interaction_id, direction, kind, person, post_url, text,
             draft_id if direction == 'out' else None),
        )
    for question_id, asker, asker_role, to_us, question, answer, topic, url in QUESTIONS:
        database.sql(
            """insert into questions (id, date, network, asker, asker_role, to_us, question,
                                      context, yatfa_answer, topic, url, status)
               values (%s, '2026-11-20', 'linkedin', %s, %s, %s, %s, 'контекст вопроса', %s,
                       %s, %s, 'открыт')""",
            (question_id, asker, asker_role, to_us, question, answer, topic, url),
        )
    for person, stage, pain, next_step in LEADS:
        database.sql(
            'insert into leads (person, stage, pain, next_step) values (%s, %s, %s, %s)',
            (person, stage, pain, next_step),
        )
    database.sql(
        """insert into ledger (at, network, topic, url, outcome)
           values (%s, %s, %s, %s, %s)""",
        (LEDGER[0], LEDGER[1], LEDGER[2], LEDGER_URL, LEDGER[3]),
    )
    database.sql(
        """insert into ledger (at, network, topic, url, outcome)
           values (%s, %s, %s, NULL, %s)""",
        LEDGER_NO_URL,
    )


def _add_bulk_rows(volume):
    """`volume` more people, each with one interaction, lead, question and draft.

    ⚠️ Every bulk row POPULATES the foreign key its changelist displays — each
    question's `asker` and each draft's `target_person` name that row's own
    bulk person. A bulk row with a null foreign key costs no per-row query, so
    seeding them empty would fill the measured page with cheap rows and the
    example would pass over a live N+1 — and worse, the cheap rows displace the
    fixture off page 1, which can make the count *fall*.
    """
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
    # Dated a day after the fixture questions, so `-date` puts the rows that
    # name an asker on the measured page rather than behind it.
    database.sql(
        """insert into questions (id, date, network, asker, to_us, question, topic, status)
           select %s || '-q:' || g, '2026-11-21', 'linkedin', %s || ':' || g, false,
                  'массовый вопрос ' || g, 'массовая тема', 'открыт'
           from generate_series(1, %s) g""",
        (BULK_PREFIX, BULK_PREFIX, volume),
    )
    # `drafts.id` is a `bigserial`, so these rows cannot carry the `bulk:`
    # prefix the others key on — `_remove_bulk_rows` finds them by their
    # `target_person` instead. `kind` and `status` are the fixture draft's own
    # values, which the table's CHECK constraints accept.
    database.sql(
        """insert into drafts (network, kind, status, target_person, body)
           select 'linkedin', 'comment', 'waiting', %s || ':' || g, 'массовый черновик ' || g
           from generate_series(1, %s) g""",
        (BULK_PREFIX, volume),
    )


def _remove_bulk_rows():
    # The rows that NAME a bulk person go first: `schema.sql` declares
    # `questions.asker`, `drafts.target_person` and `leads.person` with a bare
    # `REFERENCES people(id)`, so the database refuses to delete a person any
    # of them still points at. (`interactions.person` is free-form text with no
    # foreign key at all, so its own delete is ordered here only for company.)
    database.sql('delete from questions where asker like %s', (f'{BULK_PREFIX}:%',))
    database.sql('delete from drafts where target_person like %s', (f'{BULK_PREFIX}:%',))
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

    def test_questions_load_the_asker_with_the_question(self):
        # The `asker` column renders the related person: without this a full
        # 100-row page costs ~100 queries instead of one join.
        self.assertEqual(self.admins['Questions']['list_select_related'], ['asker'])

    def test_drafts_load_the_target_person_with_the_draft(self):
        # The `target_person` column renders the related person. Reads only:
        # `DraftsAdmin` stays view-only, and `DraftsStayViewOnlyTest` pins it.
        self.assertEqual(self.admins['Drafts']['list_select_related'], ['target_person'])

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
        # The extractor control: one cell per column, plus the action checkbox
        # where the changelist offers an action (see ACTION_CHECKBOX).
        expected = len(LIST_DISPLAY[model]) + ACTION_CHECKBOX[model]
        for row in page['rows']:
            self.assertEqual(
                len(row), expected, f'{model} row has {len(row)} cells, expected {expected}'
            )
        return page

    def cell(self, model, row, column):
        """One row's value for a `list_display` column."""
        return row[LIST_DISPLAY[model].index(column) + ACTION_CHECKBOX[model]]

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

    def test_the_asker_is_the_person_who_asked(self):
        # Loading the person with the question moves no content: the column
        # still renders the person's own label.
        asked = self.row_with(self.page, 'Questions', 'id', 'q-roi')
        self.assertEqual(self.cell('Questions', asked, 'asker_or_role'), 'Анна Петрова (linkedin)')

    def test_a_question_with_no_asker_falls_back_to_the_stored_role(self):
        # `q.asker_name || q.asker_role` on the Node pane: a question asked by
        # someone nobody has recorded still says who asked it. A null `asker`
        # must also keep its row on the page (a LEFT OUTER join, not an INNER
        # one), which is what reading the other column here checks.
        anonymous = self.row_with(self.page, 'Questions', 'id', 'q-anon')
        self.assertEqual(
            self.cell('Questions', anonymous, 'question_preview'), ANONYMOUS_QUESTION
        )
        self.assertEqual(
            self.cell('Questions', anonymous, 'asker_or_role'),
            ANONYMOUS_ASKER_ROLE,
            'the role is what the pane falls back to, not the empty marker',
        )

    def test_a_question_with_neither_asker_nor_role_shows_the_admin_empty_marker(self):
        unattributed = self.row_with(self.page, 'Questions', 'id', 'q-void')
        self.assertEqual(
            self.cell('Questions', unattributed, 'question_preview'), UNATTRIBUTED_QUESTION
        )
        self.assertEqual(
            self.cell('Questions', unattributed, 'asker_or_role'),
            '-',
            'with nothing stored about who asked, the marker is the honest cell',
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

    def test_a_person_is_findable_by_their_profile_url(self):
        # The profile was in no column and in no `search_fields`: an operator
        # holding the LinkedIn URL and wanting the person behind it could
        # reach them by neither route.
        found = self.changelist('People', f'?q={PROFILE_ONLY_TERM}')
        self.assertEqual(
            [self.cell('People', row, 'id') for row in found['rows']],
            ['li:anna'],
            f'{PROFILE_ONLY_TERM!r} is in Anna\'s profile and in no other column',
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
    """«Ушло»: the pane's four values, beside the topic's link in `url_link`."""

    def test_the_ledger_changelist_still_shows_the_four_columns_the_pane_shows(self):
        page = self.changelist('Ledger')
        row = self.row_with(page, 'Ledger', 'at', LEDGER[0])
        # when / network / topic / outcome — the Node pane's own four columns.
        self.assertEqual(self.cell('Ledger', row, 'network'), LEDGER[1])
        self.assertEqual(self.cell('Ledger', row, 'topic'), LEDGER[2])
        self.assertEqual(self.cell('Ledger', row, 'outcome'), LEDGER[3])


class DraftsPaneTest(ChangelistTestCase):
    """«Ждёт тебя» lives at `/queue/`: this listing is for inspection only."""

    def test_the_target_person_is_the_person_or_the_empty_marker(self):
        # Loading the person with the draft moves no content: the column still
        # renders the person's own label, and the draft addressed to nobody
        # still shows up with the admin's marker rather than dropping out.
        page = self.changelist('Drafts')
        addressed = self.row_with(page, 'Drafts', 'id', str(targeted_draft_id))
        self.assertEqual(
            self.cell('Drafts', addressed, 'target_person'), 'Анна Петрова (linkedin)'
        )
        unaddressed = self.row_with(page, 'Drafts', 'id', str(draft_id))
        self.assertEqual(self.cell('Drafts', unaddressed, 'target_person'), '-')


# The five anchors the Node screen puts in front of the operator, for the
# tables this admin owns: the model, the column that carries the link, the row
# to read it from (a column and the value that identifies that row), and the
# URL the anchor must open. The parity reference's sixth `href` is the drafts
# pane's «куда», which `/queue/` already serves.
ANCHORS = [
    ('People', 'profile_link', ('id', 'li:anna'), ANNA_PROFILE),
    ('Leads', 'person_profile_link', ('person', 'Анна Петрова (linkedin)'), ANNA_PROFILE),
    ('Questions', 'url_link', ('id', 'q-roi'), QUESTION_URL),
    ('Interactions', 'post_url_link', ('id', 'i-anna-in'), THREAD_URL),
    ('Ledger', 'url_link', ('at', LEDGER[0]), LEDGER_URL),
]

# The same five columns, on a row with nothing stored in them.
MISSING_URL_ROWS = [
    ('People', 'profile_link', ('id', 'x:vera')),
    ('Leads', 'person_profile_link', ('person', 'Вера Кузнецова (x)')),
    ('Questions', 'url_link', ('id', 'q-anon')),
    ('Interactions', 'post_url_link', ('id', 'i-anon-in')),
    ('Ledger', 'url_link', ('at', LEDGER_NO_URL[0])),
]

# Which row of `ANCHORS` carries each column's live link, so a marker claim
# can check the column renders one at all before reading its empty case.
ANCHOR_ROW = {(model, column): locator for model, column, locator, _url in ANCHORS}


class ClickThroughTest(ChangelistTestCase):
    """What the operator can click: the anchor half of every pane.

    The funnel's action is "comment on their post, then connect with a note",
    so the profile and the post are what a row has to reach. Before these
    columns the only `href` in a changelist row was the admin's own change
    link, and the operator's route to a LinkedIn profile was four pages and a
    copied text field.

    Each anchor claim is asserted on the extracted `href`: cell text cannot
    carry one, and `rows_of()` strips it. Each marker or plain-text claim goes
    through `cell()` instead — see the warning in the module docstring.
    """

    # One render per model, shared by the examples below: the fixture is
    # static here and every claim is a read.
    _markup = {}
    _rendered = {}

    def markup_page(self, model):
        """`model`'s changelist with each row's cells as markup and as text."""
        if model not in self._markup:
            page = self.probe(RENDER_CELL_MARKUP, PROBE_MODEL=MODEL_URLS[model])
            self.assertEqual(page['status'], 200)
            self.assertTrue(page['rows'], f'the {model} changelist rendered no row')
            # The width control `changelist()` applies, for the same reason: a
            # cell is addressed by its `list_display` index plus the action
            # checkbox, so a changelist one cell narrower than the pin reads a
            # neighbouring column and an unchecked claim passes vacuously.
            expected = len(LIST_DISPLAY[model]) + ACTION_CHECKBOX[model]
            for row in page['rows']:
                self.assertEqual(
                    len(row['markup']),
                    expected,
                    f'{model} row has {len(row["markup"])} cells, expected {expected}',
                )
            self._markup[model] = page
        return self._markup[model]

    def offset_of(self, model, column):
        return LIST_DISPLAY[model].index(column) + ACTION_CHECKBOX[model]

    def markup_of(self, model, column, locator):
        """The MARKUP of one row's `column` cell, the row found by `locator`."""
        by_column, value = locator
        rows = self.markup_page(model)['rows']
        matches = [r for r in rows if r['text'][self.offset_of(model, by_column)] == value]
        self.assertEqual(
            len(matches),
            1,
            f'expected exactly one {model} row with {by_column} == {value!r}',
        )
        return matches[0]['markup'][self.offset_of(model, column)]

    def text_row(self, model, column, value):
        """One row of the rendered changelist, via the width-controlled helper."""
        if model not in self._rendered:
            self._rendered[model] = self.changelist(model)
        return self.row_with(self._rendered[model], model, column, value)

    def hrefs_in(self, markup):
        return re.findall(r'href="([^"]*)"', markup)

    def test_every_pane_offers_its_anchor_as_a_link_in_the_row(self):
        for model, column, locator, url in ANCHORS:
            with self.subTest(model=model, column=column):
                self.assertEqual(
                    self.hrefs_in(self.markup_of(model, column, locator)),
                    [url],
                    f'{model}.{column} must open {url} from the changelist row',
                )

    def test_each_anchor_opens_in_a_new_tab_without_handing_over_the_window(self):
        # The shape `/queue/`'s own template already uses for «куда».
        for model, column, locator, _url in ANCHORS:
            with self.subTest(model=model, column=column):
                markup = self.markup_of(model, column, locator)
                self.assertIn('target="_blank"', markup)
                self.assertIn('rel="noopener"', markup)

    def test_a_missing_url_renders_the_admin_empty_marker(self):
        for model, column, (by_column, value) in MISSING_URL_ROWS:
            with self.subTest(model=model, column=column):
                # Positive control first, and the `Ledger` column is why it is
                # here: `url_link` REPLACED a plain `url` column, so the row is
                # exactly as wide as before and the width check cannot notice
                # that the cell being read is still the old text one — which
                # renders `-` for a null url too. Against an admin with no link
                # rendering at all, the marker claim alone passes vacuously;
                # reading the same column's live link is what gives it teeth.
                self.assertTrue(
                    self.hrefs_in(self.markup_of(model, column, ANCHOR_ROW[model, column])),
                    f'{model}.{column} renders no link at all, so its marker proves nothing',
                )
                row = self.text_row(model, by_column, value)
                self.assertEqual(
                    self.cell(model, row, column),
                    '-',
                    f'nothing stored in {model}.{column} must not look clickable',
                )

    def test_a_value_that_is_not_an_http_url_stays_text_and_is_no_link(self):
        # `people.profile` is free text, and a profile stored without a scheme
        # is ordinary rather than hostile: it reads as itself, not as a link.
        row = self.text_row('People', 'id', 'li:boris')
        self.assertEqual(self.cell('People', row, 'profile_link'), NO_SCHEME_PROFILE)
        self.assertEqual(
            self.hrefs_in(self.markup_of('People', 'profile_link', ('id', 'li:boris'))),
            [],
        )

    def test_a_javascript_value_reaches_no_href_on_the_page(self):
        row = self.text_row('People', 'id', 'li:script')
        self.assertEqual(self.cell('People', row, 'profile_link'), SCRIPT_PROFILE)
        self.assertEqual(
            self.hrefs_in(self.markup_of('People', 'profile_link', ('id', 'li:script'))),
            [],
            'a rejected scheme must not reach its own cell',
        )
        self.assertNotIn(
            'javascript:',
            ''.join(self.hrefs_in(self.markup_page('People')['html'])),
            'and no href anywhere on the page may carry it either',
        )

    def test_an_embedded_quote_cannot_open_a_second_attribute(self):
        markup = self.markup_of('People', 'profile_link', ('id', 'li:quoted'))
        hrefs = self.hrefs_in(markup)
        self.assertEqual(len(hrefs), 1, f'the cell must carry exactly one href: {markup!r}')
        self.assertIn(
            '&quot;', hrefs[0], 'the stored quote belongs inside the attribute, escaped'
        )
        self.assertNotIn(
            'onmouseover="',
            markup,
            'an embedded quote must not be able to open an event handler',
        )
        row = self.text_row('People', 'id', 'li:quoted')
        self.assertEqual(self.cell('People', row, 'profile_link'), 'профиль')


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
    """The resolved name, the count, the headline and every foreign-key column
    ride the main query."""

    MODELS = ['interactions', 'people', 'leads', 'questions', 'drafts']

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
        self.assertEqual(
            database.sql(
                'select count(*) as n from questions where asker like %s',
                (f'{BULK_PREFIX}:%',),
            )[0]['n'],
            0,
            'the volume questions must be gone again too',
        )
        self.assertEqual(
            database.sql(
                'select count(*) as n from drafts where target_person like %s',
                (f'{BULK_PREFIX}:%',),
            )[0]['n'],
            0,
            'the volume drafts must be gone again too',
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
            'ledger': 2,
            'drafts': 2,
        }
        for table, expected in counts.items():
            with self.subTest(table=table):
                self.assertEqual(
                    database.sql(f'select count(*) as n from {table}')[0]['n'], expected
                )


if __name__ == '__main__':
    unittest.main()

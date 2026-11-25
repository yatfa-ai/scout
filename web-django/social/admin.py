"""Django admin: the ops surface for the `social` database.

Per the v2 conventions (knowledge scout-3) the admin is how people, leads,
questions and the send log are maintained; only the approval queue gets a
custom UI, and it has one: `/queue/`. All six tables are registered here,
`drafts` view-only because that queue owns its writes (see `DraftsAdmin`).

The changelists carry the content the Node screen (`web/index.html`, the
parity reference) puts in front of the operator, so each of its panes is
readable here without opening a detail page: the reply text and the person's
name for «Ответы нам», the question and our answer for «Вопросы рынка», the
headline and the message count for «Люди», the headline and the pain for
«Лиды». «Ушло» is `LedgerAdmin`, which already matches that pane column for
column, and «Ждёт тебя» is the approval queue at `/queue/`.

Long text is shown as one truncated line (`_preview`): a changelist cell is a
scanning surface, and the full value is one click away on the detail page.
"""

from django.contrib import admin
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from .models import Drafts, Interactions, Leads, Ledger, People, Questions

admin.site.site_header = 'Yatfa Scout — админка'
admin.site.site_title = 'Yatfa Scout'
admin.site.index_title = 'Данные социального присутствия'
# «Открыть сайт» in the admin header, the one outbound link the admin offers.
# Unset, it defaults to `/`, which this project did not serve at all until the
# root route landed beside this line — and even now `/` is only a redirect to
# the queue, so pointing straight at the queue saves the hop and says where
# the link goes. The stock `admin/base.html` renders whatever
# `AdminSite.each_context` puts here, so this one line replaces a custom
# `AdminSite` or a `base_site.html` override. The login page carries no such
# link: Django renders the site link only in the authenticated staff header.
admin.site.site_url = '/queue/'

# Long enough for the first sentence of a reply or a question, short enough to
# leave the scanning columns beside it readable.
PREVIEW_LIMIT = 90


def _preview(value, limit=PREVIEW_LIMIT):
    """`value` as one line for a changelist cell, truncated at `limit`.

    Returns None for a missing value, so the admin renders its own empty
    marker instead of a blank cell — `interactions.text`, `questions
    .yatfa_answer`, `people.headline` and `leads.pain` are all nullable, and
    "nothing stored" must not look like "stored and empty".
    """
    if value is None:
        return None
    text = ' '.join(str(value).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + '…'


@admin.register(People)
class PeopleAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'network',
        'headline_preview',
        'segment',
        'status',
        'interaction_count',
        'first_seen',
        'created_at',
    )
    list_filter = ('network', 'status', 'segment')
    search_fields = ('id', 'name', 'headline', 'relevance')
    ordering = ('-created_at',)

    def get_queryset(self, request):
        """Annotate the message count the «Люди» pane shows as `n`.

        `interactions.person` carries no foreign key in `schema.sql` (plain
        `text`), so there is no reverse relation to aggregate: this is the
        correlated subquery `web/server.mjs` writes by hand, and `Coalesce`
        keeps a person nobody has written to at 0 rather than NULL.
        """
        counted = (
            Interactions.objects.filter(person=OuterRef('pk'))
            .values('person')
            .annotate(n=Count('*'))
            .values('n')
        )
        return (
            super()
            .get_queryset(request)
            .annotate(
                interaction_total=Coalesce(
                    Subquery(counted, output_field=IntegerField()),
                    0,
                    output_field=IntegerField(),
                )
            )
        )

    @admin.display(description='заголовок', ordering='headline')
    def headline_preview(self, obj):
        return _preview(obj.headline)

    @admin.display(description='сообщений', ordering='interaction_total')
    def interaction_count(self, obj):
        return obj.interaction_total


@admin.register(Drafts)
class DraftsAdmin(admin.ModelAdmin):
    """View-only: the approval queue owns every write to `drafts`.

    `drafts` is the approval queue, and each of its transitions carries a
    consequence a generic change form cannot express — `sent` is refused
    unless the row is `approved`, approving moves exactly one `waiting` row,
    and editing a body resets the row to `waiting` and clears its approval.
    The queue is the only surface that holds those together: `web/server.mjs`
    and this project's own `/queue/` page (knowledge scout-3 decisions 3
    and 4 — the admin is the ops surface for people, leads, questions and the
    ledger, and the queue gets a custom UI).

    So the listing and detail page stay, for inspection, and nothing writes:
    removing the three write permissions also empties `get_actions`, because
    Django filters `delete_selected` on `has_delete_permission`.
    """

    list_display = ('id', 'network', 'kind', 'status', 'target_person', 'created_at', 'sent_at')
    list_filter = ('status', 'network', 'kind')
    search_fields = ('body', 'target_note', 'target_url')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Interactions)
class InteractionsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'ts',
        'network',
        'direction',
        'kind',
        'person_name',
        'draft',
        'text_preview',
    )
    # `direction` first: «Ответы нам» is `direction = 'in'`, so the pane the
    # Node screen gives its own tab is one click away here.
    list_filter = ('direction', 'network', 'kind')
    search_fields = ('id', 'person', 'text', 'post_url')
    # `draft` renders the related draft's own label, so load it with the
    # interaction: without this the column costs one query per row that has a
    # draft, and the cost grows with the page.
    list_select_related = ('draft',)
    ordering = ('-ts',)

    def get_queryset(self, request):
        """Resolve the person's name without a relation to lean on.

        `schema.sql` declares `interactions.person` as plain `text` with no
        `REFERENCES`, and the column is free-form, so `web/server.mjs` reaches
        the name with `left join people p on p.id = i.person`. The ORM
        equivalent is a correlated subquery; it rides the main query, so the
        name costs no query per row.
        """
        return (
            super()
            .get_queryset(request)
            .annotate(
                person_label=Subquery(
                    People.objects.filter(pk=OuterRef('person')).values('name')[:1]
                )
            )
        )

    @admin.display(description='человек', ordering='person_label')
    def person_name(self, obj):
        """The `people` row's name, falling back to the stored id.

        An id with no `people` row is real in this data (an interaction can
        name someone nobody has recorded yet), and showing the raw id is what
        the Node pane does with `person_name || person`.
        """
        if obj.person_label is None:
            return obj.person
        return obj.person_label

    @admin.display(description='текст', ordering='text')
    def text_preview(self, obj):
        return _preview(obj.text)


@admin.register(Questions)
class QuestionsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'date',
        'network',
        'question_preview',
        'asker',
        'to_us',
        'topic',
        'answer_preview',
        'status',
    )
    list_filter = ('to_us', 'network', 'status')
    search_fields = ('id', 'question', 'context', 'yatfa_answer')
    ordering = ('-date',)

    @admin.display(description='вопрос', ordering='question')
    def question_preview(self, obj):
        return _preview(obj.question)

    @admin.display(description='наш ответ', ordering='yatfa_answer')
    def answer_preview(self, obj):
        return _preview(obj.yatfa_answer)


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
    list_display = ('id', 'at', 'network', 'topic', 'url', 'outcome')
    list_filter = ('network', 'outcome')
    search_fields = ('topic', 'url', 'outcome')
    ordering = ('-at',)


@admin.register(Leads)
class LeadsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'person',
        'stage',
        'person_headline',
        'pain_preview',
        'next_step',
        'created_at',
        'updated_at',
    )
    list_filter = ('stage',)
    search_fields = ('pain', 'next_step', 'source_url')
    # The headline comes off the related `people` row, so load it with the
    # lead instead of once per row.
    list_select_related = ('person',)
    # Left as it is on purpose: the Node pane orders by stage priority, and an
    # annotated ordering here would override the operator's own column clicks.
    # Stage is a filter and every column is sortable instead.
    ordering = ('-updated_at',)

    @admin.display(description='заголовок', ordering='person__headline')
    def person_headline(self, obj):
        return _preview(obj.person.headline)

    @admin.display(description='боль', ordering='pain')
    def pain_preview(self, obj):
        return _preview(obj.pain)

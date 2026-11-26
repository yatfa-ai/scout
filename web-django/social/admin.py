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
«Лиды». «Ушло» is `LedgerAdmin` and «Ждёт тебя» is the approval queue at
`/queue/`.

They also carry that screen's *click-through* (`_link`). The parity reference
puts six anchors in front of the operator, and five of them belong to tables
this admin owns: the person's profile in «Люди» and «Лиды», the question's
source in «Вопросы рынка», the thread a reply sits in for «Ответы нам», and
the publication in «Ушло». (The sixth, the drafts pane's «куда», is served by
`/queue/`.) The lead funnel's whole action is "comment on their post, then
connect with a note", so reaching the post or the profile must not cost a
detail page — and a changelist renders a `TextField` as text, which is why
`LedgerAdmin`'s plain `url` column was never clickable and why «Ушло» matched
that pane in its *values* rather than column for column.

Long text is shown as one truncated line (`_preview`): a changelist cell is a
scanning surface, and the full value is one click away on the detail page.
"""

from django.contrib import admin
from django.db.models import Count, IntegerField, OuterRef, Subquery, TextField
from django.db.models.functions import Coalesce
from django.utils.html import format_html

from .links import web_href
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


def _link(url, label):
    """`label` as an anchor to `url`, or `url` as plain text if it is not one.

    Shares `_preview`'s convention: None for a missing value, so the admin
    renders its own empty marker rather than an empty or dead anchor —
    `people.profile`, `questions.url`, `interactions.post_url` and
    `ledger.url` are all nullable, and "nothing stored" must not look like
    "stored and clickable".

    Which values may become an `href` is `links.web_href`'s decision, not this
    module's: the queue card's «куда» asks the same question about the same
    kind of free-form column, and the rule answering it twice is how the two
    surfaces drifted apart in the first place. That module's docstring carries
    the reason the check exists at all.

    What to render instead is this surface's own decision: anything
    `web_href` rejects falls through to `_preview`, which is the honest
    rendering for a value that is simply not a link, and keeps a long one from
    widening the scanning column. `format_html` does the escaping on top, so a
    value carrying a quote cannot close the attribute and open a second one.

    `label` is the column's own header, so the header and the link say the
    same word and the column needs no second vocabulary.
    """
    if url is None:
        return None
    href = web_href(url)
    if href is not None:
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', href, label)
    # `_preview` of a whitespace-only value is the empty string, which would
    # render as a blank cell; the empty marker is the honest cell there.
    return _preview(url) or None


class EmptyTextIsNullAdmin(admin.ModelAdmin):
    """An empty textarea over a nullable `text` column stores NULL, not `''`.

    A change form submits *every* field, so an operator correcting one column
    also rewrites the row's unset ones — and on origin/main each of those
    rewrites turned `NULL` into `''`. Measured on this project's own backup
    rows: submitting a change form exactly as it rendered, with nothing
    edited, changed two columns on a person (`followers`, `relevance`), one on
    a question (`yatfa_answer`), two on an interaction (`parent_url`, `note`)
    and one on a lead (`pain`). The add form did it too — a new person was
    born with all five optional columns `''`.

    `schema.sql` gives a nullable `text` column exactly one "no value" state,
    `NULL`, and the rest of the project agrees: `makeFlag` in `lib/cli.mjs`
    defaults an absent flag to `null`, `bin/import.mjs` binds `?? null` on
    every optional column, this project's own queue writes
    `skip_reason=reason or None` (`queue.skip`), and the backup carries zero
    empty strings. The admin was the only writer producing one.

    Why that is worth a guard, given a changelist cell renders the two
    identically (`admin.utils.display_for_field` returns `empty_value_display`
    for anything in `field.empty_values`): the machinery the operator
    *navigates by* is not identical.

      * `AllValuesFieldListFilter` offers "no value" as `?segment__isnull=
        True`, and `segment` is in `PeopleAdmin.list_filter`. Of the 24 people
        in the backup, six have no segment; that filter returned six rows
        before an unedited save of one of them and five after — the saved
        person moved behind a second choice, `?segment=`, whose rendered label
        is the empty string, an invisible link in the «сегмент» sidebar. An
        operator filtering «сегмент: Пусто» to find the people still needing a
        segment silently stops seeing the ones they have already opened.
      * Postgres sorts NULLs last and `''` first, so ascending by «сегмент»
        the saved row leads the changelist while the untouched ones sit at the
        bottom.

    The cause is a framework asymmetry rather than anything this project did.
    In Django 5.2.19 `CharField.formfield` carries

        if self.null and not connection.features.interprets_empty_strings_as_nulls:
            defaults['empty_value'] = None

    and `TextField.formfield` has no such line: it passes only `max_length`
    and the `Textarea` widget and spreads `**kwargs`. So a nullable
    `TextField` builds a `forms.CharField` whose `empty_value` is `''`, an
    empty textarea cleans to `''`, and the model stores it. `inspectdb` emits
    `TextField` for every `text` column, which is why this project met the gap
    on 22 columns at once (People 5, Interactions 6, Questions 6, Ledger 2,
    Leads 3). This mixin passes for `TextField` exactly what Django already
    passes for `CharField`.

    Three details of the guard, each load-bearing:

      * `kwargs.setdefault`, not assignment — a per-field override elsewhere
        in this module must keep winning.
      * `db_field.null` — a non-null column's only "no value" state *is* `''`,
        so writing NULL there would be the defect rather than the fix. It is
        not behaviour-bearing on today's schema (every non-nullable
        `TextField` here is required, so an empty submission is a form error
        either way); it stays as a narrowing of intent.
      * `isinstance(..., TextField)` — the census is deliberately *not*
        "every field that cleans an empty submission to `''`". Asked that way
        across every writable admin's change form it answered 25, the three
        extras being `User.email`, `User.first_name` and `User.last_name`
        from `django.contrib.auth`: a `CharField` and an `EmailField`, and
        `null=False`, so `''` is the right stored value there and `UserAdmin`
        must not inherit this.

    `DraftsAdmin` deliberately does not inherit it either: it is view-only, so
    it builds no form and its six nullable columns are unreachable from here —
    the approval queue (`social/queue.py`, `/queue/`) owns every write to
    `drafts`.

    Nothing rendered changes: this decides only what a form field *cleans* on
    submit. Clearing a field now means "nothing stored" rather than "stored
    and empty", which is the answer the CLI already gives; a whitespace-only
    submission lands there too, because `forms.CharField` strips before
    testing emptiness — the same answer `links.web_href` gives a
    whitespace-only URL.

    Inherited explicitly beside `FrozenPrimaryKeyAdmin` at each of the five
    writable admins rather than folded into it, so the rule is visible where
    it applies and `drafts` cannot acquire it by accident.
    """

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, TextField) and db_field.null and db_field.choices is None:
            kwargs.setdefault('empty_value', None)
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class FrozenPrimaryKeyAdmin(admin.ModelAdmin):
    """Offers an existing row's primary key for reading, never for editing.

    `people`, `interactions` and `questions` declare an operator-supplied
    `text PRIMARY KEY`, so the key is an ordinary editable field — which is
    right on the *add* form, where the operator must supply it, and destructive
    on the *change* form, where editing it does not rename the row. Django runs
    no uniqueness check on a primary key once the instance exists
    (`Model._perform_unique_checks` skips the pk unless `_state.adding`), and
    `Model._save_table` then issues `UPDATE … WHERE id = <the submitted
    value>`: no row matches and the save falls through to an INSERT, copying
    the row under the new id and leaving every reference pointing at the
    original; a *different* row matches and that row's columns are overwritten
    with this form's values. The admin reports success either way, and in the
    overwrite case the row count does not move, so no count-based surface
    reveals it. Measured on this project's own data: `q-003` resubmitted as
    `q-004` — one slipped digit — replaced `q-004`'s recorded market question
    and left the table seven rows long.

    The id scheme here is what makes that a live hazard rather than a
    curiosity: `interactions` are hand-minted sequentials
    (`li-20261023-01`…), `questions` are `q-001`…`q-007`, and the three
    text-keyed tables hold 42 id pairs exactly one keystroke apart.

    So the key becomes readonly as soon as there is a row to protect. A
    readonly field is excluded from the generated form, so a submitted value
    cannot reach the instance at all — the guard is the absence of the field,
    not a validation that could be bypassed. The key still *renders* on the
    change page (as `<div class="readonly">li:anna</div>`), because the
    operator needs to see which row they are editing, and the add form is
    untouched, including its duplicate-key error.

    ⚠️ The discriminator is `pk.formfield() is None`, not `pk.editable`. Every
    one of this project's six models reports `editable = True` on its primary
    key, `BigAutoField` included — `AutoFieldMixin.formfield` returns None
    instead, which is how a generated id stays out of forms. Freezing on
    `editable` would therefore add a readonly `id` row to the `drafts`,
    `leads` and `ledger` detail pages, which never showed one; `formfield()
    is None` splits the six cleanly 3/3. Both halves are pinned by
    `tests/test_admin_primary_key_frozen.py`.

    Renaming a row on purpose is not provided here, and this is not a
    substitute for one: it would have to re-point every referencing column
    (`interactions.person`, `questions.asker`, `leads.person`, …), several of
    which carry no foreign key in `schema.sql` at all. The honest answer today
    is a surface that refuses rather than one that silently corrupts.

    One rendering consequence, accepted rather than worked around: the key's
    row moves to the *bottom* of the change page's fieldset, because
    `ModelAdmin.get_fields` lays out the form's fields and then the readonly
    ones. Pinning its old position would mean declaring explicit
    `fields`/`fieldsets` on five admins, which trades one line of layout for
    five hand-maintained column lists that a new column would silently miss.
    """

    def get_readonly_fields(self, request, obj=None):
        readonly = tuple(super().get_readonly_fields(request, obj))
        if obj is None:
            # The add form: the operator supplies the key, so it must stay
            # editable and required there.
            return readonly
        pk = self.model._meta.pk
        if pk.formfield() is None or pk.name in readonly:
            # A database-generated key reaches no form in the first place, so
            # naming it here would only add a row to a detail page that never
            # had one.
            return readonly
        return readonly + (pk.name,)


@admin.register(People)
class PeopleAdmin(EmptyTextIsNullAdmin, FrozenPrimaryKeyAdmin):
    list_display = (
        'id',
        'name',
        'profile_link',
        'network',
        'headline_preview',
        'segment',
        'status',
        'interaction_count',
        'first_seen',
        'created_at',
    )
    list_filter = ('network', 'status', 'segment')
    # `profile` is here as well as in the listing: an operator who has the
    # LinkedIn URL and wants the person behind it had no way to search for it.
    search_fields = ('id', 'name', 'headline', 'relevance', 'profile')
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

    @admin.display(description='профиль', ordering='profile')
    def profile_link(self, obj):
        """«Люди» anchors the person's name to `p.profile`; this is that link.

        A column of its own rather than the name turned into an anchor: the
        name stays a plain sortable cell, and the value is rendered once.
        `profile` is this row's own column, so the link costs no query.
        """
        return _link(obj.profile, 'профиль')

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
    # `target_person` renders the related person's own label, so load it with
    # the draft: without this the column costs one query per row that names
    # someone, and the cost grows with the page (100 rows by default).
    list_select_related = ('target_person',)
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Interactions)
class InteractionsAdmin(EmptyTextIsNullAdmin, FrozenPrimaryKeyAdmin):
    list_display = (
        'id',
        'ts',
        'network',
        'direction',
        'kind',
        'person_name',
        'post_url_link',
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

    @admin.display(description='ссылка', ordering='post_url')
    def post_url_link(self, obj):
        """«Ответы нам» renders «ветка» → `i.post_url`; this is that link.

        The pane's whole purpose is answering a reply, and answering it means
        opening the thread it sits in. `post_url` is this row's own column, so
        the link costs no query.
        """
        return _link(obj.post_url, 'ссылка')

    @admin.display(description='текст', ordering='text')
    def text_preview(self, obj):
        return _preview(obj.text)


@admin.register(Questions)
class QuestionsAdmin(EmptyTextIsNullAdmin, FrozenPrimaryKeyAdmin):
    list_display = (
        'id',
        'date',
        'network',
        'question_preview',
        'url_link',
        'asker_or_role',
        'to_us',
        'topic',
        'answer_preview',
        'status',
    )
    list_filter = ('to_us', 'network', 'status')
    search_fields = ('id', 'question', 'context', 'yatfa_answer')
    # `asker` renders the related person's own label, so load it with the
    # question: without this the column costs one query per row that names
    # someone, and the cost grows with the page (100 rows by default).
    list_select_related = ('asker',)
    ordering = ('-date',)

    @admin.display(description='вопрос', ordering='question')
    def question_preview(self, obj):
        return _preview(obj.question)

    @admin.display(description='ссылка', ordering='url')
    def url_link(self, obj):
        """«Вопросы рынка» anchors the question to `q.url`; this is that link.

        A column of its own rather than the question text turned into an
        anchor, for the same reason `PeopleAdmin.profile_link` is one: the
        question stays a plain sortable preview. `url` is this row's own
        column, so the link costs no query.
        """
        return _link(obj.url, 'ссылка')

    @admin.display(description='спросил', ordering='asker')
    def asker_or_role(self, obj):
        """The `people` row, falling back to the stored role.

        `questions.asker` is nullable and `asker_role` is free text beside it,
        which is why the pane renders `q.asker_name || q.asker_role`: a
        question asked by someone nobody has recorded still says who asked it.
        The bare `asker` column dropped that role and showed the empty marker
        instead — `InteractionsAdmin.person_name` has had the same
        person-else-fallback shape one table over all along, and the
        asymmetry was the defect.

        A set asker renders exactly what the bare column did (the row's own
        label), so only the NULL case changes; `list_select_related` loads the
        person with the question, so neither branch costs a query.
        """
        if obj.asker is not None:
            return obj.asker
        return _preview(obj.asker_role)

    @admin.display(description='наш ответ', ordering='yatfa_answer')
    def answer_preview(self, obj):
        return _preview(obj.yatfa_answer)


@admin.register(Ledger)
class LedgerAdmin(EmptyTextIsNullAdmin, FrozenPrimaryKeyAdmin):
    # `url_link` *replaces* the plain `url` column rather than sitting beside
    # it: «Ушло» anchors the topic to `l.url`, and `Ledger.url` is a
    # `TextField`, so the column rendered the address as escaped text. The
    # accepted consequence is that the raw URL is no longer copyable out of a
    # changelist cell; it stays searchable below and on the detail page.
    list_display = ('id', 'at', 'network', 'topic', 'url_link', 'outcome')
    list_filter = ('network', 'outcome')
    search_fields = ('topic', 'url', 'outcome')
    ordering = ('-at',)

    @admin.display(description='ссылка', ordering='url')
    def url_link(self, obj):
        """The publication itself: «Ушло» anchors `l.topic` to `l.url`.

        `topic` is its own sortable column here, so the anchor is this cell
        rather than that one. `url` is this row's own column, so the link
        costs no query.
        """
        return _link(obj.url, 'ссылка')


@admin.register(Leads)
class LeadsAdmin(EmptyTextIsNullAdmin, FrozenPrimaryKeyAdmin):
    list_display = (
        'id',
        'person',
        'person_profile_link',
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

    @admin.display(description='профиль', ordering='person__profile')
    def person_profile_link(self, obj):
        """«Лиды» renders a «профиль» anchor → `l.profile`; this is that link.

        The funnel's own action is "comment on their post, then connect with a
        note", and from this changelist the profile was three pages and a
        copied text field away (lead detail, where the person widget shows a
        name and no URL, then the people changelist, then the person's own
        detail page). The profile comes off the related `people` row, which
        `list_select_related` already loads with the lead, so the link rides
        what is loaded rather than adding a fetch.

        The lead's own `source_url` («где нашли») gets no anchor: the pane
        anchors the person's profile and nothing else.
        """
        return _link(obj.person.profile, 'профиль')

    @admin.display(description='заголовок', ordering='person__headline')
    def person_headline(self, obj):
        return _preview(obj.person.headline)

    @admin.display(description='боль', ordering='pain')
    def pain_preview(self, obj):
        return _preview(obj.pain)

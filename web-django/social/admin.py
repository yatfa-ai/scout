"""Django admin: the ops surface for the `social` database.

Per the v2 conventions (knowledge scout-3) the admin is how people, leads,
questions and the send log are maintained; only the approval queue gets a
custom UI, in a later slice. All six tables are registered here.
"""

from django.contrib import admin

from .models import Drafts, Interactions, Leads, Ledger, People, Questions

admin.site.site_header = 'Yatfa Scout — админка'
admin.site.site_title = 'Yatfa Scout'
admin.site.index_title = 'Данные социального присутствия'


@admin.register(People)
class PeopleAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'network', 'segment', 'status', 'first_seen', 'created_at')
    list_filter = ('network', 'status', 'segment')
    search_fields = ('id', 'name', 'headline', 'relevance')
    ordering = ('-created_at',)


@admin.register(Drafts)
class DraftsAdmin(admin.ModelAdmin):
    list_display = ('id', 'network', 'kind', 'status', 'target_person', 'created_at', 'sent_at')
    list_filter = ('status', 'network', 'kind')
    search_fields = ('body', 'target_note', 'target_url')
    ordering = ('-created_at',)


@admin.register(Interactions)
class InteractionsAdmin(admin.ModelAdmin):
    list_display = ('id', 'ts', 'network', 'direction', 'kind', 'person', 'draft')
    list_filter = ('network', 'direction', 'kind')
    search_fields = ('id', 'person', 'text', 'post_url')
    ordering = ('-ts',)


@admin.register(Questions)
class QuestionsAdmin(admin.ModelAdmin):
    list_display = ('id', 'date', 'network', 'asker', 'to_us', 'topic', 'status')
    list_filter = ('to_us', 'network', 'status')
    search_fields = ('id', 'question', 'context', 'yatfa_answer')
    ordering = ('-date',)


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
    list_display = ('id', 'at', 'network', 'topic', 'url', 'outcome')
    list_filter = ('network', 'outcome')
    search_fields = ('topic', 'url', 'outcome')
    ordering = ('-at',)


@admin.register(Leads)
class LeadsAdmin(admin.ModelAdmin):
    list_display = ('id', 'person', 'stage', 'next_step', 'created_at', 'updated_at')
    list_filter = ('stage',)
    search_fields = ('pain', 'next_step', 'source_url')
    ordering = ('-updated_at',)

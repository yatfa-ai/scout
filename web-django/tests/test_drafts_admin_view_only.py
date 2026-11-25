"""Invariants #1–#3 (knowledge scout-2): the admin performs no drafts write.

`drafts` is the approval queue. Every write to it carries a state-machine
consequence the admin's generic change form cannot express — `sent` is refused
unless the row is `approved`, approving moves exactly one `waiting` row, and
editing a body resets the row to `waiting` and clears its approval (see the
four guarded routes in `web/server.mjs` and `commands.sent` in `bin/soc.mjs`,
which also writes the `ledger` and `interactions` rows). So `DraftsAdmin` is
view-only: inspectable, never writable. Mutation stays with the approval queue
(knowledge scout-3 decisions 3 and 4).

The other ops tables are the positive control: they must stay fully writable,
so a run of this module can never pass because the registry handed back
something that was not writable to begin with.

Like its sibling modules this probe opens no database: `django.setup()` loads
app configs, model metadata and the admin registry without talking to
Postgres. The permission methods are read through `admin.site._registry`, with
a stub request because Django's default implementations dereference
`request.user.has_perm(...)`.
"""

import json
import unittest

from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

READ_ADMIN_PERMISSIONS = """
import json
import django
django.setup()
from django.contrib import admin
from social.models import Drafts, Leads, Ledger, People, Questions


class _User:
    is_active = is_staff = is_superuser = True

    def has_perm(self, *args, **kwargs):
        return True


class _Request:
    user = _User()
    GET = {}
    META = {}


request = _Request()


def read(model):
    model_admin = admin.site._registry[model]
    return {
        'add': model_admin.has_add_permission(request),
        'change': model_admin.has_change_permission(request),
        'delete': model_admin.has_delete_permission(request),
        'view': model_admin.has_view_permission(request),
        'actions': sorted(model_admin.get_actions(request)),
        'list_display': list(model_admin.list_display),
        'list_filter': list(model_admin.list_filter),
        'search_fields': list(model_admin.search_fields),
        'ordering': list(model_admin.ordering),
    }


print(json.dumps({
    m.__name__: read(m) for m in (Drafts, People, Leads, Questions, Ledger)
}))
"""

# The listing configuration slice 1 shipped. Drafts stay fully inspectable:
# removing a write permission must not quietly cost the operator a column, a
# filter or the newest-first order.
DRAFTS_LISTING = {
    'list_display': ['id', 'network', 'kind', 'status', 'target_person', 'created_at', 'sent_at'],
    'list_filter': ['status', 'network', 'kind'],
    'search_fields': ['body', 'target_note', 'target_url'],
    'ordering': ['-created_at'],
}

# Ops surface per knowledge scout-3 decision 3: maintained through the admin.
WRITABLE_TABLES = ('People', 'Leads', 'Questions', 'Ledger')


class DraftsAdminIsViewOnlyTest(unittest.TestCase):
    def setUp(self):
        result = run_probe(
            READ_ADMIN_PERMISSIONS, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.admins = json.loads(result.stdout)

    def test_drafts_admin_grants_no_add_change_or_delete(self):
        drafts = self.admins['Drafts']
        self.assertEqual(
            {action: drafts[action] for action in ('add', 'change', 'delete')},
            {'add': False, 'change': False, 'delete': False},
            'a drafts write through the admin bypasses the approval gate',
        )

    def test_drafts_changelist_offers_no_bulk_action(self):
        self.assertEqual(
            self.admins['Drafts']['actions'],
            [],
            'no bulk action may be offered over the approval queue (invariant #3)',
        )

    def test_drafts_stay_inspectable(self):
        drafts = self.admins['Drafts']
        self.assertTrue(drafts['view'], 'the drafts listing and detail page must still render')
        self.assertEqual(
            {key: drafts[key] for key in DRAFTS_LISTING},
            DRAFTS_LISTING,
        )

    def test_the_other_ops_tables_stay_writable(self):
        # Positive control: this is the half that fails if the stub request or
        # the registry lookup is wrong, so an empty-registry pass is impossible.
        self.assertEqual(
            {
                name: {action: self.admins[name][action] for action in ('add', 'change', 'delete')}
                for name in WRITABLE_TABLES
            },
            {name: {'add': True, 'change': True, 'delete': True} for name in WRITABLE_TABLES},
        )

    def test_people_admin_still_offers_the_bulk_delete(self):
        self.assertIn(
            'delete_selected',
            self.admins['People']['actions'],
            'the control table must still offer the action drafts refuses',
        )


if __name__ == '__main__':
    unittest.main()

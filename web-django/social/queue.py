"""The approval queue's guarded transitions.

This module is the Django mirror of the four POST routes in `web/server.mjs`
(`/api/approve`, `/api/unapprove`, `/api/edit`, `/api/skip`), which remain the
behavioural source of truth. Each function issues exactly ONE guarded UPDATE:
the status precondition lives in the `WHERE` clause, as it does in every Node
route, so the check and the write are a single statement. A row is never
loaded and then saved — that would read the status in one statement and write
in another, and nothing in the database enforces the transition (`drafts.status`
is a plain CHECK over the five legal values, with no trigger; see `schema.sql`).

Each function returns the row when the write landed, and `None` when the status
precondition refused it — the equivalent of `one()` returning null in
`web/server.mjs`, which the server answers with `NO_CHANGE` and HTTP 200.

Three details below are parity with the Node routes rather than style:

* `approved_at` is stamped DB-side with `Now()`, because `server.mjs` uses SQL
  `now()`. It is deliberately not `timezone.now()`. (On PostgreSQL Django
  renders `Now()` as `STATEMENT_TIMESTAMP()`, the statement's own clock: a
  DB-side stamp like `now()`, and never a timestamp computed in Python.)
* `skip` with no reason stores NULL, not `''` — `reason || null` in
  `server.mjs`.
* `edit` de-approves: rewriting the body returns the row to `waiting` and
  clears `approved_at`, so text A cannot be approved and text B sent. That
  invariant is pinned on the Node side by `test/web-approval-gate.test.mjs`.

One deviation from a literal reading of the Node routes, and the reason it is
parity rather than a departure: `edit` normalises the submitted body's line
endings to LF before storing it. `web/index.html` posts `textarea.value` — the
DOM getter, which is LF-only — while this app's «Правка» posts the textarea
through a real form submission, and the HTML form payload rule rewrites every
newline to CRLF. Storing the payload raw therefore rewrites a draft on a save
that edited nothing, and the stored convention everywhere else in the project
is LF (`bin/soc.mjs` writes a file body, `bin/import.mjs` copies the backup).
The publisher in `scout-ext` compares the stored body against a DOM value
character-for-character and against a length limit, so a carriage return is not
cosmetic there. `_lf` lives here, with the write, so every caller of
`queue.edit` gets the rule — the same reason `skip` resolves `reason or None`
here instead of in the view.
"""

from django.db.models.functions import Now

from .models import Drafts

# The gate's own answer to a write its status precondition refused, verbatim
# from `web/server.mjs`: a refusal is not an error, it is "nothing changed".
NO_CHANGE = 'ничего не изменилось (статус не тот)'

# The statuses an operator can still act on: `edit` and `skip` accept both,
# mirroring `status in ('waiting','approved')` in the two Node routes.
OPEN_STATUSES = ('waiting', 'approved')


def _row(draft_id):
    """The draft as the UPDATE left it, or None when it is gone."""
    return Drafts.objects.filter(pk=draft_id).first()


def _lf(body):
    """`body` with every line ending collapsed to a single LF.

    A browser submits a `<textarea>` with CRLF line endings, so the payload is
    not the text the operator was shown; a non-browser caller of the same route
    (curl, a script) can post a lone CR instead, which is collapsed too, so
    "the stored body carries no carriage return" is one assertion rather than
    two.

    Deliberately NOT `.strip()`, even though `bin/soc.mjs` trims a file body:
    an empty body is a meaningful state on the publisher side — an invitation
    *without* a note — so turning a whitespace-only edit into an empty one
    would change what the save means.
    """
    return body.replace('\r\n', '\n').replace('\r', '\n')


def approve(draft_id):
    """`POST /api/approve`: `waiting` -> `approved`, stamping `approved_at`.

    Applies strictly to this one draft; no other row is touched.
    """
    changed = Drafts.objects.filter(pk=draft_id, status='waiting').update(
        status='approved', approved_at=Now()
    )
    return _row(draft_id) if changed else None


def unapprove(draft_id):
    """`POST /api/unapprove`: `approved` -> `waiting`, clearing `approved_at`."""
    changed = Drafts.objects.filter(pk=draft_id, status='approved').update(
        status='waiting', approved_at=None
    )
    return _row(draft_id) if changed else None


def edit(draft_id, body):
    """`POST /api/edit`: rewrite the body and return the row to `waiting`.

    The approval does not survive the edit: `status` goes back to `waiting` and
    `approved_at` is cleared, so the approver always approves the text that
    will actually be sent.

    The body is stored with LF line endings whatever the caller submitted; see
    `_lf` for why the form payload is not the text to store.
    """
    changed = Drafts.objects.filter(pk=draft_id, status__in=OPEN_STATUSES).update(
        body=_lf(body), status='waiting', approved_at=None
    )
    return _row(draft_id) if changed else None


def skip(draft_id, reason):
    """`POST /api/skip`: hide the draft, storing the reason or NULL."""
    changed = Drafts.objects.filter(pk=draft_id, status__in=OPEN_STATUSES).update(
        status='skipped', skip_reason=reason or None
    )
    return _row(draft_id) if changed else None

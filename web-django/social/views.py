"""The approval queue page and its four write routes.

This is the Django counterpart of `web/index.html` plus the four POST routes of
`web/server.mjs`: `GET /queue/` lists every `waiting` and `approved` draft, and
each write route calls the matching guarded transition in `queue.py` and
redirects back to the page.

Two decisions worth knowing, both reversible in one line:

* **Server-rendered forms with a redirect**, not a JSON API consumed by
  JavaScript: `web/index.html` is a JS client against `/api/*`, and the v2
  conventions (knowledge scout-3) rule out JS frameworks. The consequence is
  that «Правка» posts a textarea and re-renders the page instead of editing in
  place.
* **The queue is staff-only.** `web/server.mjs` has no auth at all — its
  loopback binding is its whole boundary — but the other write surface in this
  app (the admin) is already login-gated, and shipping the drafts write path
  less protected than the view-only admin would be odd. Drop
  `@staff_member_required` to get the Node app's zero-friction behaviour.

`require_POST` sits OUTSIDE `staff_member_required` on the write routes, so a
GET is 405 for every caller rather than 405 for staff and a login redirect for
everyone else. The auth gate still refuses the write itself: an anonymous POST
is redirected to the admin login and reaches no transition.

A refused transition must be visible: `queue.NO_CHANGE` — `server.mjs`'s own
sentence — goes into `django.contrib.messages`, and `queue.html` renders the
messages block. A silent refusal would leave the operator pressing a button
that does nothing.
"""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import queue
from .models import Drafts


def _draft_id(request):
    """The posted draft id as an int, or None when it is missing or not a number."""
    try:
        return int(request.POST.get('id', ''))
    except (TypeError, ValueError):
        return None


def _back_to_queue(request, row):
    """Redirect to the queue, telling the operator when the write was refused."""
    if row is None:
        messages.error(request, queue.NO_CHANGE)
    return redirect('queue')


def _apply(request, transition, *args):
    draft_id = _draft_id(request)
    # An unusable id can change nothing, so it reads as a refusal rather than
    # as a server error.
    row = None if draft_id is None else transition(draft_id, *args)
    return _back_to_queue(request, row)


@staff_member_required
def queue_page(request):
    """The approval queue: every `waiting` and `approved` draft, ordered by id.

    Mirrors the `waiting` pane of `web/index.html`, which is fed by the same
    `status in ('waiting','approved') order by d.id` query in `server.mjs`.
    """
    drafts = list(Drafts.objects.filter(status__in=queue.OPEN_STATUSES).order_by('id'))
    return render(
        request,
        'social/queue.html',
        {
            'drafts': drafts,
            'waiting_count': sum(1 for draft in drafts if draft.status == 'waiting'),
            'approved_count': sum(1 for draft in drafts if draft.status == 'approved'),
        },
    )


@require_POST
@staff_member_required
def approve(request):
    return _apply(request, queue.approve)


@require_POST
@staff_member_required
def unapprove(request):
    return _apply(request, queue.unapprove)


@require_POST
@staff_member_required
def edit(request):
    return _apply(request, queue.edit, request.POST.get('body', ''))


@require_POST
@staff_member_required
def skip(request):
    return _apply(request, queue.skip, request.POST.get('reason', ''))

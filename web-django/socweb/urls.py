"""URL configuration for socweb.

Two surfaces: the Django admin (the ops surface for people, leads, questions
and the ledger) and the approval queue at `/queue/`, which owns every write to
`drafts`. The remaining `/api/state` panes of `web/index.html` (sent, ledger,
questions, people, leads, inbound) are covered by the admin's changelists; the
Node app (`npm run web`, port 4177) stays Roman's UI until the
acceptance-parity slice.

`/` is the front door, the way `web/server.mjs` answers `GET /` with the whole
screen. Here it redirects to the queue rather than rendering it, so the queue
keeps one canonical URL: staff get `302 → /queue/ → 200`, and an anonymous
visitor gets `302 → /queue/ → 302 /admin/login/?next=/queue/`, so the front
door is also the login door and returns to the queue afterwards. The redirect
resolves `queue` by name and is deliberately unauthenticated — it touches no
model, so it widens nothing.

`/static/…` is served by this urlconf, not by `runserver`'s own machinery, so
the admin's CSS and JS work in the mode the project pins: `DEBUG` off.
`django.contrib.staticfiles.views.serve` refuses to serve anything with
`DEBUG` off unless it is asked to (`if not settings.DEBUG and not insecure:
raise Http404`), which is why `{'insecure': True}` is load-bearing — without
it the route is present, answers 404 for every asset, and logs nothing. The
prefix is derived from `STATIC_URL` exactly the way `staticfiles_urlpatterns()`
derives it, so the two can never disagree.

This is the same mechanism as `runserver --insecure`, written into the urlconf
instead: it needs no flag the operator can forget, and it holds under
`socweb/wsgi.py` and `socweb/asgi.py` too. What it exposes is one directory:
with `STATICFILES_DIRS == []` and no `STATIC_ROOT`, the finders resolve only
`AppDirectoriesFinder` over `django.contrib.admin` (the `social` app ships no
`static/`), so nothing of the repo, the `.env` or the operator's data is
reachable through it — and the surface binds 127.0.0.1 only (knowledge
scout-2, invariant 4). The view's own docstring calls it a development view;
that trade-off is accepted deliberately for a single-operator loopback tool.
"""

import re

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.views import serve as serve_static
from django.urls import path, re_path
from django.views.generic.base import RedirectView

from social import views

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='queue'), name='home'),
    path('admin/', admin.site.urls),
    path('queue/', views.queue_page, name='queue'),
    path('queue/approve/', views.approve, name='queue_approve'),
    path('queue/unapprove/', views.unapprove, name='queue_unapprove'),
    path('queue/edit/', views.edit, name='queue_edit'),
    path('queue/skip/', views.skip, name='queue_skip'),
    re_path(
        r'^%s(?P<path>.*)$' % re.escape(settings.STATIC_URL.lstrip('/')),
        serve_static,
        {'insecure': True},
    ),
]

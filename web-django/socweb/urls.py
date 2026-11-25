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
"""

from django.contrib import admin
from django.urls import path
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
]

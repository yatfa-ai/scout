"""URL configuration for socweb.

Two surfaces: the Django admin (the ops surface for people, leads, questions
and the ledger) and the approval queue at `/queue/`, which owns every write to
`drafts`. The remaining `/api/state` panes of `web/index.html` (sent, ledger,
questions, people, leads, inbound) are covered by the admin or are later
slices; the Node app (`npm run web`, port 4177) stays Roman's UI until the
acceptance-parity slice.
"""

from django.contrib import admin
from django.urls import path

from social import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('queue/', views.queue_page, name='queue'),
    path('queue/approve/', views.approve, name='queue_approve'),
    path('queue/unapprove/', views.unapprove, name='queue_unapprove'),
    path('queue/edit/', views.edit, name='queue_edit'),
    path('queue/skip/', views.skip, name='queue_skip'),
]

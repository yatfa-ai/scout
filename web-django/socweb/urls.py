"""URL configuration for socweb.

This slice ships the Django admin and nothing else: the approval-queue UI, the
read pages and the API are later slices of the rebuild. The Node app
(`npm run web`, port 4177) remains the primary UI until then.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path('admin/', admin.site.urls),
]

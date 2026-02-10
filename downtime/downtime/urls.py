from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    # Built-in login/logout/password views:
    path("accounts/", include("django.contrib.auth.urls")),

    # Your app:
    path("", include(("downtime_tracker.urls", "downtime_tracker"), namespace="downtime_tracker")),
]

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("downtime_tracker.urls", namespace="downtime_tracker")),
]


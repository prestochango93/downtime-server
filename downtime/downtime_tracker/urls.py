from django.urls import path
from . import views

app_name = "downtime_tracker"

urlpatterns = [
    # Home IS the plant dashboard
    path("", views.home, name="home"),

    path("dept/<slug:code>/", views.department_detail, name="department_detail"),

    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
    path("equipment/<int:pk>/status/", views.change_status, name="change_status"),

    path("dept/<slug:code>/export.csv", views.department_export_csv, name="department_export_csv"),
    path("equipment/<int:pk>/export.csv", views.equipment_export_csv, name="equipment_export_csv"),
]

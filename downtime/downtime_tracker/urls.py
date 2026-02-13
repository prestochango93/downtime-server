from django.urls import path
from . import views

app_name = "downtime_tracker"

urlpatterns = [
    path("", views.home, name="home"),

    # NEW: Plant Dashboard
    path("dashboard/", views.plant_dashboard, name="plant_dashboard"),

    path("dept/<slug:code>/", views.department_detail, name="department_detail"),
    path("dept/<slug:code>/export.csv", views.department_export_csv, name="department_export_csv"),

    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
    path("equipment/<int:pk>/export.csv", views.equipment_export_csv, name="equipment_export_csv"),
    path("equipment/<int:pk>/status/", views.change_status, name="change_status"),
]

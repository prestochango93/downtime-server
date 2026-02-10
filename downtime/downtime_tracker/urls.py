from django.urls import path
from . import views

app_name = "downtime_tracker"

urlpatterns = [
    path("", views.home, name="home"),
    path("dept/<slug:code>/", views.department_detail, name="department_detail"),
    path("equipment/<int:pk>/status/", views.change_status, name="change_status"),
    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
]

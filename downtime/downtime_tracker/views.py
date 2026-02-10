import json
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import make_aware

from .forms import EquipmentStatusForm
from .models import Department, Equipment, DowntimeEvent



@login_required
def home(request):
    departments = Department.objects.filter(is_active=True).order_by("name")
    return render(request, "downtime_tracker/home.html", {"departments": departments})


@login_required
def department_detail(request, code: str):
    department = get_object_or_404(Department, code=code, is_active=True)

    equipment_list = (
        Equipment.objects.filter(department=department, is_active=True)
        .order_by("asset_number")
    )

    # Year selection (defaults to current year)
    now_local = timezone.localtime(timezone.now())
    try:
        year = int(request.GET.get("year", now_local.year))
    except ValueError:
        year = now_local.year

    year_start = make_aware(datetime(year, 1, 1, 0, 0, 0))
    year_end = make_aware(datetime(year + 1, 1, 1, 0, 0, 0))

    # Events overlapping the year window
    events = (
        DowntimeEvent.objects.filter(equipment__department=department, equipment__is_active=True)
        .filter(started_at__lt=year_end)
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gt=year_start))
        .select_related("equipment")
    )

    totals_seconds = {eq.id: 0.0 for eq in equipment_list}
    now_ts = timezone.now()

    for ev in events:
        overlap_start = max(ev.started_at, year_start)
        overlap_end = min(ev.ended_at or now_ts, year_end)
        if overlap_end > overlap_start:
            totals_seconds[ev.equipment_id] += (overlap_end - overlap_start).total_seconds()

    # Build chart arrays (days)
    chart_labels = [eq.asset_number for eq in equipment_list]
    chart_values_days = [round(totals_seconds.get(eq.id, 0.0) / 86400.0, 3) for eq in equipment_list]

    return render(
        request,
        "downtime_tracker/department.html",
        {
            "department": department,
            "equipment_list": equipment_list,
            "year": year,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_values_json": json.dumps(chart_values_days),
        },
    )


@login_required
@permission_required("downtime_tracker.change_equipment", raise_exception=True)
def change_status(request, pk: int):
    equipment = get_object_or_404(Equipment, pk=pk, is_active=True)

    if request.method == "POST":
        form = EquipmentStatusForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data["new_status"]
            comment = form.cleaned_data["comment"]
            try:
                equipment.set_status(
                    new_status=new_status,
                    comment=comment,
                    user=request.user,
                    changed_at=timezone.now(),
                )
                messages.success(request, f"{equipment.asset_number} set to {new_status}.")
                return redirect("downtime_tracker:department_detail", code=equipment.department.code)
            except Exception as exc:
                messages.error(request, f"Could not change status: {exc}")
    else:
        form = EquipmentStatusForm(initial={"new_status": equipment.status})

    return render(
        request,
        "downtime_tracker/change_status.html",
        {"equipment": equipment, "form": form},
    )

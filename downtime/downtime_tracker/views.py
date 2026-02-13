import csv
import json
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import make_aware

from .forms import EquipmentStatusForm
from .models import Department, Equipment, DowntimeEvent


def _year_window_from_request(request):
    now_local = timezone.localtime(timezone.now())
    try:
        year = int(request.GET.get("year", now_local.year))
    except (TypeError, ValueError):
        year = now_local.year

    year_start = make_aware(datetime(year, 1, 1, 0, 0, 0))
    year_end = make_aware(datetime(year + 1, 1, 1, 0, 0, 0))
    return year, year_start, year_end


def _overlap_seconds(event, window_start, window_end, now_ts):
    overlap_start = max(event.started_at, window_start)
    overlap_end = min(event.ended_at or now_ts, window_end)
    if overlap_end > overlap_start:
        return (overlap_end - overlap_start).total_seconds()
    return 0.0


def _cat_from_request(request):
    """
    Returns (cat, cat_filter) where:
      - cat is the normalized display value: 'ALL' or one of the choice keys
      - cat_filter is None for ALL, otherwise the valid choice key to filter on
    """
    cat = (request.GET.get("cat") or "ALL").upper().strip()
    valid = {c for c, _ in DowntimeEvent.Category.choices}
    if cat in valid:
        return cat, cat
    return "ALL", None


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

    # Filters
    year, year_start, year_end = _year_window_from_request(request)
    cat, cat_filter = _cat_from_request(request)

    # Open downtime events (for "Down reason" column)
    open_events_qs = DowntimeEvent.objects.filter(
        equipment__department=department,
        equipment__is_active=True,
        ended_at__isnull=True,
    )
    if cat_filter:
        open_events_qs = open_events_qs.filter(category=cat_filter)
    open_events_qs = open_events_qs.select_related("equipment")

    open_event_by_equipment_id = {ev.equipment_id: ev for ev in open_events_qs}

    # Events overlapping the year window (for chart)
    events = DowntimeEvent.objects.filter(
        equipment__department=department,
        equipment__is_active=True,
        started_at__lt=year_end,
    ).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=year_start)
    )
    if cat_filter:
        events = events.filter(category=cat_filter)
    events = events.select_related("equipment")

    totals_seconds = {eq.id: 0.0 for eq in equipment_list}
    now_ts = timezone.now()

    for ev in events:
        totals_seconds[ev.equipment_id] += _overlap_seconds(ev, year_start, year_end, now_ts)

    chart_labels = [eq.asset_number for eq in equipment_list]
    chart_values_days = [round(totals_seconds.get(eq.id, 0.0) / 86400.0, 3) for eq in equipment_list]

    return render(
        request,
        "downtime_tracker/department.html",
        {
            "department": department,
            "equipment_list": equipment_list,
            "year": year,
            "cat": cat,
            "cat_choices": DowntimeEvent.Category.choices,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_values_json": json.dumps(chart_values_days),
            "open_event_by_equipment_id": open_event_by_equipment_id,
        },
    )


@login_required
def department_export_csv(request, code: str):
    department = get_object_or_404(Department, code=code, is_active=True)

    year, year_start, year_end = _year_window_from_request(request)
    cat, cat_filter = _cat_from_request(request)
    now_ts = timezone.now()

    equipment_list = (
        Equipment.objects.filter(department=department, is_active=True)
        .order_by("asset_number")
    )

    events = DowntimeEvent.objects.filter(
        equipment__department=department,
        equipment__is_active=True,
        started_at__lt=year_end,
    ).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=year_start)
    )
    if cat_filter:
        events = events.filter(category=cat_filter)

    events = events.select_related("equipment").order_by("equipment__asset_number", "-started_at")

    totals_seconds = {eq.id: 0.0 for eq in equipment_list}
    for ev in events:
        totals_seconds[ev.equipment_id] += _overlap_seconds(ev, year_start, year_end, now_ts)

    filename = f"{department.code}_downtime_{year}_{cat}.csv"
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow([
        "Department",
        "Year",
        "Category Filter",
        "Asset Number",
        "Equipment Description",
        "Event Start",
        "Event End",
        "Duration (days)",
        "Type",
        "Start Comment",
        "End Comment",
        "Total Downtime This Year (days) for Equipment",
    ])

    total_days_by_eq = {eq_id: round(sec / 86400.0, 3) for eq_id, sec in totals_seconds.items()}

    events_by_eq = {}
    for ev in events:
        events_by_eq.setdefault(ev.equipment_id, []).append(ev)

    for eq in equipment_list:
        eq_events = events_by_eq.get(eq.id, [])
        eq_total_days = total_days_by_eq.get(eq.id, 0.0)

        if not eq_events:
            writer.writerow([
                department.name,
                year,
                cat,
                eq.asset_number,
                eq.description,
                "",
                "",
                "",
                "",
                "",
                "",
                eq_total_days,
            ])
            continue

        for ev in eq_events:
            dur_days = round(_overlap_seconds(ev, year_start, year_end, now_ts) / 86400.0, 3)
            writer.writerow([
                department.name,
                year,
                cat,
                eq.asset_number,
                eq.description,
                ev.started_at,
                ev.ended_at or "",
                dur_days,
                ev.get_category_display(),
                ev.start_comment,
                ev.end_comment,
                eq_total_days,
            ])

    return resp


@login_required
def equipment_export_csv(request, pk: int):
    equipment = get_object_or_404(
        Equipment.objects.select_related("department"),
        pk=pk,
        is_active=True,
    )

    year, year_start, year_end = _year_window_from_request(request)
    cat, cat_filter = _cat_from_request(request)
    now_ts = timezone.now()

    events = DowntimeEvent.objects.filter(
        equipment=equipment,
        started_at__lt=year_end,
    ).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=year_start)
    )
    if cat_filter:
        events = events.filter(category=cat_filter)

    events = events.order_by("-started_at")

    total_seconds = 0.0
    for ev in events:
        total_seconds += _overlap_seconds(ev, year_start, year_end, now_ts)
    total_days = round(total_seconds / 86400.0, 3)

    filename = f"{equipment.asset_number}_downtime_{year}_{cat}.csv"
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow(["Department", equipment.department.name])
    writer.writerow(["Asset Number", equipment.asset_number])
    writer.writerow(["Description", equipment.description])
    writer.writerow(["Year", year])
    writer.writerow(["Category Filter", cat])
    writer.writerow(["Total Downtime (days)", total_days])
    writer.writerow([])

    writer.writerow(["Event Start", "Event End", "Duration (days)", "Type", "Start Comment", "End Comment"])

    for ev in events:
        dur_days = round(_overlap_seconds(ev, year_start, year_end, now_ts) / 86400.0, 3)
        writer.writerow([
            ev.started_at,
            ev.ended_at or "",
            dur_days,
            ev.get_category_display(),
            ev.start_comment,
            ev.end_comment,
        ])

    return resp


@login_required
def equipment_detail(request, pk: int):
    equipment = get_object_or_404(
        Equipment.objects.select_related("department"),
        pk=pk,
        is_active=True
    )

    year, year_start, year_end = _year_window_from_request(request)

    # NEW: category filter (defaults to ALL)
    cat = (request.GET.get("cat") or "ALL").upper().strip()
    cat_choices = [("PLANNED", "Calibration / Preventive Maintenance"), ("UNPLANNED", "Unplanned")]

    # base queryset
    events_all_qs = DowntimeEvent.objects.filter(equipment=equipment).order_by("-started_at")

    # apply filter to the table if not ALL
    if cat != "ALL":
        valid = {v for v, _ in DowntimeEvent.Category.choices}
        if cat in valid:
            events_all_qs = events_all_qs.filter(category=cat)

    events_all = events_all_qs

    open_event = (
        DowntimeEvent.objects.filter(equipment=equipment, ended_at__isnull=True)
        .order_by("-started_at")
        .first()
    )

    # events overlapping year window (respect cat filter for totals)
    events_year_qs = (
        DowntimeEvent.objects.filter(equipment=equipment)
        .filter(started_at__lt=year_end)
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gt=year_start))
    )

    if cat != "ALL":
        valid = {v for v, _ in DowntimeEvent.Category.choices}
        if cat in valid:
            events_year_qs = events_year_qs.filter(category=cat)

    events_year = events_year_qs.order_by("-started_at")

    now_ts = timezone.now()

    # total downtime (for current filter)
    total_seconds_year = 0.0
    for ev in events_year:
        total_seconds_year += _overlap_seconds(ev, year_start, year_end, now_ts)
    total_days_year = round(total_seconds_year / 86400.0, 3)

    # NEW: category split chart ONLY when cat == ALL
    chart_labels_json = "null"
    chart_values_json = "null"
    if cat == "ALL":
        events_year_all = (
            DowntimeEvent.objects.filter(equipment=equipment)
            .filter(started_at__lt=year_end)
            .filter(Q(ended_at__isnull=True) | Q(ended_at__gt=year_start))
        )

        seconds_by_cat = {
            DowntimeEvent.Category.PLANNED: 0.0,
            DowntimeEvent.Category.UNPLANNED: 0.0,
        }

        for ev in events_year_all:
            seconds_by_cat[ev.category] = seconds_by_cat.get(ev.category, 0.0) + _overlap_seconds(
                ev, year_start, year_end, now_ts
            )

        labels = [
            DowntimeEvent.Category.PLANNED.label,
            DowntimeEvent.Category.UNPLANNED.label,
        ]
        values = [
            round(seconds_by_cat.get(DowntimeEvent.Category.PLANNED, 0.0) / 86400.0, 3),
            round(seconds_by_cat.get(DowntimeEvent.Category.UNPLANNED, 0.0) / 86400.0, 3),
        ]

        chart_labels_json = json.dumps(labels)
        chart_values_json = json.dumps(values)

    now_local = timezone.localtime(timezone.now())
    years = list(range(now_local.year - 2, now_local.year + 2 + 1))

    return render(
        request,
        "downtime_tracker/equipment_detail.html",
        {
            "equipment": equipment,
            "open_event": open_event,
            "events_all": events_all,
            "year": year,
            "years": years,
            "total_days_year": total_days_year,
            "cat": cat,
            "cat_choices": cat_choices,  # or DowntimeEvent.Category.choices if you prefer
            "chart_labels_json": chart_labels_json,
            "chart_values_json": chart_values_json,
        },
    )


@login_required
@permission_required("downtime_tracker.change_equipment", raise_exception=True)
def change_status(request, pk: int):
    equipment = get_object_or_404(Equipment, pk=pk, is_active=True)

    # Where to return after change (defaults to the equipment's department page)
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = EquipmentStatusForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data["new_status"]
            comment = form.cleaned_data["comment"]
            downtime_category = form.cleaned_data.get("downtime_category")

            try:
                equipment.set_status(
                    new_status=new_status,
                    comment=comment,
                    user=request.user,
                    changed_at=timezone.now(),
                    downtime_category=downtime_category,
                )
                messages.success(request, f"{equipment.asset_number} set to {new_status}.")

                if next_url:
                    return redirect(next_url)

                return redirect("downtime_tracker:department_detail", code=equipment.department.code)

            except Exception as exc:
                messages.error(request, f"Could not change status: {exc}")
    else:
        form = EquipmentStatusForm(initial={"new_status": equipment.status})

    return render(
        request,
        "downtime_tracker/change_status.html",
        {"equipment": equipment, "form": form, "next": next_url},
    )

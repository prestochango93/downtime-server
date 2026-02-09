from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import EquipmentStatusForm
from .models import Department, Equipment


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
    return render(
        request,
        "downtime_tracker/department.html",
        {"department": department, "equipment_list": equipment_list},
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

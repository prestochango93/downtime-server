from __future__ import annotations
from django.contrib import admin, messages
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Department, Equipment, StatusChangeLog, DowntimeEvent


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "description")
    prepopulated_fields = {"code": ("name",)}
    ordering = ("name",)


class DowntimeEventInline(admin.TabularInline):
    model = DowntimeEvent
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("-started_at",)
    fields = ("started_at", "ended_at", "duration_display", "start_comment", "end_comment", "created_by", "closed_by")
    readonly_fields = ("duration_display", "created_by", "closed_by")

    def duration_display(self, obj: DowntimeEvent) -> str:
        # Avoid formatting surprises; this works even for open events.
        seconds = int(obj.duration.total_seconds())
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        return f"{days}d {hours:02d}h {minutes:02d}m"

    duration_display.short_description = "Duration"


class StatusChangeLogInline(admin.TabularInline):
    model = StatusChangeLog
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("-changed_at",)
    fields = ("changed_at", "changed_by", "from_status", "to_status", "comment")
    readonly_fields = ("changed_at", "changed_by", "from_status", "to_status", "comment")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        "asset_number",
        "description",
        "department",
        "status_badge",
        "status_updated_at",
        "open_event_started_at",
    )
    list_filter = ("department", "status", "is_active")
    search_fields = ("asset_number", "description", "location", "department__name", "department__code")
    ordering = ("department__name", "asset_number")
    list_select_related = ("department",)
    inlines = (DowntimeEventInline, StatusChangeLogInline)

    fieldsets = (
        ("Identity", {"fields": ("department", "asset_number", "description", "location", "is_active")}),
        ("Current Status", {"fields": ("status", "status_updated_at")}),
    )

    readonly_fields = ("status_updated_at",)

    actions = ("mark_up", "mark_down")

    @admin.display(description="Status")
    def status_badge(self, obj: Equipment) -> str:
        # Admin list display is plain text; you can add HTML later if desired.
        return "DOWN" if obj.status == Equipment.Status.DOWN else "UP"

    @admin.display(description="Open event since")
    def open_event_started_at(self, obj: Equipment):
        open_evt = obj.downtime_events.filter(ended_at__isnull=True).order_by("-started_at").first()
        return open_evt.started_at if open_evt else None

    def save_model(self, request, obj: Equipment, form, change):
        """
        Prevent silent status changes in admin without the required comment.
        If status is changed via admin edit form, require using the action instead.
        """
        if change:
            old = Equipment.objects.get(pk=obj.pk)
            if old.status != obj.status:
                # Revert the status change and instruct to use actions (which enforce comments).
                obj.status = old.status
                messages.error(
                    request,
                    "Status changes must be done using the admin actions (Mark UP / Mark DOWN) so a comment is captured.",
                )
        super().save_model(request, obj, form, change)

    def _bulk_change_status(self, request, queryset, new_status: str):
        """
        Admin actions for bulk status changes.
        We require a comment, but Django admin actions don't provide a built-in prompt.
        Production approach: block bulk status changes and force single-equipment change,
        OR implement a custom admin action form.
        For safety and auditability, we block bulk and allow only one at a time.
        """
        count = queryset.count()
        if count != 1:
            messages.error(request, "For auditability, select exactly ONE equipment item for status changes.")
            return

        eq: Equipment = queryset.first()
        # Minimal comment capture: use a timestamped default and instruct user to use the main UI later.
        # If you prefer strict: raise and block here until we add custom form.
        comment = f"Admin action by {request.user.username} at {timezone.now():%Y-%m-%d %H:%M} (temporary default comment)"
        try:
            eq.set_status(new_status=new_status, comment=comment, user=request.user)
            messages.success(request, f"{eq.asset_number} set to {new_status}.")
        except Exception as exc:
            messages.error(request, f"Failed to change status: {exc}")

    @admin.action(description="Mark selected equipment UP (close downtime)")
    def mark_up(self, request, queryset):
        self._bulk_change_status(request, queryset, Equipment.Status.UP)

    @admin.action(description="Mark selected equipment DOWN (start downtime)")
    def mark_down(self, request, queryset):
        self._bulk_change_status(request, queryset, Equipment.Status.DOWN)


@admin.register(DowntimeEvent)
class DowntimeEventAdmin(admin.ModelAdmin):
    list_display = (
        "equipment",
        "started_at",
        "ended_at",
        "is_open",
        "duration_display",
        "created_by",
        "closed_by",
    )
    list_filter = ("equipment__department", "ended_at")
    search_fields = ("equipment__asset_number", "equipment__description", "start_comment", "end_comment")
    ordering = ("-started_at",)
    list_select_related = ("equipment", "equipment__department", "created_by", "closed_by")

    readonly_fields = (
        "created_at",
        "updated_at",
        "duration_display",
        "created_by",
        "closed_by",
        "started_by_log",
        "ended_by_log",
    )

    fieldsets = (
        ("Equipment", {"fields": ("equipment",)}),
        ("Timing", {"fields": ("started_at", "ended_at", "duration_display")}),
        ("Comments", {"fields": ("start_comment", "end_comment")}),
        ("Audit", {"fields": ("created_by", "closed_by", "started_by_log", "ended_by_log", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Open?")
    def is_open(self, obj: DowntimeEvent) -> bool:
        return obj.ended_at is None

    @admin.display(description="Duration")
    def duration_display(self, obj: DowntimeEvent) -> str:
        seconds = int(obj.duration.total_seconds())
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        return f"{days}d {hours:02d}h {minutes:02d}m"


@admin.register(StatusChangeLog)
class StatusChangeLogAdmin(admin.ModelAdmin):
    list_display = ("equipment", "changed_at", "changed_by", "from_status", "to_status", "short_comment")
    list_filter = ("equipment__department", "to_status", "from_status")
    search_fields = ("equipment__asset_number", "equipment__description", "comment", "changed_by__username")
    ordering = ("-changed_at",)
    list_select_related = ("equipment", "equipment__department", "changed_by")

    readonly_fields = ("equipment", "changed_by", "from_status", "to_status", "comment", "changed_at")

    fieldsets = (
        ("Event", {"fields": ("equipment", "changed_at", "changed_by")}),
        ("Change", {"fields": ("from_status", "to_status")}),
        ("Comment", {"fields": ("comment",)}),
    )

    @admin.display(description="Comment")
    def short_comment(self, obj: StatusChangeLog) -> str:
        return (obj.comment[:60] + "…") if len(obj.comment) > 60 else obj.comment

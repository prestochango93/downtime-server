from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, F
from django.utils import timezone


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.SlugField(
        max_length=40,
        unique=True,
        help_text="Short code for URLs (e.g., QC, PUR, UTIL).",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional description of the department scope or function.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Equipment(models.Model):
    class Status(models.TextChoices):
        UP = "UP", "Up"
        DOWN = "DOWN", "Down"

    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="equipment")
    asset_number = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    status = models.CharField(max_length=8, choices=Status.choices, default=Status.UP)
    status_updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["department__name", "asset_number"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["asset_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset_number} — {self.description}"

    @property
    def has_open_downtime(self) -> bool:
        return self.downtime_events.filter(ended_at__isnull=True).exists()

    @transaction.atomic
    def set_status(
        self,
        *,
        new_status: str,
        comment: str,
        user=None,
        changed_at=None,
    ) -> "StatusChangeLog":
        """
        Rules:
        - Comment REQUIRED on any status change.
        - DOWN starts a downtime event.
        - UP ends the currently open downtime event.
        - Prevents duplicate open events per equipment.
        """
        new_status = str(new_status).upper().strip()
        if new_status not in (self.Status.UP, self.Status.DOWN):
            raise ValidationError(f"Invalid status: {new_status}")

        comment = (comment or "").strip()
        if not comment:
            raise ValidationError("A comment is required when changing status.")

        changed_at = changed_at or timezone.now()
        old_status = self.status

        if new_status == old_status:
            raise ValidationError(f"Equipment is already {new_status}.")

        log = StatusChangeLog.objects.create(
            equipment=self,
            changed_by=user if user and getattr(user, "is_authenticated", False) else None,
            from_status=old_status,
            to_status=new_status,
            comment=comment,
            changed_at=changed_at,
        )

        if new_status == self.Status.DOWN:
            if self.downtime_events.filter(ended_at__isnull=True).exists():
                raise ValidationError("This equipment already has an open downtime event.")
            DowntimeEvent.objects.create(
                equipment=self,
                started_at=changed_at,
                start_comment=comment,
                created_by=log.changed_by,
                started_by_log=log,
            )

        elif new_status == self.Status.UP:
            open_evt = (
                self.downtime_events.select_for_update()
                .filter(ended_at__isnull=True)
                .order_by("-started_at")
                .first()
            )
            if not open_evt:
                raise ValidationError("Cannot set UP because there is no open downtime event to close.")

            open_evt.ended_at = changed_at
            open_evt.end_comment = comment
            open_evt.closed_by = log.changed_by
            open_evt.ended_by_log = log
            open_evt.full_clean()
            open_evt.save(
                update_fields=["ended_at", "end_comment", "closed_by", "ended_by_log", "updated_at"]
            )

        self.status = new_status
        self.status_updated_at = changed_at
        self.save(update_fields=["status", "status_updated_at"])

        return log


class StatusChangeLog(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="status_logs")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="equipment_status_changes",
    )
    from_status = models.CharField(max_length=8)
    to_status = models.CharField(max_length=8)
    comment = models.TextField()
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["equipment", "-changed_at"]),
            models.Index(fields=["-changed_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.equipment.asset_number}: {self.from_status} → {self.to_status} "
            f"@ {self.changed_at:%Y-%m-%d %H:%M}"
        )


class DowntimeEvent(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="downtime_events")

    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    start_comment = models.TextField()
    end_comment = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downtime_started",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downtime_closed",
    )

    started_by_log = models.OneToOneField(
        StatusChangeLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="started_downtime_event",
    )
    ended_by_log = models.OneToOneField(
        StatusChangeLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ended_downtime_event",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["equipment", "-started_at"]),
            models.Index(fields=["ended_at"]),
        ]
        

    def __str__(self) -> str:
        if self.ended_at:
            return (
                f"{self.equipment.asset_number} downtime "
                f"{self.started_at:%Y-%m-%d %H:%M} → {self.ended_at:%Y-%m-%d %H:%M}"
            )
        return f"{self.equipment.asset_number} downtime OPEN since {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration(self) -> timedelta:
        end = self.ended_at or timezone.now()
        return end - self.started_at

    @property
    def duration_days(self) -> float:
        return self.duration.total_seconds() / 86400.0

    def clean(self):
        super().clean()
        if self.ended_at and self.ended_at <= self.started_at:
            raise ValidationError("ended_at must be later than started_at.")

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


# =========================================================
# Department
# =========================================================

class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.SlugField(max_length=40, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# =========================================================
# Equipment
# =========================================================

class Equipment(models.Model):

    class Status(models.TextChoices):
        UP = "UP", "Up"
        DOWN = "DOWN", "Down"

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="equipment"
    )

    asset_number = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.UP
    )

    status_updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["department__name", "asset_number"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["asset_number"]),
        ]

    def __str__(self):
        return f"{self.asset_number} — {self.description}"

    @property
    def has_open_downtime(self):
        return self.downtime_events.filter(ended_at__isnull=True).exists()


    # =====================================================
    # STATUS CHANGE LOGIC
    # =====================================================

    @transaction.atomic
    def set_status(
        self,
        *,
        new_status: str,
        comment: str,
        user=None,
        changed_at=None,
        downtime_category=None,  # <-- NEW
    ):
        """
        DOWN  -> starts downtime (category required)
        UP    -> closes downtime
        """

        new_status = str(new_status).upper().strip()

        if new_status not in (self.Status.UP, self.Status.DOWN):
            raise ValidationError(f"Invalid status: {new_status}")

        comment = (comment or "").strip()
        if not comment:
            raise ValidationError("A comment is required.")

        changed_at = changed_at or timezone.now()
        old_status = self.status

        if new_status == old_status:
            raise ValidationError(f"Equipment already {new_status}")

        log = StatusChangeLog.objects.create(
            equipment=self,
            changed_by=user if user and getattr(user, "is_authenticated", False) else None,
            from_status=old_status,
            to_status=new_status,
            comment=comment,
            changed_at=changed_at,
        )

        # -------------------------
        # GOING DOWN
        # -------------------------
        if new_status == self.Status.DOWN:

            if self.has_open_downtime:
                raise ValidationError("Equipment already has open downtime.")

            if not downtime_category:
                raise ValidationError("Downtime category is required.")

            DowntimeEvent.objects.create(
                equipment=self,
                started_at=changed_at,
                start_comment=comment,
                category=downtime_category,
                created_by=log.changed_by,
                started_by_log=log,
            )

        # -------------------------
        # GOING UP
        # -------------------------
        elif new_status == self.Status.UP:

            open_evt = (
                self.downtime_events
                .select_for_update()
                .filter(ended_at__isnull=True)
                .first()
            )

            if not open_evt:
                raise ValidationError("No open downtime event to close.")

            open_evt.ended_at = changed_at
            open_evt.end_comment = comment
            open_evt.closed_by = log.changed_by
            open_evt.ended_by_log = log
            open_evt.save()

        self.status = new_status
        self.status_updated_at = changed_at
        self.save(update_fields=["status", "status_updated_at"])

        return log


# =========================================================
# StatusChangeLog
# =========================================================

class StatusChangeLog(models.Model):
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name="status_logs"
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    from_status = models.CharField(max_length=8)
    to_status = models.CharField(max_length=8)
    comment = models.TextField()
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.equipment.asset_number}: {self.from_status} → {self.to_status}"


# =========================================================
# DowntimeEvent
# =========================================================

class DowntimeEvent(models.Model):

    class Category(models.TextChoices):
        PLANNED = "PLANNED", "Calibration / Preventive Maintenance"
        UNPLANNED = "UNPLANNED", "Unplanned"

    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name="downtime_events"
    )

    category = models.CharField(
        max_length=12,
        choices=Category.choices,
        default=Category.UNPLANNED,
        db_index=True,
    )

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

    # -----------------------------------------------------

    @property
    def is_open(self):
        return self.ended_at is None

    @property
    def duration(self) -> timedelta:
        if not self.started_at:
            return timedelta(0)

        end = self.ended_at or timezone.now()
        return end - self.started_at

    @property
    def duration_days(self):
        return self.duration.total_seconds() / 86400

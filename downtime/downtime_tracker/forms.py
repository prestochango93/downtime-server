from django import forms
from .models import Equipment, DowntimeEvent


class EquipmentStatusForm(forms.Form):
    new_status = forms.ChoiceField(
        choices=Equipment.Status.choices,
        label="New status",
    )

    downtime_category = forms.ChoiceField(
        choices=DowntimeEvent.Category.choices,
        label="Downtime reason",
        required=False,  # we enforce requirement only when DOWN in clean()
    )

    comment = forms.CharField(
        label="Comment (required)",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Why is the equipment changing status?"}),
        min_length=3,
        required=True,
    )

    def clean(self):
        cleaned = super().clean()
        new_status = cleaned.get("new_status")
        cat = cleaned.get("downtime_category")

        if new_status == Equipment.Status.DOWN:
            if not cat:
                self.add_error(
                    "downtime_category",
                    "Select either Calibration / Preventive Maintenance or Unplanned.",
                )
        else:
            # When setting UP, ignore any category value
            cleaned["downtime_category"] = None

        return cleaned

from django import forms
from .models import Equipment


class EquipmentStatusForm(forms.Form):
    new_status = forms.ChoiceField(
        choices=Equipment.Status.choices,
        label="New status",
    )
    comment = forms.CharField(
        label="Comment (required)",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Why is the equipment changing status?"}),
        min_length=3,
        required=True,
    )

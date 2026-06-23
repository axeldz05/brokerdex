from decimal import Decimal
from django import forms
from .models import Order
from creature.models import Creature


class MarketOrderForm(forms.Form):
    """Form for placing market (instant) buy/sell orders."""

    creature = forms.UUIDField(widget=forms.HiddenInput())
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=8,
        min_value=Decimal('0.00000001'),
        label='Quantity',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0.00',
            'step': '0.01',
            'min': '0.01',
        })
    )
    order_type = forms.ChoiceField(
        choices=Order.OrderType.choices,
        widget=forms.HiddenInput(),
    )

    def clean_creature(self):
        creature_id = self.cleaned_data['creature']
        try:
            return Creature.objects.get(pk=creature_id)
        except Creature.DoesNotExist:
            raise forms.ValidationError("Creature not found.")


class LimitOrderForm(forms.Form):
    """Form for placing limit orders with a specific price threshold."""

    creature = forms.UUIDField(widget=forms.HiddenInput())
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=8,
        min_value=Decimal('0.00000001'),
        label='Quantity',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0.00',
            'step': '0.01',
            'min': '0.01',
        })
    )
    limit_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0.01'),
        label='Limit Price',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0.00',
            'step': '0.01',
            'min': '0.01',
        })
    )
    order_type = forms.ChoiceField(
        choices=Order.OrderType.choices,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )

    def clean_creature(self):
        creature_id = self.cleaned_data['creature']
        try:
            return Creature.objects.get(pk=creature_id)
        except Creature.DoesNotExist:
            raise forms.ValidationError("Creature not found.")

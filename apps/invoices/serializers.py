"""Invoice serializers with the line-item breakdown (doc 04 §10)."""

from rest_framework import serializers

from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    """Read shape for both the resident and treasurer views."""

    unit_number = serializers.CharField(source='unit.full_unit_id', read_only=True)
    occupant_name = serializers.SerializerMethodField()
    outstanding_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    line_items = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'billing_period', 'unit', 'unit_number', 'occupant_name',
            'base_amount', 'water_charge', 'sinking_fund', 'amenity_charges',
            'non_occupancy_surcharge', 'late_fee', 'gst_amount', 'total_amount',
            'line_items', 'status', 'due_date', 'paid_date', 'paid_amount',
            'outstanding_amount', 'payment_method', 'utr_number', 'payments', 'generated_at',
        ]
        read_only_fields = fields

    def get_occupant_name(self, obj):
        unit = obj.unit
        if unit.is_rented and unit.tenant_name:
            return unit.tenant_name
        return unit.owner_name

    def get_line_items(self, obj):
        labels = [
            ('Base maintenance', obj.base_amount),
            ('Water charge', obj.water_charge),
            ('Sinking fund', obj.sinking_fund),
            ('Amenity charges', obj.amenity_charges),
            ('Non-occupancy surcharge', obj.non_occupancy_surcharge),
            ('Late fee', obj.late_fee),
            ('GST', obj.gst_amount),
        ]
        return [{'label': label, 'amount': str(amount)} for label, amount in labels if amount]

    def get_payments(self, obj):
        return [
            {
                'id': p.id,
                'amount': str(p.amount),
                'method': p.method,
                'status': p.status,
                'utr_number': p.utr_number,
                'created_at': p.created_at,
            }
            for p in obj.payments.all()[:10]
        ]


class InvoiceGenerateSerializer(serializers.Serializer):
    """Optional period override for the manual generate action (§10.3)."""

    billing_period = serializers.RegexField(r'^\d{4}-(0[1-9]|1[0-2])$', required=False)

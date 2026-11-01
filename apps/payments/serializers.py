"""Payment serializers for UPI intents and manual receipts (doc 04 §9)."""

import re

from rest_framework import serializers

from .models import PaymentRecord

# UPI transaction references are 12 characters in the spec; banks sometimes
# issue longer ones, so 12–16 is accepted rather than forcing a rejection that
# would strand a genuine payment.
UTR_RE = re.compile(r'^[0-9A-Za-z]{12,16}$')

# Gap #20 (doc 02 §12): the edge caps uploads at 15 MB but the loopback port
# has no cap, so the limit must travel with the request. Deliberately a byte
# cap only — phone screenshots legitimately exceed 2048 px in one dimension,
# so a pixel rejection would turn away the exact proofs MVR exists for.
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


class PaymentRecordSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True)
    unit_number = serializers.CharField(source='invoice.unit.full_unit_id', read_only=True)
    billing_period = serializers.CharField(source='invoice.billing_period', read_only=True)
    amount_paid = serializers.DecimalField(source='amount', max_digits=10, decimal_places=2, read_only=True)
    submitted_by = serializers.SerializerMethodField()
    submitted_at = serializers.DateTimeField(source='created_at', read_only=True)
    receipt_image = serializers.FileField(use_url=True, required=False, allow_null=True)

    class Meta:
        model = PaymentRecord
        fields = [
            'id', 'invoice', 'invoice_number', 'unit_number', 'billing_period',
            'amount', 'amount_paid', 'method', 'status', 'utr_number', 'payment_app',
            'receipt_image', 'submitted_by', 'verified_by', 'verified_at',
            'verification_note', 'created_at', 'submitted_at',
        ]
        read_only_fields = [
            'id', 'invoice_number', 'unit_number', 'billing_period', 'method', 'status',
            'submitted_by', 'verified_by', 'verified_at', 'created_at', 'submitted_at',
        ]

    def get_submitted_by(self, obj):
        if not obj.user_id:
            return None
        return {'id': obj.user_id, 'name': obj.user.get_full_name() or obj.user.username}


class UpiLinkRequestSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    payment_app = serializers.CharField(required=False, allow_blank=True, max_length=50)


class ManualPaymentSerializer(serializers.Serializer):
    """Manual proof-of-payment submission (MVR-001…005)."""

    invoice_id = serializers.IntegerField()
    utr_number = serializers.CharField(required=False, max_length=16)
    receipt_image = serializers.ImageField(required=False)
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    payment_app = serializers.CharField(required=False, allow_blank=True, max_length=50)

    def validate_utr_number(self, value):
        value = (value or '').strip()
        if not value:
            return None
        if not UTR_RE.match(value):
            raise serializers.ValidationError('UTR must be 12–16 letters/digits from the UPI receipt')
        return value

    def validate_receipt_image(self, value):
        if value is not None and value.size > MAX_RECEIPT_BYTES:
            raise serializers.ValidationError(
                'Screenshot is over 5 MB — share a smaller copy from your UPI app.'
            )
        return value

    def validate(self, attrs):
        if not attrs.get('utr_number') and not attrs.get('receipt_image'):
            raise serializers.ValidationError(
                {'utr_number': 'Provide the UTR number or upload a receipt screenshot'}
            )
        return attrs


class VerifyPaymentSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    utr_number = serializers.CharField(required=False, max_length=16)
    note = serializers.CharField(required=False, max_length=255)

    def validate_utr_number(self, value):
        value = (value or '').strip()
        if value and not UTR_RE.match(value):
            raise serializers.ValidationError('UTR must be 12–16 letters/digits from the UPI receipt')
        return value or None

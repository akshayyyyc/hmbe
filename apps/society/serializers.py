"""Serializers for society onboarding and subscription (doc 04 §4, §11, §17)."""

import re

from rest_framework import serializers

from apps.users.utils import normalize_phone

from .models import Society, SocietySubscription
from .services import generate_subdomain

PINCODE_RE = re.compile(r'^[1-9][0-9]{5}$')


class SocietyRegisterSerializer(serializers.ModelSerializer):
    """Self-service society onboarding input (SBO flow)."""

    president_phone = serializers.CharField(max_length=20)

    class Meta:
        model = Society
        fields = [
            'name', 'address', 'pincode', 'city', 'state',
            'total_units', 'president_name', 'president_phone', 'president_email',
        ]

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Society name is required')
        return value

    def validate_pincode(self, value):
        if not PINCODE_RE.match(value or ''):
            raise serializers.ValidationError('Invalid Indian pincode format (6 digits)')
        return value

    def validate_total_units(self, value):
        if value is None or int(value) <= 0:
            raise serializers.ValidationError('Total units must be a positive integer')
        return int(value)

    def validate_president_phone(self, value):
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError("President's mobile number is invalid")
        return normalized

    def validate_president_email(self, value):
        return value or None

    def create(self, validated_data):
        validated_data['subdomain'] = generate_subdomain(validated_data['name'])
        return super().create(validated_data)


class SocietyPublicSerializer(serializers.ModelSerializer):
    """What an unauthenticated visitor may see while choosing their society."""

    class Meta:
        model = Society
        fields = [
            'id', 'name', 'subdomain', 'address', 'city', 'state', 'pincode',
            'total_units', 'subscription_status',
        ]


class SocietyDetailSerializer(SocietyPublicSerializer):
    """Full society record for the tenant's own RWA/admin views."""

    registered_units = serializers.IntegerField(read_only=True)
    trial_start_date = serializers.DateField(read_only=True)
    trial_end_date = serializers.DateField(read_only=True)
    is_trial_active = serializers.SerializerMethodField()
    days_until_trial_end = serializers.IntegerField(read_only=True)

    class Meta(SocietyPublicSerializer.Meta):
        fields = SocietyPublicSerializer.Meta.fields + [
            'registered_units', 'subscription_tier', 'trial_start_date', 'trial_end_date',
            'is_trial_active', 'days_until_trial_end',
            'president_name', 'president_phone', 'president_email',
            'created_at',
        ]

    def get_is_trial_active(self, obj):
        return obj.is_trial_active()


class SocietySubscriptionSerializer(serializers.ModelSerializer):
    """Subscription state used by the B2B portal (doc 04 §11.1)."""

    tier = serializers.CharField(source='society.subscription_tier', read_only=True)
    society_status = serializers.CharField(source='society.subscription_status', read_only=True)
    trial_start_date = serializers.DateField(source='society.trial_start_date', read_only=True)
    trial_end_date = serializers.DateField(source='society.trial_end_date', read_only=True)
    is_trial_active = serializers.SerializerMethodField()
    days_until_trial_end = serializers.SerializerMethodField()

    class Meta:
        model = SocietySubscription
        fields = [
            'tier', 'billing_cycle', 'base_amount', 'status',
            'society_status', 'trial_start_date', 'trial_end_date',
            'is_trial_active', 'days_until_trial_end',
            'invoice_date', 'due_date', 'paid_date',
        ]

    def get_is_trial_active(self, obj):
        return obj.society.is_trial_active()

    def get_days_until_trial_end(self, obj):
        return obj.society.days_until_trial_end()

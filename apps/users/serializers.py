"""Serializers for authentication and user management (doc 04 §5, §17)."""

from rest_framework import serializers

from .models import CustomUser
from .utils import PUBLIC_PURPOSES, normalize_phone


class UserSerializer(serializers.ModelSerializer):
    """Public shape of a user; ``society`` is flattened to avoid leaking tenant rows."""

    society = serializers.SerializerMethodField()
    # Model properties: naming the field is enough, an explicit source here trips
    # DRF's "redundant source" assertion.
    is_rwa_admin = serializers.ReadOnlyField()
    is_resident_or_landlord = serializers.ReadOnlyField()

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 'phone',
            'role', 'society', 'resident_status', 'flat_number',
            'is_phone_verified', 'is_email_verified',
            'profile_photo', 'is_rwa_admin', 'is_resident_or_landlord',
            'created_at',
        ]
        read_only_fields = ['id', 'username', 'phone', 'role', 'created_at', 'society']

    def get_society(self, obj):
        if not obj.society_id:
            return None
        return {'id': obj.society.id, 'name': obj.society.name, 'subdomain': obj.society.subdomain}


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Self-service profile edits. Phone, role and society are not editable here."""

    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'profile_photo']


class SendOtpSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    purpose = serializers.CharField(required=False, default='LOGIN')

    def validate_phone(self, value):
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError('Enter a valid Indian mobile number.')
        return normalized

    def validate_purpose(self, value):
        purpose = (value or 'LOGIN').upper()
        if purpose not in PUBLIC_PURPOSES:
            raise serializers.ValidationError(f'purpose must be one of: {", ".join(sorted(PUBLIC_PURPOSES))}')
        return purpose


class VerifyOtpSerializer(SendOtpSerializer):
    otp = serializers.CharField(max_length=6, min_length=6)

    def validate_otp(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('OTP must be 6 digits.')
        return value


class AssignSocietySerializer(serializers.Serializer):
    society_subdomain = serializers.CharField(max_length=100)
    flat_number = serializers.CharField(max_length=20)


class UserListQuerySerializer(serializers.Serializer):
    """Filters accepted by the RWA user directory (doc 04 §5.3)."""

    role = serializers.CharField(required=False)
    resident_status = serializers.CharField(required=False)
    search = serializers.CharField(required=False)

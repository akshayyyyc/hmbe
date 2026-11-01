"""Notice board serializers (doc 04 §7)."""

from django.utils import timezone
from rest_framework import serializers

from .models import Notice


class NoticeSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
    attachment = serializers.FileField(use_url=True, required=False, allow_null=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Notice
        fields = [
            'id', 'title', 'content', 'category', 'is_pinned', 'expires_at',
            'attachment', 'created_by', 'created_at', 'updated_at', 'is_expired',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_created_by(self, obj):
        if not obj.created_by_id:
            return None
        user = obj.created_by
        return {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'role': user.role,
        }

    def get_is_expired(self, obj):
        return bool(obj.expires_at and obj.expires_at < timezone.now())


class NoticeWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notice
        fields = ['title', 'content', 'category', 'is_pinned', 'expires_at', 'attachment']

    def validate_title(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Title is required')
        return value

    def validate_content(self, value):
        value = (value or '').strip()
        if len(value) < 10:
            raise serializers.ValidationError('Content must be at least 10 characters')
        return value

    def validate_category(self, value):
        if value not in Notice.NoticeCategory.values:
            raise serializers.ValidationError(f'category must be one of: {", ".join(Notice.NoticeCategory.values)}')
        return value

    def validate_expires_at(self, value):
        if value and value < timezone.now():
            raise serializers.ValidationError('expires_at must be in the future')
        return value

    def validate_is_pinned(self, value):
        # NB-003: pinning is a committee power. Non-admins silently get False
        # rather than an error, because the mobile composer offers the toggle.
        request = self.context.get('request')
        if value and not getattr(getattr(request, 'user', None), 'is_rwa_admin', False):
            return False
        return bool(value)

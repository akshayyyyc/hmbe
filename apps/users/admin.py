"""Admin configuration for the Users app."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, OTPVerification


@admin.register(CustomUser)
class UserAdmin(BaseUserAdmin):
    list_display = ('phone', 'email', 'role', 'society', 'is_phone_verified')
    list_filter = ('role', 'is_phone_verified', 'resident_status')
    search_fields = ('phone', 'email', 'first_name', 'last_name')
    ordering = ('phone',)


@admin.register(OTPVerification)
class OTPVerificationAdmin(admin.ModelAdmin):
    list_display = ('phone', 'email', 'purpose', 'is_verified', 'expires_at')
    list_filter = ('purpose', 'is_verified')
    search_fields = ('phone', 'email')

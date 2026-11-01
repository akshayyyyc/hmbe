"""Admin configuration for the Society app."""

from django.contrib import admin
from .models import Society, SocietySubscription


@admin.register(Society)
class SocietyAdmin(admin.ModelAdmin):
    list_display = ('name', 'subdomain', 'city', 'total_units', 'subscription_status')
    list_filter = ('subscription_status', 'subscription_tier')
    search_fields = ('name', 'subdomain', 'pincode')


@admin.register(SocietySubscription)
class SocietySubscriptionAdmin(admin.ModelAdmin):
    list_display = ('society', 'tier', 'billing_cycle', 'base_amount', 'status')
    list_filter = ('tier', 'status')

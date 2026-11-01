"""Admin configuration for the Payments app."""

from django.contrib import admin

from .models import PaymentRecord


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'user', 'amount', 'method', 'status', 'utr_number', 'verified_at')
    list_filter = ('method', 'status')
    search_fields = ('utr_number', 'invoice__invoice_number', 'user__phone')
    readonly_fields = ('created_at', 'updated_at', 'verified_by', 'verified_at')
    date_hierarchy = 'created_at'
    autocomplete_fields = ('invoice', 'user', 'verified_by')

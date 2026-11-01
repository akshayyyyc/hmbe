"""Admin configuration for the Invoices app."""

from django.contrib import admin
from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('society', 'unit', 'invoice_number', 'billing_period', 'total_amount', 'status')
    list_filter = ('status', 'billing_period')
    search_fields = ('invoice_number',)

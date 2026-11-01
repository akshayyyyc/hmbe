"""Admin configuration for the Units app."""

from django.contrib import admin
from .models import Unit, Vehicle


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('society', 'building', 'floor', 'unit_number', 'unit_type', 'occupancy_type')
    list_filter = ('unit_type', 'occupancy_type')
    search_fields = ('building', 'unit_number')


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('unit', 'vehicle_number', 'vehicle_type', 'is_primary')
    list_filter = ('vehicle_type', 'is_primary')

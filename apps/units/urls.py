"""Unit registry routes — mounted at ``/api/v1/units/``."""

from django.urls import path

from . import views

app_name = 'units'

urlpatterns = [
    path('', views.unit_collection, name='collection'),
    path('bulk-import/', views.bulk_import, name='import'),
    path('vehicles/<int:vehicle_id>/', views.vehicle_detail, name='vehicle-detail'),
    path('<int:pk>/', views.unit_detail, name='detail'),
    path('<int:pk>/vehicles/', views.unit_vehicles, name='vehicles'),
]

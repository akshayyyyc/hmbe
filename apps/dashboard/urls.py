"""Dashboard routes — mounted at ``/api/v1/dashboard/``."""

from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('resident/', views.resident_dashboard, name='resident'),
    path('treasurer/', views.treasurer_dashboard, name='treasurer'),
]

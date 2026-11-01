"""Authenticated user routes — mounted at ``/api/v1/users/``."""

from django.urls import path

from . import views

app_name = 'users'

urlpatterns = [
    path('', views.list_users, name='list'),
    path('me/', views.current_user, name='me'),
    path('me/update/', views.update_current_user, name='me-update'),
    path('assign-to-society/', views.assign_to_society, name='assign-to-society'),
    path('residents/', views.list_residents, name='residents'),
]

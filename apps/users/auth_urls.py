"""Unauthenticated authentication routes — mounted at ``/api/v1/auth/``."""

from django.urls import path

from . import views

app_name = 'users_auth'

urlpatterns = [
    path('send-otp/', views.send_otp, name='send-otp'),
    path('verify-otp/', views.verify_otp_and_login, name='verify-otp'),
    path('refresh-token/', views.refresh_token, name='refresh-token'),
    path('logout/', views.logout, name='logout'),
]

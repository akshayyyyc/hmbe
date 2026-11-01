"""Society routes — mounted at ``/api/v1/society/``."""

from django.urls import path

from . import views

app_name = 'society'

urlpatterns = [
    path('register/', views.register_society, name='register'),
    path('verify-otp/', views.verify_society_otp, name='verify-otp'),
    path('search/', views.search_societies, name='search'),
    path('me/', views.my_society, name='me'),
    path('', views.list_societies, name='list'),
    # Static segments above must stay ahead of the subdomain catch-all.
    path('<str:subdomain>/', views.society_public_detail, name='public-detail'),
]

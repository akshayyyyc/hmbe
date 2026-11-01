"""Invoice routes — mounted at ``/api/v1/invoices/``."""

from django.urls import path

from . import views

app_name = 'invoices'

urlpatterns = [
    path('', views.invoice_list, name='list'),
    path('generate/', views.generate_invoices, name='generate'),
    path('<int:pk>/', views.invoice_detail, name='detail'),
]

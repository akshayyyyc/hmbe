"""Payment routes — mounted at ``/api/v1/payments/``."""

from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    path('', views.payment_list, name='list'),
    path('upi/generate-link/', views.generate_upi_link, name='upi-generate-link'),
    path('submit-manual/', views.submit_manual, name='submit-manual'),
    path('pending-count/', views.pending_verification_count, name='pending-count'),
    path('<int:payment_id>/verify/', views.verify_payment, name='verify'),
    path('<int:pk>/', views.payment_detail, name='detail'),
]

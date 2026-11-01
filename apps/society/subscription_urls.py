"""Subscription routes — mounted at ``/api/v1/subscription/`` (doc 04 §11).

No ``webhook/`` route in Phase 1: there is no gateway to call us, and doc 07
defers webhook reconciliation to Phase 4b. Activation is the manual
``confirm/`` step below.
"""

from django.urls import path

from . import views

app_name = 'subscription'

urlpatterns = [
    path('status/', views.subscription_status, name='status'),
    path('checkout/', views.subscription_checkout, name='checkout'),
    path('confirm/', views.subscription_confirm, name='confirm'),
]

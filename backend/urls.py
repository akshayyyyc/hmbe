"""
URL configuration — HomeManager backend.

Layout (doc 04 §14): public endpoints first, then the authenticated tenant-scoped
API. ``/api/health/`` stays as an alias so the Docker/nginx healthchecks written
in W1 keep working while the canonical path moves under ``/api/v1/``.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import HealthCheckView

admin.site.site_header = 'HomeManager Administration'
admin.site.site_title = 'HomeManager Admin'
admin.site.index_title = 'Platform operations'

urlpatterns = [
    # ── Public ───────────────────────────────────────────────────────────
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/v1/health/', HealthCheckView.as_view(), name='health-check-v1'),

    # Auth (rate-limited by OTP cooldown, not by DRF throttling — doc 04 §3.1)
    path('api/v1/auth/', include('apps.users.auth_urls')),

    # Society onboarding is public until the president's OTP is verified
    path('api/v1/society/', include('apps.society.urls')),
    path('api/v1/subscription/', include('apps.society.subscription_urls')),

    # ── Authenticated, tenant-scoped ─────────────────────────────────────
    path('api/v1/users/', include('apps.users.urls')),
    path('api/v1/units/', include('apps.units.urls')),
    path('api/v1/notices/', include('apps.notices.urls')),
    path('api/v1/chat/', include('apps.chat.urls')),
    path('api/v1/invoices/', include('apps.invoices.urls')),
    path('api/v1/payments/', include('apps.payments.urls')),
    path('api/v1/dashboard/', include('apps.dashboard.urls')),

    # ── Django admin ─────────────────────────────────────────────────────
    path('admin/', admin.site.urls),
]

# Media is served by nginx in Docker; this only covers local runserver.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

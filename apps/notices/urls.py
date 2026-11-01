"""Notice board routes — mounted at ``/api/v1/notices/``."""

from django.urls import path

from . import views

app_name = 'notices'

urlpatterns = [
    path('', views.notice_collection, name='collection'),
    path('<int:pk>/', views.notice_detail, name='detail'),
    path('<int:pk>/pin/', views.notice_pin, name='pin'),
]

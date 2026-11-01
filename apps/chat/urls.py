"""Chat routes — mounted at ``/api/v1/chat/``."""

from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('rooms/', views.room_list, name='room-list'),
    path('messages/', views.message_list, name='message-list'),
    path('read-receipt/', views.read_receipt, name='read-receipt'),
]

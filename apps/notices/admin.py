"""Admin configuration for the Notices app."""

from django.contrib import admin
from .models import Notice


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('society', 'title', 'category', 'is_pinned', 'created_at')
    list_filter = ('category', 'is_pinned')
    search_fields = ('title', 'content')

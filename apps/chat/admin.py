"""Admin configuration for the Chat app."""

from django.contrib import admin

from .models import ChatMessage, ChatReadReceipt, ChatRoom


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ('sender', 'content', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    list_display = ('society', 'room_name', 'is_active', 'message_count')
    list_filter = ('is_active',)
    search_fields = ('society__name', 'room_name')
    inlines = [ChatMessageInline]

    @admin.display(description='Messages')
    def message_count(self, obj):
        return obj.messages.count()


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('room', 'sender', 'created_at', 'excerpt')
    search_fields = ('content', 'sender__phone', 'sender__first_name')
    list_filter = ('room',)
    date_hierarchy = 'created_at'

    @admin.display(description='Content')
    def excerpt(self, obj):
        return obj.content[:80]


@admin.register(ChatReadReceipt)
class ChatReadReceiptAdmin(admin.ModelAdmin):
    list_display = ('room', 'user', 'last_read_at')
    search_fields = ('user__phone',)

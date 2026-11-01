"""Chat room and message models for community chat."""

from django.db import models
from django.conf import settings


class ChatRoom(models.Model):
    """A society-wide chat room (one per society)."""

    society = models.OneToOneField(
        'society.Society', on_delete=models.CASCADE, related_name='chat_room'
    )
    room_name = models.CharField(max_length=100, default='Society Chat')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_room'

    def __str__(self):
        return f"{self.society.name} Chat"


class ChatMessage(models.Model):
    """A single WebSocket chat message."""

    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_message'
        ordering = ['created_at']
        indexes = [models.Index(fields=['room', 'created_at'])]

    def __str__(self):
        return f"{self.sender} @ {self.created_at}"


class ChatReadReceipt(models.Model):
    """How far a user has read in a room (doc 04 §8.2).

    Backs the ``unread_messages`` counter on the resident dashboard, which needs
    a per-user watermark — deriving it from message rows alone would mean
    scanning history on every dashboard load.
    """

    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='read_receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    last_read_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'chat_read_receipt'
        constraints = [
            models.UniqueConstraint(fields=['room', 'user'], name='unique_room_user_receipt')
        ]

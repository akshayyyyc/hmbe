"""Chat helpers shared by the REST endpoints and (from W5) the WebSocket consumer."""

from datetime import timedelta

from django.utils import timezone

from .models import ChatMessage, ChatReadReceipt, ChatRoom

# With no read receipt yet, "unread" is bounded to a recent window so a first
# login into an old room does not show thousands of unread messages.
DEFAULT_UNREAD_WINDOW_DAYS = 7


def room_for_user(user):
    """The caller's society room, creating it if a legacy society has none."""
    if not getattr(user, 'society_id', None):
        return None
    room, _ = ChatRoom.objects.get_or_create(society=user.society)
    return room


def unread_count(room, user):
    """Messages in ``room`` the user has not seen yet."""
    if not room:
        return 0
    receipt = ChatReadReceipt.objects.filter(room=room, user=user).first()
    since = receipt.last_read_at if receipt else timezone.now() - timedelta(days=DEFAULT_UNREAD_WINDOW_DAYS)
    return ChatMessage.objects.filter(room=room, created_at__gt=since).exclude(sender=user).count()


def mark_read(room, user):
    """Advance the user's read watermark to now."""
    receipt, _ = ChatReadReceipt.objects.get_or_create(
        room=room, user=user, defaults={'last_read_at': timezone.now()}
    )
    if receipt.last_read_at < timezone.now():
        receipt.last_read_at = timezone.now()
        receipt.save(update_fields=['last_read_at', 'updated_at'])
    return receipt

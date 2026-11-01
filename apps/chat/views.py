"""Chat REST endpoints: room lookup, history and read receipts (doc 04 §8).

Live messaging arrives over WebSocket in W5 (doc 08); these routes stay the
source of truth for history, infinite scroll and the mobile app's fallback.
"""

import logging
from datetime import datetime

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.core.pagination import ApiPagination
from apps.core.permissions import HasSociety
from apps.core.responses import error, ok, validation_error

from .models import ChatMessage
from .serializers import ChatMessageSerializer, ChatRoomSerializer
from .services import mark_read, room_for_user

logger = logging.getLogger(__name__)

MAX_PAGE = 100


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def room_list(request):
    """The caller's society room (Phase 1 has exactly one per society)."""
    room = room_for_user(request.user)
    if not room:
        return error('NO_CHAT_ROOM', 'No chat room available', status_code=404)
    return ok([ChatRoomSerializer(room).data])


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def message_list(request):
    """Paginated history, newest first, optionally before a cursor (§8.1)."""
    room = room_for_user(request.user)
    if not room:
        return error('NO_CHAT_ROOM', 'No chat room available', status_code=404)

    messages = (
        ChatMessage.objects.filter(room=room)
        .select_related('sender')
        .order_by('-created_at')
    )

    before = request.query_params.get('before')
    if before:
        try:
            cursor = datetime.fromisoformat(before.replace('Z', '+00:00'))
            if timezone.is_naive(cursor):
                cursor = timezone.make_aware(cursor, timezone.get_current_timezone())
        except ValueError:
            return validation_error({'before': ['Use an ISO-8601 timestamp, e.g. 2026-11-01T10:00:00Z']})
        messages = messages.filter(created_at__lt=cursor)

    try:
        limit = int(request.query_params.get('limit', 50))
    except (TypeError, ValueError):
        return validation_error({'limit': ['limit must be a number']})
    limit = max(1, min(limit, MAX_PAGE))

    # Chat defaults to a larger page than the REST list default.
    paginator = ApiPagination()
    paginator.page_size = limit
    page = paginator.paginate_queryset(messages, request)
    return paginator.get_paginated_response(ChatMessageSerializer(page or [], many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasSociety])
def read_receipt(request):
    """Advance the caller's read watermark (§8.2)."""
    room = room_for_user(request.user)
    if not room:
        return error('NO_CHAT_ROOM', 'No chat room available', status_code=404)

    receipt = mark_read(room, request.user)
    return ok({'room': room.id, 'last_read_at': receipt.last_read_at, 'unread': 0})

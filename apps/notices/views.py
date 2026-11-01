"""Notice board CRUD + pin toggle (doc 04 §7)."""

import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsRWAAdmin
from apps.core.responses import error, ok, validation_error

from .models import Notice
from .serializers import NoticeSerializer, NoticeWriteSerializer

logger = logging.getLogger(__name__)


def notice_list(request):
    """Active notices for the caller's society, pinned first (§7.1).

    Expired notices are hidden by default — that is the auto-archive rule — but
    RWA admins can pass ``?include_expired=true`` while clearing the board.
    """
    notices = Notice.objects.filter(society=request.user.society)

    include_expired = (request.query_params.get('include_expired') or '').lower() in ('1', 'true', 'yes')
    if not (include_expired and request.user.is_rwa_admin):
        notices = notices.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))

    category = request.query_params.get('category')
    if category:
        notices = notices.filter(category=category)
    if (request.query_params.get('only_pinned') or '').lower() in ('1', 'true', 'yes'):
        notices = notices.filter(is_pinned=True)

    notices = notices.select_related('created_by').order_by('-is_pinned', '-created_at')
    return paginated(notices, request, lambda rows: NoticeSerializer(rows, many=True).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, HasSociety])
def notice_collection(request):
    """``GET`` reads the board, ``POST`` publishes to it (§7.1, §7.2).

    One path, two verbs, so a single view dispatches them. Posting is an RWA
    action (NB-003); the handlers below are plain functions because an
    ``@api_view`` wrapper around a DRF request trips DRF's own request check.
    """
    if request.method == 'GET':
        return notice_list(request)
    if not request.user.is_rwa_admin:
        return error('PERMISSION_DENIED', 'Only RWA committee members can post notices', status_code=403)
    return notice_create(request)


def notice_create(request):
    """Publish a notice. Board posts are an RWA action (NB-003, §7.2)."""
    serializer = NoticeWriteSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    notice = serializer.save(society=request.user.society, created_by=request.user)
    return ok(NoticeSerializer(notice).data, status_code=201)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, HasSociety])
def notice_detail(request, pk):
    """Author or RWA admin may edit/delete; any society member may read (§7.3, §7.4)."""
    notice = Notice.objects.filter(pk=pk, society=request.user.society).select_related('created_by').first()
    if not notice:
        return error('NOTICE_NOT_FOUND', 'Notice not found', status_code=404)

    if request.method == 'GET':
        return ok(NoticeSerializer(notice).data)

    is_author = notice.created_by_id == request.user.id
    if not (is_author or request.user.is_rwa_admin):
        return error('PERMISSION_DENIED', 'Only the author or an RWA admin can change this notice', status_code=403)

    if request.method == 'DELETE':
        title = notice.title
        notice.delete()
        # Phase 1 has no audit table; the application log is the audit trail
        # until the audit-log model lands with the superadmin console (Phase 6).
        logger.info('Notice deleted by %s (%s): %s', request.user.phone, request.user.role, title)
        return ok({'message': 'Notice deleted', 'id': pk})

    serializer = NoticeWriteSerializer(notice, data=request.data, partial=(request.method == 'PATCH'),
                                       context={'request': request})
    if not serializer.is_valid():
        return validation_error(serializer.errors)
    notice = serializer.save()
    return ok(NoticeSerializer(notice).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def notice_pin(request, pk):
    """Pin or unpin a notice (§7.5)."""
    notice = Notice.objects.filter(pk=pk, society=request.user.society).first()
    if not notice:
        return error('NOTICE_NOT_FOUND', 'Notice not found', status_code=404)

    notice.is_pinned = not notice.is_pinned
    notice.save(update_fields=['is_pinned', 'updated_at'])
    return ok({'id': notice.id, 'is_pinned': notice.is_pinned})

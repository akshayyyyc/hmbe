"""Shared API views — currently the health check (doc 04 §13)."""

from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .responses import ok


class HealthCheckView(APIView):
    """Liveness/readiness for Docker, nginx and smoke tests.

    Checks Postgres (`SELECT 1`) and Redis (PING). Redis failures degrade the
    response rather than failing it outright when the caller only needs the API
    to be up — cache and the channel layer both have workable fallbacks short
    term, so ``degraded`` + 503 is the honest signal.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        database = 'connected'
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
        except Exception:  # noqa: BLE001 - health checks must never raise
            database = 'error'

        redis_state = 'connected'
        try:
            import redis as redis_lib

            client = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            client.ping()
        except Exception:  # noqa: BLE001
            redis_state = 'error'

        healthy = database == 'connected' and redis_state == 'connected'
        payload = {
            'status': 'ok' if healthy else 'degraded',
            'database': database,
            'redis': redis_state,
            'timestamp': timezone.now().isoformat(),
        }

        if healthy:
            return ok(payload)
        return Response(
            {
                'success': False,
                'data': payload,
                'error': {
                    'code': 'DEGRADED',
                    'message': 'One or more dependencies are unavailable',
                },
            },
            status=503,
        )

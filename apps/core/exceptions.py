"""Project-wide DRF exception handler (doc 04 §2.4).

Normalises every DRF error into the error envelope so clients get one shape:
``{"success": false, "error": {"code", "message", "details"}}``.

Uncaught non-DRF errors fall through to Django's 500 handler, except database
connectivity failures, which are reported as 503 rather than leaking a stack.
"""

import logging

from django.db import DatabaseError, OperationalError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler

from .responses import error

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    status.HTTP_400_BAD_REQUEST: 'BAD_REQUEST',
    status.HTTP_401_UNAUTHORIZED: 'UNAUTHORIZED',
    status.HTTP_403_FORBIDDEN: 'FORBIDDEN',
    status.HTTP_404_NOT_FOUND: 'NOT_FOUND',
    status.HTTP_405_METHOD_NOT_ALLOWED: 'METHOD_NOT_ALLOWED',
    status.HTTP_409_CONFLICT: 'CONFLICT',
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: 'UNSUPPORTED_MEDIA_TYPE',
    status.HTTP_429_TOO_MANY_REQUESTS: 'RATE_LIMITED',
    status.HTTP_500_INTERNAL_SERVER_ERROR: 'INTERNAL_ERROR',
}

# DRF's own exception_code values, mapped to project error codes.
_DRF_CODES = {
    'not_authenticated': 'UNAUTHORIZED',
    'authentication_failed': 'INVALID_CREDENTIALS',
    'permission_denied': 'PERMISSION_DENIED',
    'not_found': 'NOT_FOUND',
    'method_not_allowed': 'METHOD_NOT_ALLOWED',
    'parse_error': 'INVALID_REQUEST_BODY',
    'unsupported_media_type': 'UNSUPPORTED_MEDIA_TYPE',
    'authentication_throttled': 'RATE_LIMITED',
    'throttled': 'RATE_LIMITED',
}


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        # Errors DRF does not know about — database connectivity is the realistic
        # one here (Postgres restart / connection pool exhaustion).
        if isinstance(exc, OperationalError):
            logger.warning('Database connectivity error: %s', exc)
            return error(
                'DATABASE_ERROR',
                'Database connectivity issue',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if isinstance(exc, DatabaseError):
            logger.exception('Database error: %s', exc)
            return error(
                'DATABASE_ERROR',
                'Database request failed',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return None

    data = response.data
    details = None

    if isinstance(exc, ValidationError):
        code = 'VALIDATION_ERROR'
        message = 'Validation failed'
        details = data
    elif isinstance(data, dict) and 'detail' in data:
        code = _DRF_CODES.get(
            getattr(exc, 'default_code', None),
            _STATUS_CODES.get(response.status_code, 'ERROR'),
        )
        message = str(data['detail'])
        extra = {k: v for k, v in data.items() if k != 'detail'}
        details = extra or None
    else:
        code = _STATUS_CODES.get(response.status_code, 'ERROR')
        message = 'Validation failed'
        details = data

    return error(code, message, status_code=response.status_code, details=details)

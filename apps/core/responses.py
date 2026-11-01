"""Response envelope helpers shared by every API endpoint (doc 04 §1.2).

All successful responses are ``{"success": true, "data": ..., "meta": ...}``;
all error responses are ``{"success": false, "error": {...}}``.
"""

from rest_framework import status
from rest_framework.response import Response


def ok(data, *, status_code=status.HTTP_200_OK, meta=None):
    """Return a success envelope."""
    payload = {'success': True, 'data': data}
    if meta is not None:
        payload['meta'] = meta
    return Response(payload, status=status_code)


def error(code, message, *, status_code, details=None):
    """Return an error envelope.

    ``code`` is a stable machine-readable string (e.g. ``INVALID_OTP``);
    ``message`` is human readable; ``details`` carries field-level errors.
    """
    payload = {'success': False, 'error': {'code': code, 'message': message}}
    if details:
        payload['error']['details'] = details
    return Response(payload, status=status_code)


def validation_error(details, message='Validation failed'):
    """400 with field-level details, matching doc 04 §1.3."""
    return error('VALIDATION_ERROR', message, status_code=400, details=details)

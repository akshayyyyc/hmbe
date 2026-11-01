"""Invoice endpoints (doc 04 §10)."""

import logging

from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsRWAAdmin
from apps.core.responses import error, ok, validation_error

from .models import Invoice
from .serializers import InvoiceGenerateSerializer, InvoiceSerializer
from .services import current_period, generate_invoices_for_society

logger = logging.getLogger(__name__)


def visible_invoices(user):
    """Tenant scope plus the resident/landlord narrowing (§10.1).

    Residents see invoices for the flats they hold — matched through the unit
    registry's owner/tenant phones, because a flat can be billed before its
    occupant has finished linking an account.
    """
    qs = Invoice.objects.filter(society=user.society).select_related('unit', 'resident')
    if user.is_rwa_admin:
        return qs
    return qs.filter(
        Q(resident=user) | Q(unit__owner_phone=user.phone) | Q(unit__tenant_phone=user.phone)
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def invoice_list(request):
    """Own invoices for residents, all of them for RWA admins."""
    invoices = visible_invoices(request.user)

    status_filter = request.query_params.get('status')
    if status_filter:
        invoices = invoices.filter(status=status_filter)
    period = request.query_params.get('billing_period')
    if period:
        invoices = invoices.filter(billing_period=period)
    unit = request.query_params.get('unit')
    if unit:
        invoices = invoices.filter(Q(unit__unit_number__iexact=unit) | Q(unit__building__iexact=unit))

    invoices = invoices.order_by('-billing_period', 'unit__building', 'unit__unit_number')
    return paginated(invoices, request, lambda rows: InvoiceSerializer(rows, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def invoice_detail(request, pk):
    """Full breakdown plus the payments already filed against it (§10.2)."""
    invoice = visible_invoices(request.user).filter(pk=pk).first()
    if not invoice:
        return error('INVOICE_NOT_FOUND', 'Invoice not found', status_code=404)
    return ok(InvoiceSerializer(invoice).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def generate_invoices(request):
    """Run the monthly billing cycle for this society (§10.3).

    Idempotent — calling it twice in a month does not double-bill anyone.
    """
    query = InvoiceGenerateSerializer(data=request.data)
    if not query.is_valid():
        return validation_error(query.errors)

    result = generate_invoices_for_society(
        request.user.society, period=query.validated_data.get('billing_period'), actor=request.user
    )
    return ok({**result, 'default_period': current_period()}, status_code=201)

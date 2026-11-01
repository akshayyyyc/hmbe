"""Aggregated dashboard payloads for residents and RWA treasurers (doc 04 §12).

Both endpoints are deliberately single calls: the mobile app and the web panel
each render one screen from one round trip, which is what keeps the p95 < 200 ms
target realistic on a ₹1,500/month VPS.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.chat.services import room_for_user, unread_count
from apps.core.permissions import HasSociety
from apps.core.responses import error, ok
from apps.invoices.models import Invoice
from apps.invoices.services import current_period
from apps.notices.models import Notice
from apps.payments.models import PaymentRecord
from apps.units.models import Unit
from apps.users.models import CustomUser

ZERO = Decimal('0')


def _money(value):
    return str(Decimal(value or 0).quantize(Decimal('0.01')))


def _resident_invoices(user):
    return Invoice.objects.filter(
        Q(resident=user) | Q(unit__owner_phone=user.phone) | Q(unit__tenant_phone=user.phone),
        society=user.society,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def resident_dashboard(request):
    """Balance due, open invoices, latest notices and unread chat count (§12.1)."""
    user = request.user
    invoices = _resident_invoices(user)

    outstanding = sum((inv.outstanding_amount for inv in invoices), ZERO)
    pending_payments = [
        {
            'invoice_id': inv.id,
            'invoice_number': inv.invoice_number,
            'amount': _money(inv.outstanding_amount),
            'due_date': inv.due_date,
            'status': inv.status,
        }
        for inv in invoices.exclude(status=Invoice.InvoiceStatus.PAID).order_by('due_date')[:10]
    ]

    notices = (
        Notice.objects.filter(society=user.society)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .order_by('-is_pinned', '-created_at')[:5]
    )
    room = room_for_user(user)

    return ok(
        {
            'user': {
                'id': user.id,
                'name': user.get_full_name() or user.username,
                'flat_number': user.flat_number,
                'role': user.role,
                'resident_status': user.resident_status,
            },
            'society': {'id': user.society.id, 'name': user.society.name},
            'outstanding_balance': _money(outstanding),
            'pending_payments': pending_payments,
            'recent_notices': [
                {
                    'id': n.id,
                    'title': n.title,
                    'category': n.category,
                    'is_pinned': n.is_pinned,
                    'created_at': n.created_at,
                }
                for n in notices
            ],
            'unread_messages': unread_count(room, user),
            'needs_society_link': user.society_id is None,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def treasurer_dashboard(request):
    """Collection health for the month, plus the two action queues (§12.2)."""
    user = request.user
    if not user.is_rwa_admin:
        return error('PERMISSION_DENIED', 'Only RWA admins can view this dashboard', status_code=403)

    society = user.society
    period = current_period()

    totals = Invoice.objects.filter(society=society).aggregate(
        billed=Sum('total_amount'), collected=Sum('paid_amount')
    )
    period_invoices = Invoice.objects.filter(society=society, billing_period=period)
    collected_this_month = sum(
        (inv.paid_amount or ZERO for inv in period_invoices), ZERO
    )
    pending_this_month = sum(
        (inv.outstanding_amount for inv in period_invoices if inv.status != Invoice.InvoiceStatus.PAID),
        ZERO,
    )
    overdue = Invoice.objects.filter(
        society=society, status=Invoice.InvoiceStatus.OVERDUE
    ).aggregate(amount=Sum('total_amount'), units=Count('unit', distinct=True))
    billed_total = Decimal(totals['billed'] or 0)
    collected_total = Decimal(totals['collected'] or 0)

    collection_rate = (
        round(float(collected_total / billed_total * 100), 2) if billed_total else 0.0
    )

    recent_payments = PaymentRecord.objects.filter(
        invoice__society=society, status=PaymentRecord.PaymentStatus.SUCCESS
    ).select_related('invoice', 'invoice__unit').order_by('-verified_at')[:8]

    overdue_invoices = (
        Invoice.objects.filter(society=society, status=Invoice.InvoiceStatus.OVERDUE)
        .select_related('unit')
        .order_by('due_date')[:10]
    )
    pending_verification = PaymentRecord.objects.filter(
        invoice__society=society,
        status=PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
    ).count()

    return ok(
        {
            'society': {
                'id': society.id,
                'name': society.name,
                'subdomain': society.subdomain,
                'subscription_status': society.subscription_status,
                'total_units': society.total_units,
                'registered_units': society.registered_units,
                'active_residents': CustomUser.objects.filter(
                    society=society,
                    resident_status=CustomUser.ResidentStatus.VERIFIED,
                    role__in=[CustomUser.UserRole.RESIDENT, CustomUser.UserRole.LANDLORD],
                ).count(),
                'billing_period': period,
            },
            'financial_summary': {
                'total_billed': _money(billed_total),
                'total_collected': _money(collected_total),
                'total_collected_this_month': _money(collected_this_month),
                'total_pending_this_month': _money(pending_this_month),
                'total_overdue': _money(overdue['amount'] or 0),
                'collection_rate': collection_rate,
                'units_with_outstanding': overdue['units'],
                'pending_verification_payments': pending_verification,
            },
            'recent_payments': [
                {
                    'id': p.id,
                    'unit_number': p.invoice.unit.full_unit_id,
                    'amount': _money(p.amount),
                    'status': p.status,
                    'utr_number': p.utr_number,
                    'created_at': p.verified_at or p.created_at,
                }
                for p in recent_payments
            ],
            'overdue_invoices': [
                {
                    'invoice_id': inv.id,
                    'invoice_number': inv.invoice_number,
                    'unit_number': inv.unit.full_unit_id,
                    'amount': _money(inv.outstanding_amount),
                    'due_date': inv.due_date,
                    'days_overdue': (date.today() - inv.due_date).days,
                }
                for inv in overdue_invoices
            ],
            'registry': {
                'units_total': Unit.objects.filter(society=society, is_deleted=False).count(),
                'units_rented': Unit.objects.filter(society=society, is_deleted=False, is_rented=True).count(),
                'vehicles': Unit.objects.filter(society=society, is_deleted=False)
                .aggregate(total=Count('vehicles'))['total'],
            },
        }
    )

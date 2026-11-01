"""UPI intent, manual receipt submission and treasurer verification (doc 04 §9)."""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsRWAAdmin
from apps.core.responses import error, ok, validation_error
from apps.invoices.models import Invoice
from apps.invoices.services import apply_payment_to_invoice
from apps.invoices.views import visible_invoices

from .models import PaymentRecord
from .serializers import (
    ManualPaymentSerializer,
    PaymentRecordSerializer,
    UpiLinkRequestSerializer,
    VerifyPaymentSerializer,
)
from .services import build_upi_link

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def payment_list(request):
    """Payment history — own payments for residents, all for RWA admins."""
    payments = PaymentRecord.objects.filter(
        invoice__society=request.user.society
    ).select_related('invoice', 'invoice__unit', 'user')

    if not request.user.is_rwa_admin:
        payments = payments.filter(user=request.user)

    status_filter = request.query_params.get('status')
    if status_filter:
        payments = payments.filter(status=status_filter)
    invoice_id = request.query_params.get('invoice')
    if invoice_id:
        payments = payments.filter(invoice_id=invoice_id)

    payments = payments.order_by('-created_at')
    return paginated(payments, request, lambda rows: PaymentRecordSerializer(rows, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasSociety])
def generate_upi_link(request):
    """Create a pending payment and return the ``upi://pay`` deep link (§9.1)."""
    serializer = UpiLinkRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    invoice = visible_invoices(request.user).filter(pk=serializer.validated_data['invoice_id']).first()
    if not invoice:
        return error('INVOICE_NOT_FOUND', 'Invoice not found', status_code=404)
    if invoice.status == Invoice.InvoiceStatus.PAID:
        return error('ALREADY_PAID', 'This invoice is already settled', status_code=409)

    amount = serializer.validated_data.get('amount') or invoice.outstanding_amount
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if amount <= 0:
        return validation_error({'amount': ['Amount must be greater than zero']})
    if amount > invoice.outstanding_amount:
        return validation_error(
            {'amount': [f'Amount exceeds the outstanding balance of ₹{invoice.outstanding_amount}']}
        )

    payment = PaymentRecord.objects.create(
        invoice=invoice,
        user=request.user,
        amount=amount,
        method=PaymentRecord.PaymentMethod.UPI_INTENT,
        status=PaymentRecord.PaymentStatus.PENDING,
        payment_app=serializer.validated_data.get('payment_app') or None,
    )

    link = build_upi_link(
        amount=amount,
        transaction_note=invoice.invoice_number,
        payee_vpa=settings.UPI_PAYEE_VPA,
        payee_name=settings.UPI_PAYEE_NAME,
    )

    return ok(
        {
            'payment_record_id': payment.id,
            'upi_link': link,
            'amount': str(amount),
            'invoice_number': invoice.invoice_number,
            'payee_vpa': settings.UPI_PAYEE_VPA,
            'payee_name': settings.UPI_PAYEE_NAME,
            'transaction_note': invoice.invoice_number,
            'expires_in': settings.UPI_INTENT_TTL_SECONDS,
        },
        status_code=201,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasSociety])
def submit_manual(request):
    """Resident reports a payment the system cannot see (MVR-001…005, §9.2).

    Phase 1 has no bank confirmation, so a self-declared transfer waits in
    ``PENDING_VERIFICATION`` until an RWA admin approves it.
    """
    serializer = ManualPaymentSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    invoice = visible_invoices(request.user).filter(pk=serializer.validated_data['invoice_id']).first()
    if not invoice:
        return error('INVOICE_NOT_FOUND', 'Invoice not found', status_code=404)
    if invoice.status == Invoice.InvoiceStatus.PAID:
        return error('ALREADY_PAID', 'This invoice is already settled', status_code=409)

    utr = serializer.validated_data.get('utr_number')
    if utr and PaymentRecord.objects.filter(utr_number=utr).exists():
        return error('UTR_ALREADY_USED', 'This UTR has already been recorded', status_code=409)

    amount = serializer.validated_data.get('amount_paid') or invoice.outstanding_amount
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))

    # A UPI intent the resident already opened should be completed, not
    # duplicated — otherwise the treasurer reviews the same transfer twice.
    payment = PaymentRecord.objects.filter(
        invoice=invoice,
        user=request.user,
        method=PaymentRecord.PaymentMethod.UPI_INTENT,
        status=PaymentRecord.PaymentStatus.PENDING,
    ).order_by('-created_at').first()

    with transaction.atomic():
        if payment:
            payment.method = PaymentRecord.PaymentMethod.UPI_INTENT
            payment.status = PaymentRecord.PaymentStatus.PENDING_VERIFICATION
            payment.amount = amount
            payment.utr_number = utr or payment.utr_number
            payment.receipt_image = serializer.validated_data.get('receipt_image') or payment.receipt_image
            payment.payment_app = serializer.validated_data.get('payment_app') or payment.payment_app
            payment.save()
        else:
            payment = PaymentRecord.objects.create(
                invoice=invoice,
                user=request.user,
                amount=amount,
                method=PaymentRecord.PaymentMethod.MANUAL,
                status=PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
                utr_number=utr,
                receipt_image=serializer.validated_data.get('receipt_image'),
                payment_app=serializer.validated_data.get('payment_app') or None,
            )

    return ok(PaymentRecordSerializer(payment).data, status_code=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def verify_payment(request, payment_id):
    """Approve or reject a self-declared payment (MVR-003, §9.3).

    Approval is the only point in Phase 1 where money is recognised: it moves
    the payment to SUCCESS and posts the amount against the invoice.
    """
    serializer = VerifyPaymentSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    payment = PaymentRecord.objects.filter(
        pk=payment_id, invoice__society=request.user.society
    ).select_related('invoice', 'invoice__unit').first()
    if not payment:
        return error('PAYMENT_NOT_FOUND', 'Payment not found', status_code=404)
    if payment.status in (
        PaymentRecord.PaymentStatus.SUCCESS,
        PaymentRecord.PaymentStatus.REJECTED,
    ):
        return error('ALREADY_VERIFIED', f'Payment is already {payment.status}', status_code=409)

    action = serializer.validated_data['action']
    utr = serializer.validated_data.get('utr_number') or payment.utr_number

    if action == 'reject':
        note = serializer.validated_data.get('note')
        if not note:
            return validation_error({'note': ['A reason is required when rejecting a payment']})
        payment.status = PaymentRecord.PaymentStatus.REJECTED
        payment.verification_note = note
        payment.verified_by = request.user
        payment.verified_at = timezone.now()
        payment.save()
        logger.info('Payment %s rejected by %s: %s', payment.id, request.user.phone, note)
        return ok(
            {
                'payment_record_id': payment.id,
                'status': payment.status,
                'invoice_status': payment.invoice.status,
                'verified_by': request.user.get_full_name() or request.user.role,
                'verified_at': payment.verified_at,
            }
        )

    if not utr:
        return validation_error({'utr_number': ['UTR is required to approve a payment']})

    with transaction.atomic():
        payment.status = PaymentRecord.PaymentStatus.SUCCESS
        payment.utr_number = utr
        payment.verified_by = request.user
        payment.verified_at = timezone.now()
        payment.save()
        invoice = apply_payment_to_invoice(
            payment.invoice, payment.amount, method=payment.method, utr_number=utr
        )

    logger.info('Payment %s approved by %s (UTR %s)', payment.id, request.user.phone, utr)
    return ok(
        {
            'payment_record_id': payment.id,
            'status': payment.status,
            'invoice_status': invoice.status,
            'invoice_paid_amount': str(invoice.paid_amount),
            'invoice_outstanding': str(invoice.outstanding_amount),
            'verified_by': request.user.get_full_name() or request.user.role,
            'verified_at': payment.verified_at,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def pending_verification_count(request):
    """Badge count for the treasurer queue."""
    count = PaymentRecord.objects.filter(
        invoice__society=request.user.society,
        status=PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
    ).count()
    return ok({'pending_verification': count})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def payment_detail(request, pk):
    payment = PaymentRecord.objects.filter(
        pk=pk, invoice__society=request.user.society
    ).select_related('invoice', 'invoice__unit', 'user').first()
    if not payment:
        return error('PAYMENT_NOT_FOUND', 'Payment not found', status_code=404)
    if not request.user.is_rwa_admin and payment.user_id != request.user.id:
        return error('PAYMENT_NOT_FOUND', 'Payment not found', status_code=404)
    return ok(PaymentRecordSerializer(payment).data)

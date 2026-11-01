"""UPI intent and manual-verification (MVR) tests — doc 04 §9, doc 03 §2.6."""

import io
import os
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from apps.core.testing import api_client, committee, make_society, make_unit, make_user
from apps.invoices.models import Invoice
from apps.invoices.services import current_period, generate_invoices_for_society
from apps.users.models import CustomUser

from .models import PaymentRecord
from .services import build_upi_link


class UpiLinkBuilderTests(TestCase):
    def test_link_carries_the_mandated_upi_fields(self):
        link = build_upi_link(amount=Decimal('1999.00'), transaction_note='INV-2026-11-0001-A101',
                              payee_vpa='society@upi', payee_name='Sunrise Residency')
        self.assertTrue(link.startswith('upi://pay?'))
        query = parse_qs(urlparse(link).query)
        self.assertEqual(query['pa'], ['society@upi'])
        self.assertEqual(query['pn'], ['Sunrise Residency'])
        self.assertEqual(query['am'], ['1999.00'])
        self.assertEqual(query['cu'], ['INR'])
        self.assertEqual(query['tn'], ['INV-2026-11-0001-A101'])

    def test_note_is_quoted_and_capped(self):
        link = build_upi_link(amount=Decimal('10.00'), transaction_note='A & B/C 1234567890' * 8,
                              payee_vpa='society@upi', payee_name='Sunrise & Co')
        query = parse_qs(urlparse(link).query)
        self.assertLessEqual(len(query['tn'][0]), 50)
        self.assertNotIn('&', urlparse(link).query.split('tn=')[1].split('&')[0])


class UpiIntentEndpointTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        make_unit(self.society, 'A', '101', owner_phone=self.resident.phone)
        generate_invoices_for_society(self.society, period=current_period())
        self.invoice = Invoice.objects.get(society=self.society)

    def test_generate_link_opens_a_pending_intent(self):
        response = api_client(self.resident).post(
            reverse('payments:upi-generate-link'),
            {'invoice_id': self.invoice.pk, 'amount': '500.00'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.json())
        data = response.json()['data']
        self.assertTrue(data['upi_link'].startswith('upi://pay?'))
        self.assertEqual(data['amount'], '500.00')

        payment = PaymentRecord.objects.get(pk=data['payment_record_id'])
        self.assertEqual(payment.status, PaymentRecord.PaymentStatus.PENDING)
        self.assertEqual(payment.user_id, self.resident.id)

    def test_amount_cannot_exceed_the_outstanding_balance(self):
        response = api_client(self.resident).post(
            reverse('payments:upi-generate-link'),
            {'invoice_id': self.invoice.pk, 'amount': '99999.00'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount', response.json()['error']['details'])

    def test_zero_amount_is_rejected_not_promoted_to_the_full_balance(self):
        response = api_client(self.resident).post(
            reverse('payments:upi-generate-link'),
            {'invoice_id': self.invoice.pk, 'amount': '0.00'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount', response.json()['error']['details'])

    def test_settled_invoice_refuses_a_new_intent(self):
        invoice = Invoice.objects.create(
            society=self.society, unit=self.invoice.unit, resident=self.resident,
            invoice_number='INV-PAID-ONCE', billing_period='2026-09', base_amount=Decimal('1000.00'),
            total_amount=Decimal('1000.00'), paid_amount=Decimal('1000.00'),
            status=Invoice.InvoiceStatus.PAID, due_date=self.invoice.due_date,
        )
        response = api_client(self.resident).post(
            reverse('payments:upi-generate-link'), {'invoice_id': invoice.pk},
            content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'ALREADY_PAID')

    def test_another_resident_cannot_pay_this_invoice(self):
        stranger = make_user(self.society, '+91-9800000999', flat='999')
        response = api_client(stranger).post(
            reverse('payments:upi-generate-link'), {'invoice_id': self.invoice.pk},
            content_type='application/json')
        self.assertEqual(response.status_code, 404)


class ManualVerificationTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        make_unit(self.society, 'A', '101', owner_phone=self.resident.phone)
        generate_invoices_for_society(self.society, period=current_period())
        self.invoice = Invoice.objects.get(society=self.society)
        self.client = api_client(self.resident)

    def submit(self, **body):
        return self.client.post(reverse('payments:submit-manual'),
                                {'invoice_id': self.invoice.pk, **body}, content_type='application/json')

    def test_submission_without_any_proof_is_rejected(self):
        response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertIn('utr_number', response.json()['error']['details'])

    def test_malformed_utr_is_rejected(self):
        response = self.submit(utr_number='123')
        self.assertEqual(response.status_code, 400)

    def test_submission_waits_for_verification(self):
        response = self.submit(utr_number='897654321012')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['data']['status'], 'PENDING_VERIFICATION')
        self.assertEqual(PaymentRecord.objects.filter(invoice=self.invoice).count(), 1)

    def test_a_pending_intent_is_completed_not_duplicated(self):
        generated = api_client(self.resident).post(
            reverse('payments:upi-generate-link'), {'invoice_id': self.invoice.pk, 'amount': '1000.00'},
            content_type='application/json').json()['data']
        response = self.submit(utr_number='897654321013')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['data']['id'], generated['payment_record_id'])
        self.assertEqual(PaymentRecord.objects.filter(invoice=self.invoice).count(), 1)

    def test_a_utr_can_only_be_claimed_once(self):
        first = self.submit(utr_number='897654321014')
        self.assertEqual(first.status_code, 201)
        second = self.submit(utr_number='897654321014')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['error']['code'], 'UTR_ALREADY_USED')

    def test_claim_cannot_exceed_the_outstanding_balance(self):
        # The web/mobile forms block overpayments, but the server is the control
        # (gap list in PaymentReceiptForm's header — this closes it).
        response = self.submit(utr_number='897654321015', amount_paid='99999.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount_paid', response.json()['error']['details'])
        self.assertEqual(PaymentRecord.objects.filter(invoice=self.invoice).count(), 0)

    def test_zero_claim_is_rejected_not_promoted_to_the_full_balance(self):
        response = self.submit(utr_number='897654321016', amount_paid='0.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount_paid', response.json()['error']['details'])

    def test_a_partial_claim_is_recorded_as_submitted(self):
        response = self.submit(utr_number='897654321017', amount_paid='500.00')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Decimal(response.json()['data']['amount_paid']), Decimal('500.00'))

    def submit_with_receipt(self, receipt, **body):
        # The test client multipart-encodes SimpleUploadedFile values on its own.
        return self.client.post(
            reverse('payments:submit-manual'),
            {'invoice_id': self.invoice.pk, **body, 'receipt_image': receipt},
        )

    @staticmethod
    def make_png(px):
        # Random pixels barely compress, so the PNG lands near px²×3 bytes —
        # enough to build a valid image that is legitimately over the 5 MB cap.
        buf = io.BytesIO()
        Image.frombytes('RGB', (px, px), os.urandom(px * px * 3)).save(buf, 'PNG')
        return SimpleUploadedFile('receipt.png', buf.getvalue(), content_type='image/png')

    def test_receipt_over_the_size_cap_is_rejected(self):
        # Gap #20 (doc 02 §12): the cap must apply through the loopback port too.
        response = self.submit_with_receipt(self.make_png(1500), utr_number='897654321018')
        self.assertEqual(response.status_code, 400)
        self.assertIn('receipt_image', response.json()['error']['details'])
        self.assertEqual(PaymentRecord.objects.filter(invoice=self.invoice).count(), 0)

    def test_a_screenshot_receipt_without_utr_is_accepted(self):
        response = self.submit_with_receipt(self.make_png(40))
        self.assertEqual(response.status_code, 201, response.json())
        payment = PaymentRecord.objects.get(invoice=self.invoice)
        self.assertIsNotNone(payment.receipt_image)
        self.assertEqual(payment.status, PaymentRecord.PaymentStatus.PENDING_VERIFICATION)


class VerifyPaymentTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        make_unit(self.society, 'A', '101', owner_phone=self.resident.phone)
        generate_invoices_for_society(self.society, period=current_period())
        self.invoice = Invoice.objects.get(society=self.society)
        self.payment = PaymentRecord.objects.create(
            invoice=self.invoice, user=self.resident, amount=self.invoice.total_amount,
            method=PaymentRecord.PaymentMethod.MANUAL,
            status=PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
            utr_number='897654321099',
        )

    def test_approval_posts_the_money_and_audits_the_approver(self):
        response = api_client(self.treasurer).post(
            reverse('payments:verify', args=[self.payment.pk]),
            {'action': 'approve', 'utr_number': '897654321099'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()['data']
        self.assertEqual(data['status'], 'SUCCESS')
        self.assertEqual(data['invoice_status'], 'PAID')
        self.assertTrue(data['verified_by'])

        self.payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertIsNotNone(self.payment.verified_by_id)
        self.assertIsNotNone(self.payment.verified_at)
        self.assertEqual(self.invoice.paid_amount, self.invoice.total_amount)
        self.assertEqual(self.invoice.outstanding_amount, Decimal('0'))

    def test_rejection_needs_a_reason_and_leaves_the_bill_open(self):
        client = api_client(self.treasurer)
        without_note = client.post(reverse('payments:verify', args=[self.payment.pk]),
                                   {'action': 'reject'}, content_type='application/json')
        self.assertEqual(without_note.status_code, 400)

        rejected = client.post(reverse('payments:verify', args=[self.payment.pk]),
                               {'action': 'reject', 'note': 'UTR does not match the bank statement'},
                               content_type='application/json')
        self.assertEqual(rejected.status_code, 200, rejected.json())
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentRecord.PaymentStatus.REJECTED)
        self.assertIn('UTR', self.payment.verification_note)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('0'))

    def test_a_verified_payment_cannot_be_re_verified(self):
        client = api_client(self.treasurer)
        client.post(reverse('payments:verify', args=[self.payment.pk]),
                    {'action': 'approve', 'utr_number': '897654321099'}, content_type='application/json')
        again = client.post(reverse('payments:verify', args=[self.payment.pk]),
                            {'action': 'approve'}, content_type='application/json')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()['error']['code'], 'ALREADY_VERIFIED')

    def test_residents_cannot_verify_payments(self):
        response = api_client(self.resident).post(
            reverse('payments:verify', args=[self.payment.pk]),
            {'action': 'approve'}, content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_other_tenants_cannot_even_see_the_payment(self):
        foreign = make_society('other-ten', 'Other Ten')
        outsider = make_user(foreign, '+91-9800001000', CustomUser.UserRole.RWA_TREASURER, flat=None)
        response = api_client(outsider).post(
            reverse('payments:verify', args=[self.payment.pk]),
            {'action': 'approve'}, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_pending_count_only_covers_the_callers_tenant(self):
        response = api_client(self.treasurer).get(reverse('payments:pending-count'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['pending_verification'], 1)


class PaymentListScopingTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        mine = make_unit(self.society, 'A', '101', owner_phone=self.resident.phone)
        theirs = make_unit(self.society, 'A', '102', owner_phone='+91-9800000600')
        other_resident = make_user(self.society, '+91-9800000600', flat='102')
        generate_invoices_for_society(self.society, period=current_period())

        for unit, who in ((mine, self.resident), (theirs, other_resident)):
            invoice = Invoice.objects.get(society=self.society, unit=unit)
            PaymentRecord.objects.create(
                invoice=invoice, user=who, amount=Decimal('100.00'),
                method=PaymentRecord.PaymentMethod.MANUAL,
                status=PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
                utr_number=f'89000{invoice.pk:07d}',
            )

    def test_resident_sees_only_their_own_payments(self):
        rows = api_client(self.resident).get(reverse('payments:list')).json()['data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['submitted_by']['id'], self.resident.id)

    def test_treasurer_sees_the_whole_tenant_queue(self):
        rows = api_client(self.treasurer).get(reverse('payments:list')).json()['data']
        self.assertEqual(len(rows), 2)

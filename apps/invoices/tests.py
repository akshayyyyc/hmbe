"""Billing engine tests: idempotency, period-derived due dates, surcharge, payment posting."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.testing import api_client, committee, make_society, make_unit, make_user

from .models import Invoice
from .services import (
    apply_payment_to_invoice,
    current_period,
    generate_invoices_for_society,
    mark_overdue_invoices,
)


class GenerationTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.owner = make_user(self.society, '+91-9800000201', flat='101')
        self.unit = make_unit(self.society, 'A', '101', owner_phone=self.owner.phone)

    def test_rerunning_a_period_never_double_bills(self):
        first = generate_invoices_for_society(self.society, period='2026-11')
        self.assertEqual(first['created'], 1)
        second = generate_invoices_for_society(self.society, period='2026-11')
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(Invoice.objects.filter(society=self.society).count(), 1)

    def test_due_date_follows_the_billing_period_not_the_run_date(self):
        """Regression: backfilling October used to hand the bill a November deadline."""
        generate_invoices_for_society(self.society, period='2026-10')
        invoice = Invoice.objects.get(society=self.society, billing_period='2026-10')
        self.assertEqual(invoice.due_date, date(2026, 10, invoice.due_date.day))
        self.assertLess(invoice.due_date, date(2026, 11, 1))

    def test_invoice_number_carries_the_building(self):
        """A-101 and B-101 are different flats; their numbers must not collide."""
        make_unit(self.society, 'B', '101', owner_phone='+91-9800000202')
        generate_invoices_for_society(self.society, period='2026-11')
        numbers = sorted(Invoice.objects.values_list('invoice_number', flat=True))
        self.assertEqual(len(numbers), len(set(numbers)), numbers)
        self.assertTrue(any(number.endswith('-A101') for number in numbers))
        self.assertTrue(any(number.endswith('-B101') for number in numbers))

    def test_rented_flat_pays_the_non_occupancy_surcharge(self):
        make_unit(self.society, 'C', '501', 5, is_rented=True, base_maintenance=Decimal('3000.00'),
                  tenant_phone='+91-9800000203', tenant_name='Tenant',
                  owner_phone='+91-9800000204')
        generate_invoices_for_society(self.society, period='2026-11')
        invoice = Invoice.objects.get(society=self.society, unit__building='C')
        self.assertEqual(invoice.non_occupancy_surcharge, Decimal('300.00'))
        self.assertEqual(invoice.total_amount, invoice.base_amount + Decimal('300.00'))

    def test_soft_deleted_units_are_not_billed(self):
        self.unit.soft_delete()
        result = generate_invoices_for_society(self.society, period='2026-11')
        self.assertEqual(result['created'], 0)

    def test_invoice_is_linked_to_the_account_that_holds_the_flat(self):
        generate_invoices_for_society(self.society, period='2026-11')
        invoice = Invoice.objects.get(society=self.society)
        self.assertEqual(invoice.resident_id, self.owner.id)


class StatusTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.unit = make_unit(self.society)
        generate_invoices_for_society(self.society, period='2026-10')
        self.invoice = Invoice.objects.get(society=self.society)

    def test_past_due_unpaid_invoices_become_overdue(self):
        self.invoice.due_date = date(2026, 10, 10)
        self.invoice.save(update_fields=['due_date'])
        flipped = mark_overdue_invoices(as_of=date(2026, 11, 1))
        self.assertEqual(flipped, 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.OVERDUE)

    def test_paid_invoices_are_not_flipped_back(self):
        apply_payment_to_invoice(self.invoice, self.invoice.total_amount)
        self.invoice.refresh_from_db()
        self.invoice.due_date = date(2026, 10, 1)
        self.invoice.save(update_fields=['due_date'])
        self.assertEqual(mark_overdue_invoices(as_of=date(2026, 11, 1)), 0)

    def test_part_payment_marks_partial_and_keeps_the_balance(self):
        apply_payment_to_invoice(self.invoice, Decimal('500.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PARTIAL)
        self.assertEqual(self.invoice.outstanding_amount, Decimal('1499.00'))

    def test_full_payment_marks_paid_and_stops_the_balance_growing_negative(self):
        apply_payment_to_invoice(self.invoice, self.invoice.total_amount)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PAID)
        apply_payment_to_invoice(self.invoice, Decimal('10.00'))
        self.invoice.refresh_from_db()
        self.assertGreaterEqual(self.invoice.outstanding_amount, Decimal('0'))


class InvoiceEndpointTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        self.own = make_unit(self.society, 'A', '101', owner_phone=self.resident.phone)
        self.other = make_unit(self.society, 'A', '102', owner_phone='+91-9800000777')
        generate_invoices_for_society(self.society, period=current_period())

    def test_resident_only_sees_their_own_bills(self):
        response = api_client(self.resident).get(reverse('invoices:list'))
        self.assertEqual(response.status_code, 200)
        rows = response.json()['data']
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['unit'], self.own.pk)

    def test_committee_sees_the_whole_tenant(self):
        response = api_client(self.treasurer).get(reverse('invoices:list'))
        self.assertGreaterEqual(len(response.json()['data']), 2)

    def test_another_society_cannot_read_this_invoice(self):
        foreign = make_society('other-town', 'Other Town')
        outsider = make_user(foreign, '+91-9800000888')
        invoice = Invoice.objects.filter(society=self.society).first()
        response = api_client(outsider).get(reverse('invoices:detail', args=[invoice.pk]))
        self.assertEqual(response.status_code, 404)

    def test_generate_endpoint_is_rwa_only(self):
        response = api_client(self.resident).post(reverse('invoices:generate'), {},
                                                  content_type='application/json')
        self.assertEqual(response.status_code, 403)

        response = api_client(self.treasurer).post(reverse('invoices:generate'),
                                                   {'billing_period': '2026-12'},
                                                   content_type='application/json')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['data']['created'], 2)

"""Unit registry tests: tenant scoping, soft delete, CSV import, contact masking."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.testing import api_client, committee, make_society, make_unit, make_user

from .models import Unit, Vehicle
from .services import bulk_import_units, parse_upload
from .serializers import UnitWriteSerializer


class UnitSerializerTests(TestCase):
    def setUp(self):
        self.society = make_society()

    def test_phone_is_normalised_on_write(self):
        serializer = UnitWriteSerializer(
            data={'building': 'A', 'floor': 1, 'unit_number': '101', 'unit_type': '2BHK',
                  'owner_name': 'Owner', 'owner_phone': '98765 43210'},
            context={'society': self.society},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['owner_phone'], '+91-9876543210')

    def test_occupancy_change_keeps_is_rented_in_step(self):
        serializer = UnitWriteSerializer(
            data={'building': 'A', 'floor': 1, 'unit_number': '101', 'unit_type': '2BHK',
                  'owner_name': 'Owner', 'owner_phone': '9876543210',
                  'occupancy_type': 'RENTED', 'tenant_name': 'Tenant', 'tenant_phone': '9876500000'},
            context={'society': self.society},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        unit = serializer.save(society=self.society)
        self.assertTrue(unit.is_rented)

    def test_rented_unit_needs_tenant_contacts(self):
        serializer = UnitWriteSerializer(
            data={'building': 'A', 'floor': 1, 'unit_number': '101', 'unit_type': '2BHK',
                  'owner_name': 'Owner', 'owner_phone': '9876543210', 'occupancy_type': 'RENTED'},
            context={'society': self.society},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('tenant_phone', serializer.errors)


class SoftDeleteTests(TestCase):
    """A removed flat must be re-registerable — invoices have to survive."""

    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)

    def test_soft_delete_keeps_history_and_frees_the_unit_number(self):
        unit = make_unit(self.society, 'B', '301', 3)
        client = api_client(self.treasurer)

        response = client.delete(reverse('units:detail', args=[unit.pk]))
        self.assertEqual(response.status_code, 200, response.json())
        unit.refresh_from_db()
        self.assertTrue(unit.is_deleted)
        self.assertIsNotNone(unit.deleted_at)

        again = client.post(reverse('units:collection'),
                            {'building': 'B', 'floor': 3, 'unit_number': '301', 'unit_type': '3BHK',
                             'owner_name': 'New Owner', 'owner_phone': '9876543211'},
                            content_type='application/json')
        self.assertEqual(again.status_code, 201, again.json())
        self.assertEqual(Unit.objects.filter(society=self.society, building='B', unit_number='301').count(), 2)
        self.assertEqual(Unit.objects.filter(society=self.society, is_deleted=False,
                                             building='B', unit_number='301').count(), 1)

    def test_unit_with_open_invoices_cannot_be_removed(self):
        from apps.invoices.services import generate_invoices_for_society

        unit = make_unit(self.society, 'C', '401', 4)
        generate_invoices_for_society(self.society)
        client = api_client(self.treasurer)
        response = client.delete(reverse('units:detail', args=[unit.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'UNIT_HAS_ACTIVE_INVOICES')

    def test_resident_cannot_register_units(self):
        client = api_client(self.resident)
        response = client.post(reverse('units:collection'),
                               {'building': 'A', 'floor': 1, 'unit_number': '999', 'unit_type': '1BHK',
                                'owner_name': 'Nobody', 'owner_phone': '9876543212'},
                               content_type='application/json')
        self.assertEqual(response.status_code, 403)


class CsvImportTests(TestCase):
    def setUp(self):
        self.society = make_society()

    def upload(self, text):
        class FakeUpload:
            name = 'units.csv'

            def read(self):
                return text.encode()

        return parse_upload(FakeUpload())

    def test_template_columns_parse_into_rows(self):
        rows = self.upload(
            'building,floor,unit_number,unit_type,area_sqft,occupancy_type,base_maintenance,'
            'owner_name,owner_phone,owner_email,parking_number\n'
            'A,1,101,2BHK,950.00,OWNER_OCCUPIED,2000,One Owner,9876543210,a@example.com,P-101\n'
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]['owner_phone'], '9876543210')

    def test_missing_floor_column_is_reported_before_any_row_runs(self):
        with self.assertRaises(Exception) as ctx:
            self.upload('building,unit_number,unit_type,owner_name,owner_phone\nA,101,2BHK,One,9876543210\n')
        self.assertIn('floor', str(ctx.exception))

    def test_one_bad_row_does_not_lose_the_spreadsheet(self):
        rows = self.upload(
            'building,floor,unit_number,unit_type,owner_name,owner_phone\n'
            'A,1,101,2BHK,Good Owner,9876543210\n'
            'A,1,102,2BHK,Bad Owner,\n'
        )
        result = bulk_import_units(self.society, rows, UnitWriteSerializer)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['failed'], 1)
        error = result['errors'][0]
        self.assertEqual(error['row'], 3)
        self.assertIn('owner_phone', error['error'], 'the row error must name the field')

    def test_imported_rows_belong_to_the_calling_tenant_only(self):
        other = make_society('blue-heights', 'Blue Heights')
        rows = self.upload('building,floor,unit_number,unit_type,owner_name,owner_phone\n'
                           'A,1,101,2BHK,Owner,9876543210\n')
        bulk_import_units(other, rows, UnitWriteSerializer)
        self.assertEqual(Unit.objects.filter(society=other).count(), 1)
        self.assertEqual(Unit.objects.filter(society=self.society).count(), 0)


class ContactMaskingTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        self.unit = make_unit(self.society, 'A', '101', owner_phone='+91-9800000201',
                              owner_email='owner@example.com')

    def rows(self, user):
        response = api_client(user).get(reverse('units:collection'))
        self.assertEqual(response.status_code, 200)
        return response.json()['data']

    def test_resident_sees_masked_contacts(self):
        row = next(r for r in self.rows(self.resident) if r['id'] == self.unit.pk)
        self.assertTrue(row['owner_phone'].startswith('***'))
        self.assertTrue(row['owner_email'].startswith('o***@'))

    def test_committee_sees_real_contacts(self):
        row = next(r for r in self.rows(self.treasurer) if r['id'] == self.unit.pk)
        self.assertEqual(row['owner_phone'], '+91-9800000201')
        self.assertEqual(row['owner_email'], 'owner@example.com')


class VehicleTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        self.unit = make_unit(self.society)

    def test_vehicle_number_is_normalised_and_scoped_to_the_unit(self):
        client = api_client(self.treasurer)
        response = client.post(reverse('units:vehicles', args=[self.unit.pk]),
                               {'vehicle_type': 'TWO_WHEELER', 'vehicle_number': 'ka 01 ab 1234'},
                               content_type='application/json')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['data']['vehicle_number'], 'KA01AB1234')

    def test_duplicate_vehicle_on_the_same_unit_is_rejected(self):
        Vehicle.objects.create(unit=self.unit, vehicle_type='CAR', vehicle_number='KA01AB1234')
        client = api_client(self.treasurer)
        response = client.post(reverse('units:vehicles', args=[self.unit.pk]),
                               {'vehicle_type': 'TWO_WHEELER', 'vehicle_number': 'ka01ab1234'},
                               content_type='application/json')
        self.assertEqual(response.status_code, 400)


class RegistryCounterTests(TestCase):
    def test_registered_units_ignores_soft_deleted_rows(self):
        society = make_society('counter-test', 'Counter Test')
        make_unit(society, 'A', '101')
        make_unit(society, 'A', '102')
        deleted = make_unit(society, 'A', '103')
        deleted.soft_delete()

        from .services import refresh_registered_units

        refresh_registered_units(society)
        society.refresh_from_db()
        self.assertEqual(society.registered_units, 2)
        self.assertEqual(society.total_units, 10)

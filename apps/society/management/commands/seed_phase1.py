"""Seed a realistic Phase 1 demo dataset (doc 03 §5, doc 04 §15.1).

Run after ``migrate``:

    python manage.py seed_phase1            # creates or tops up the demo tenant
    python manage.py seed_phase1 --reset    # wipe the demo tenant first

Everything is idempotent, so it can be re-run at any point in the dev loop.

Refuses to run while ``DEBUG`` is off unless ``--yes`` is passed, so a deploy
shell-walk cannot drop a demo society into a live tenant database.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.chat.models import ChatMessage
from apps.chat.services import room_for_user
from apps.invoices.models import Invoice
from apps.invoices.services import current_period, generate_invoices_for_society, mark_overdue_invoices
from apps.notices.models import Notice
from apps.payments.models import PaymentRecord
from apps.society.models import Society, SocietySubscription
from apps.units.models import Unit, Vehicle
from apps.users.models import CustomUser

DEMO_SUBDOMAIN = 'sunrise'
DEV_PASSWORD = 'HomeManager@123'

BUILDINGS = ['A', 'B']
FLOORS = [1, 2, 3]
UNITS_PER_FLOOR = 4

# The demo committee gets real flats, so their invoices link to their accounts
# and the resident/treasurer dashboards have something to show.
UNIT_LINKS = [('A', '101', 'president'), ('A', '102', 'secretary'),
              ('A', '201', 'treasurer'), ('A', '202', 'resident')]

FIRST_NAMES = [
    'Anita Desai', 'Rakesh Iyer', 'Meera Nair', 'Suresh Rao', 'Fatima Sheikh', 'Vikram Chandar',
    'Pri Menon', 'Joseph Dsouza', 'Kavitha Reddy', 'Imran Qureshi', 'Sanjay Bhatia', 'Leela Krishnan',
    'Arjun Pillai', 'Nisha Verma', 'Gaurav Sethi', 'Bina Joshi', 'Tarun Malhotra', 'Reshma Hakki',
    'Dev ANand', 'Shalini Kulkarni', 'Mohit Bansal', 'Jaya Prakash', 'Ravi Teja', 'Uma Shankar',
]


class Command(BaseCommand):
    help = 'Seed Phase 1 demo data: society, users, units, vehicles, notices, invoices, payments'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete the demo society and its data first')
        parser.add_argument('--yes', action='store_true',
                            help='Allow seeding when DEBUG is off (staging only — never a live tenant DB)')

    @transaction.atomic
    def handle(self, *args, **options):
        # A demo seeder is trivially runnable against production by whoever is
        # shell-walking the deploy. The cost of an accidental "Sunrise Residency"
        # in front of real residents is a support incident, so the guard is here
        # rather than in a doc someone has to remember.
        if not settings.DEBUG and not options['yes']:
            raise CommandError(
                'seed_phase1 is a development tool and this process has DEBUG=False. '
                'Re-run with --yes only against a staging database.'
            )

        if options['reset']:
            deleted, _ = Society.objects.filter(subdomain=DEMO_SUBDOMAIN).delete()
            self.stdout.write(self.style.WARNING(f'Removed {deleted} demo record(s).'))

        society = self._society()
        users = self._users(society)
        units = self._units(society)
        self._link_users(society, users)
        self._vehicles(units)
        self._notices(society, users['president'])
        self._invoices(society, units)
        self._chat(society, users)

        self.stdout.write(self.style.SUCCESS(f'\nDemo tenant ready: {society.name} ({society.subdomain})'))
        self.stdout.write(f'  Units: {Unit.objects.filter(society=society).count()}   '
                          f'Users: {CustomUser.objects.filter(society=society).count()}   '
                          f'Invoices: {Invoice.objects.filter(society=society).count()}')
        self.stdout.write('  Sign in with any phone below, then OTP (dev logs the OTP to the console):')
        for label, user in users.items():
            self.stdout.write(f'    {label:<12} {user.phone}  role={user.role}')
        self.stdout.write(f'  Django admin superuser: create with `python manage.py createsuperuser`')

    # ── pieces ────────────────────────────────────────────────────────────

    def _society(self):
        society, created = Society.objects.get_or_create(
            subdomain=DEMO_SUBDOMAIN,
            defaults={
                'name': 'Sunrise Residency',
                'address': '123 Main Street, Indiranagar',
                'pincode': '560038',
                'city': 'Bangalore',
                'state': 'Karnataka',
                'total_units': len(BUILDINGS) * len(FLOORS) * UNITS_PER_FLOOR,
                'president_name': 'Anita Desai',
                'president_phone': '+91-9800000001',
                'president_email': 'president@sunrise.example',
            },
        )
        if created:
            society.activate_trial()
            SocietySubscription.objects.create(
                society=society,
                tier=society.subscription_tier,
                base_amount=Decimal('1999.00'),
                status=SocietySubscription.PaymentStatus.PENDING,
                invoice_date=date.today(),
                due_date=society.trial_end_date,
            )
            self.stdout.write(f'Created society {society.name}')
        return society

    def _make_user(self, society, phone, first, role, *, flat=None, status=CustomUser.ResidentStatus.VERIFIED):
        first_name, *rest = first.split()
        user, created = CustomUser.objects.get_or_create(
            phone=phone,
            defaults={
                'username': phone,
                'first_name': first_name,
                'last_name': ' '.join(rest),
                'email': f"{first_name.lower()}@sunrise.example",
                'role': role,
                'society': society,
                'flat_number': flat,
                'resident_status': status,
                'is_phone_verified': True,
            },
        )
        if created:
            user.set_password(DEV_PASSWORD)
            user.save(update_fields=['password'])
        return user

    def _users(self, society):
        users = {
            'president': self._make_user(society, '+91-9800000001', 'Anita Desai',
                                         CustomUser.UserRole.RWA_PRESIDENT, flat='101'),
            'secretary': self._make_user(society, '+91-9800000002', 'Rakesh Iyer',
                                         CustomUser.UserRole.RWA_SECRETARY, flat='102'),
            'treasurer': self._make_user(society, '+91-9800000003', 'Meera Nair',
                                         CustomUser.UserRole.RWA_TREASURER, flat='201'),
            'resident': self._make_user(society, '+91-9800000010', 'Suresh Rao',
                                        CustomUser.UserRole.RESIDENT, flat='202'),
            'guard': self._make_user(society, '+91-9800000011', 'Vikram Chandar',
                                     CustomUser.UserRole.SECURITY_GUARD, flat=None),
        }
        return users

    def _link_users(self, society, users):
        """Point four units at the demo accounts and re-point their invoices."""
        for building, unit_number, key in UNIT_LINKS:
            user = users[key]
            unit = Unit.objects.filter(society=society, building=building, unit_number=unit_number).first()
            if not unit:
                continue
            if unit.owner_phone != user.phone:
                unit.owner_phone = user.phone
                unit.owner_name = user.get_full_name() or unit.owner_name
                unit.occupancy_type = Unit.OccupancyType.OWNER_OCCUPIED
                unit.is_rented = False
                unit.tenant_name = ''
                unit.tenant_phone = ''
                unit.save(update_fields=['owner_phone', 'owner_name', 'occupancy_type', 'is_rented',
                                         'tenant_name', 'tenant_phone', 'updated_at'])
            Invoice.objects.filter(unit=unit, resident__isnull=True).update(resident=user)
        linked = Invoice.objects.filter(society=society, resident__isnull=False).count()
        self.stdout.write(f'Invoices linked to accounts: {linked}')

    def _units(self, society):
        units = []
        names = list(FIRST_NAMES)
        random.seed(7)  # stable demo data across runs
        index = 0
        phone_seq = 20

        for building in BUILDINGS:
            for floor in FLOORS:
                for slot in range(1, UNITS_PER_FLOOR + 1):
                    unit_number = f'{floor}{slot:02d}'
                    name = names[index % len(names)]
                    index += 1
                    phone = f'+91-980000{phone_seq:04d}'
                    phone_seq += 1
                    rented = index % 4 == 0

                    unit, _ = Unit.objects.get_or_create(
                        society=society,
                        building=building,
                        unit_number=unit_number,
                        defaults={
                            'floor': floor,
                            'unit_type': random.choice(['1BHK', '2BHK', '2BHK', '3BHK']),
                            'area_sqft': Decimal(random.choice([650, 850, 950, 1250])),
                            'occupancy_type': Unit.OccupancyType.RENTED if rented else Unit.OccupancyType.OWNER_OCCUPIED,
                            'is_rented': rented,
                            'base_maintenance': Decimal('2000.00'),
                            'owner_name': name,
                            'owner_phone': phone,
                            'owner_email': f'{name.split()[0].lower()}{index}@example.com',
                            'tenant_name': f'{name.split()[0]} Tenant' if rented else '',
                            'tenant_phone': f'+91-981110{phone_seq:04d}' if rented else '',
                            'parking_number': f'P-{building}{unit_number}',
                        },
                    )
                    units.append(unit)

        society.registered_units = len(units)
        society.save(update_fields=['registered_units', 'updated_at'])
        self.stdout.write(f'Units registered: {len(units)}')
        return units

    def _vehicles(self, units):
        created = 0
        for i, unit in enumerate(units):
            if i % 3:
                continue
            number = f'KA01AB{1000 + i:04d}'
            _, made = Vehicle.objects.get_or_create(
                unit=unit,
                vehicle_number=number,
                defaults={
                    'vehicle_type': Vehicle.VehicleType.FOUR_WHEELER if i % 6 == 0 else Vehicle.VehicleType.TWO_WHEELER,
                    'color': 'White',
                    'is_primary': True,
                },
            )
            created += made
        self.stdout.write(f'Vehicles added: {created}')

    def _notices(self, society, president):
        today = date.today()
        samples = [
            {
                'title': 'Water tank cleaning — supply on the 10th, 10 AM to 2 PM',
                'content': ('The overhead tank is cleaned on the second Saturday of every month. '
                            'Please store water in advance. Borewell backup will run from 2 PM.'),
                'category': Notice.NoticeCategory.GENERAL,
                'is_pinned': True,
                'expires_at': timezone.now() + timedelta(days=10),
            },
            {
                'title': 'Annual General Meeting — save the date',
                'content': ('AGM at the community hall at 6 PM. Agenda: maintenance budget revision, '
                            'parking re-allotment, and the sinking fund statement.'),
                'category': Notice.NoticeCategory.MEETING,
                'is_pinned': False,
                'expires_at': timezone.now() + timedelta(days=21),
            },
            {
                'title': 'Guest parking policy update',
                'content': ('Visitors must park in the marked bay near the gate. Overnight guest parking '
                            'needs the security desk to log the vehicle number.'),
                'category': Notice.NoticeCategory.POLICY,
                'is_pinned': False,
                'expires_at': None,
            },
        ]
        made = 0
        for sample in samples:
            _, created = Notice.objects.get_or_create(
                society=society, title=sample['title'],
                defaults={**sample, 'created_by': president},
            )
            made += created
        self.stdout.write(f'Notices added: {made}')

    def _invoices(self, society, units):
        period = current_period()
        generate_invoices_for_society(society, period=period, actor='seed')

        # A settled month gives the treasurer dashboard something to show.
        previous = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        if not Invoice.objects.filter(society=society, billing_period=previous).exists():
            generate_invoices_for_society(society, period=previous, actor='seed')
            for invoice in Invoice.objects.filter(society=society, billing_period=previous)[:12]:
                invoice.paid_amount = invoice.total_amount
                invoice.paid_date = invoice.due_date
                invoice.status = Invoice.InvoiceStatus.PAID
                invoice.payment_method = PaymentRecord.PaymentMethod.UPI_INTENT
                invoice.utr_number = f'{700000000000 + invoice.id:012d}'
                invoice.save(update_fields=['paid_amount', 'paid_date', 'status', 'payment_method', 'utr_number'])
                PaymentRecord.objects.get_or_create(
                    invoice=invoice,
                    defaults={
                        'user': CustomUser.objects.filter(
                            society=society, phone=invoice.unit.owner_phone
                        ).first(),
                        'amount': invoice.total_amount,
                        'method': PaymentRecord.PaymentMethod.UPI_INTENT,
                        'status': PaymentRecord.PaymentStatus.SUCCESS,
                        'utr_number': invoice.utr_number,
                        'verified_by': CustomUser.objects.filter(society=society, role='RWA_TREASURER').first(),
                        'verified_at': timezone.now() - timedelta(days=20),
                    },
                )

        # Leave the treasurer a review queue so the MVR flow is demonstrable.
        open_invoice = Invoice.objects.filter(
            society=society, billing_period=period, status=Invoice.InvoiceStatus.GENERATED
        ).first()
        if open_invoice:
            PaymentRecord.objects.get_or_create(
                invoice=open_invoice,
                utr_number=f'{810000000001 + open_invoice.id:012d}',
                defaults={
                    'user': CustomUser.objects.filter(society=society, role='RESIDENT').first(),
                    'amount': open_invoice.total_amount,
                    'method': PaymentRecord.PaymentMethod.MANUAL,
                    'status': PaymentRecord.PaymentStatus.PENDING_VERIFICATION,
                },
            )

        # Run last so the older unpaid invoices land in the right state.
        overdue = mark_overdue_invoices()
        self.stdout.write(f'Invoices for {period} and {previous} generated; overdue flagged: {overdue}')

    def _chat(self, society, users):
        room = room_for_user(users['resident'])
        if room.messages.exists():
            return
        for text, user in [
            ('Lift in B wing is out again — maintenance has been informed.', users['secretary']),
            ('Thanks for flagging. Engineer is coming at 4 PM today.', users['president']),
            ('Reminder: society meeting on Sunday in the community hall.', users['treasurer']),
        ]:
            ChatMessage.objects.create(room=room, sender=user, content=text)
        self.stdout.write('Chat seeded with 3 messages')

"""Create a demo society for Phase 1 trial / development."""

from django.core.management.base import BaseCommand
from apps.society.models import Society


class Command(BaseCommand):
    help = 'Seed Phase 1 demo data (society + trial)'

    def handle(self, *args, **options):
        society, created = Society.objects.get_or_create(
            subdomain='demo',
            defaults={
                'name': 'Sunrise Residency Demo',
                'address': '123 Main Street, Bangalore',
                'pincode': '560001',
                'city': 'Bangalore',
                'state': 'Karnataka',
                'total_units': 50,
                'president_name': 'John Doe',
                'president_phone': '+91-9876543210',
                'president_email': 'john@example.com',
            },
        )
        if created:
            society.activate_trial()
            self.stdout.write(
                self.style.SUCCESS(f'Created demo society: {society.name}')
            )
        else:
            self.stdout.write(self.style.WARNING('Demo society already exists.'))

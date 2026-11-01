"""Monthly billing cron (doc 04 §15.2) — run on the 1st of each month.

Crontab on the VPS:
    0 6 1 * * cd /srv/homemanager && docker compose exec -T backend \
        python manage.py generate_monthly_invoices
"""

from django.core.management.base import BaseCommand

from apps.invoices.services import current_period, generate_invoices_for_society, mark_overdue_invoices
from apps.society.models import Society


class Command(BaseCommand):
    help = 'Generate flat-rate maintenance invoices for every active society, then flag overdue ones.'

    def add_arguments(self, parser):
        parser.add_argument('--period', help='Billing period as YYYY-MM (defaults to the current month)')
        parser.add_argument('--society', help='Subdomain of a single society (defaults to all)')
        parser.add_argument(
            '--include-suspended', action='store_true',
            help='Also bill suspended societies (default: trial and active only)',
        )

    def handle(self, *args, **options):
        period = options['period'] or current_period()

        societies = Society.objects.exclude(subscription_status=Society.OnboardingStatus.SUSPENDED)
        if options['include_suspended']:
            societies = Society.objects.all()
        if options['society']:
            societies = societies.filter(subdomain=options['society'])

        totals = {'created': 0, 'skipped': 0}
        for society in societies.order_by('id'):
            result = generate_invoices_for_society(society, period=period, actor='cron')
            totals['created'] += result['created']
            totals['skipped'] += result['skipped']
            self.stdout.write(f'{society.subdomain}: created {result["created"]}, skipped {result["skipped"]}')

        overdue = mark_overdue_invoices()
        self.stdout.write(self.style.SUCCESS(
            f'Period {period}: {totals["created"]} invoice(s) created, {totals["skipped"]} skipped, '
            f'{overdue} marked overdue.'
        ))

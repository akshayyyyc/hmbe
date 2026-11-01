"""Society domain services."""

import re

from .models import Society

SUBDOMAIN_MAX_LENGTH = 100


def generate_subdomain(name):
    """Derive a unique subdomain from a society name.

    ``'Sunrise Residency'`` → ``'sunrise-residency'``; collisions get numeric
    suffixes (``-2``, ``-3``). Subdomains are the public handle residents type
    into during onboarding, so they stay lowercase, ASCII and URL-safe.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower().strip()).strip('-')
    slug = slug[:SUBDOMAIN_MAX_LENGTH].strip('-')
    if not slug:
        slug = 'society'

    base_slug, counter, candidate = slug, 1, slug
    while Society.objects.filter(subdomain=candidate).exists():
        counter += 1
        suffix = f'-{counter}'
        candidate = f'{base_slug[: SUBDOMAIN_MAX_LENGTH - len(suffix)]}{suffix}'
    return candidate

"""Notice board tests: who may post, who may pin, and the auto-archive rule."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import api_client, committee, make_society, make_user

from .models import Notice


def make_notice(society, author, **overrides):
    defaults = {
        'society': society,
        'created_by': author,
        'title': 'Water tank cleaning',
        'content': 'The overhead tank is cleaned on the second Saturday every month.',
        'category': Notice.NoticeCategory.GENERAL,
        'is_pinned': False,
    }
    defaults.update(overrides)
    return Notice.objects.create(**defaults)


class BoardPermissionTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)

    def test_residents_cannot_post_to_the_board(self):
        """Regression: posting used to be open to any society member."""
        response = api_client(self.resident).post(
            reverse('notices:collection'),
            {'title': 'Resident post', 'content': 'I would like to announce something.'},
            content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'PERMISSION_DENIED')

    def test_committee_member_posts_and_pins(self):
        response = api_client(self.treasurer).post(
            reverse('notices:collection'),
            {'title': 'AGM notice', 'content': 'Annual general meeting on Sunday at 6 AM.',
             'category': 'MEETING', 'is_pinned': True},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.json())
        self.assertTrue(response.json()['data']['is_pinned'])

    def test_residents_cannot_pin(self):
        notice = make_notice(self.society, self.president)
        response = api_client(self.resident).post(reverse('notices:pin', args=[notice.pk]))
        self.assertEqual(response.status_code, 403)
        notice.refresh_from_db()
        self.assertFalse(notice.is_pinned)

    def test_pin_toggles_both_ways_for_committee(self):
        notice = make_notice(self.society, self.president, is_pinned=True)
        client = api_client(self.president)
        first = client.post(reverse('notices:pin', args=[notice.pk]))
        self.assertIs(first.json()['data']['is_pinned'], False)
        second = client.post(reverse('notices:pin', args=[notice.pk]))
        self.assertIs(second.json()['data']['is_pinned'], True)

    def test_author_may_edit_their_own_notice(self):
        notice = make_notice(self.society, self.treasurer)
        response = api_client(self.treasurer).patch(
            reverse('notices:detail', args=[notice.pk]), {'title': 'Corrected title'},
            content_type='application/json')
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['data']['title'], 'Corrected title')

    def test_a_different_member_cannot_edit_it(self):
        notice = make_notice(self.society, self.resident)
        response = api_client(self.treasurer).patch(
            reverse('notices:detail', args=[notice.pk]), {'title': 'Hijacked'},
            content_type='application/json')
        self.assertEqual(response.status_code, 200)  # treasurer is RWA admin

        stranger = make_user(self.society, '+91-9800000123', flat='999')
        other_author = make_notice(self.society, stranger, title='Their notice')
        response = api_client(self.resident).delete(reverse('notices:detail', args=[other_author.pk]))
        self.assertEqual(response.status_code, 403)


class BoardListingTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.president, self.treasurer, self.resident = committee(self.society)
        self.other_society = make_society('other-vice', 'Other Vice')
        self.outsider = make_user(self.other_society, '+91-9800000124')

        make_notice(self.society, self.president, title='Live notice')
        make_notice(self.society, self.president, title='Pinned notice', is_pinned=True)
        make_notice(self.society, self.president, title='Expired notice',
                    expires_at=timezone.now() - timedelta(days=1))
        make_notice(self.other_society, self.outsider, title='Elsewhere notice')

    def titles(self, user, query=''):
        response = api_client(user).get(reverse('notices:collection') + query)
        self.assertEqual(response.status_code, 200)
        return [row['title'] for row in response.json()['data']]

    def test_pinned_comes_first_and_expired_is_archived(self):
        self.assertEqual(self.titles(self.resident), ['Pinned notice', 'Live notice'])

    def test_only_the_callers_tenant_is_visible(self):
        self.assertNotIn('Elsewhere notice', self.titles(self.resident))

    def test_committee_can_review_expired_notices(self):
        rows = self.titles(self.treasurer, '?include_expired=true')
        self.assertIn('Expired notice', rows)
        self.assertNotIn('Expired notice', self.titles(self.resident, '?include_expired=true'))

    def test_category_filter(self):
        response = api_client(self.resident).get(reverse('notices:collection') + '?category=MEETING')
        self.assertEqual(response.json()['data'], [])

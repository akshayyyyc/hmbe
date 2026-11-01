"""Cross-cutting contract tests: envelope, pagination, health and tenant isolation."""

from django.conf import settings
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse

from apps.core.client_ip import get_client_ip, is_direct_socket_peer
from apps.core.pagination import ApiPagination
from apps.core.testing import api_client, committee, make_society, make_unit, make_user
from apps.users.models import CustomUser

from .views import HealthCheckView


class EnvelopeTests(TestCase):
    def test_health_reports_dependencies_under_the_envelope(self):
        response = self.client.get(reverse('health-check'))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['database'], 'connected')
        self.assertIn('timestamp', body['data'])

    def test_health_is_served_by_the_core_view_on_both_paths(self):
        from django.urls import resolve

        self.assertIs(resolve('/api/health/').func.view_class, HealthCheckView)
        self.assertIs(resolve('/api/v1/health/').func.view_class, HealthCheckView)

    def test_v1_alias_and_legacy_path_are_the_same_view(self):
        legacy = self.client.get(reverse('health-check')).json()
        v1 = self.client.get(reverse('health-check-v1')).json()
        self.assertEqual(legacy['data']['status'], v1['data']['status'])

    def test_unknown_object_uses_the_error_envelope(self):
        society = make_society()
        _president, treasurer, _resident = committee(society)
        response = api_client(treasurer).get(reverse('units:detail', args=[999999]))
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body['success'])
        self.assertEqual(body['error']['code'], 'UNIT_NOT_FOUND')
        self.assertTrue(body['error']['message'])

    def test_validation_failure_names_the_fields(self):
        society = make_society()
        president, _treasurer, _resident = committee(society)
        response = api_client(president).post(reverse('units:collection'),
                                              {'building': '', 'unit_number': ''},
                                              content_type='application/json')
        self.assertEqual(response.status_code, 400)
        details = response.json()['error']['details']
        self.assertIn('building', details)
        self.assertIn('unit_number', details)

    def test_anonymous_caller_is_rejected(self):
        response = self.client.get(reverse('units:collection'))
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()['success'])


class PaginationTests(TestCase):
    def setUp(self):
        self.society = make_society('paginated', 'Paginated Society')
        _president, treasurer, _resident = committee(self.society)
        for index in range(1, 26):
            make_unit(self.society, 'A', f'{index:03d}', owner_phone=f'+91-980000{index:04d}')
        self.treasurer = treasurer

    def test_default_page_size_and_meta(self):
        body = api_client(self.treasurer).get(reverse('units:collection')).json()
        self.assertEqual(len(body['data']), ApiPagination.page_size)
        self.assertEqual(body['meta'], {'page': 1, 'limit': ApiPagination.page_size,
                                        'total': 25, 'total_pages': 2})

    def test_page_and_limit_are_honoured(self):
        body = api_client(self.treasurer).get(reverse('units:collection') + '?page=2&limit=10').json()
        self.assertEqual(len(body['data']), 10)
        self.assertEqual(body['meta']['page'], 2)
        self.assertEqual(body['meta']['total_pages'], 3)

    def test_beyond_the_last_page_reports_not_found_in_the_envelope(self):
        response = api_client(self.treasurer).get(reverse('units:collection') + '?page=9')
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body['success'])
        self.assertIn('error', body, 'pagination errors must still use the standard envelope')


class TenantIsolationTests(TestCase):
    """Row-level tenancy: the token decides the tenant, never the request body."""

    def setUp(self):
        self.alpha = make_society('alpha', 'Alpha Heights')
        self.beta = make_society('beta', 'Beta Heights')
        self.alpha_pres, self.alpha_treas, self.alpha_res = committee(self.alpha, tag='01')
        self.beta_pres, self.beta_treas, self.beta_res = committee(self.beta, tag='02')
        make_unit(self.alpha, 'A', '101')
        make_unit(self.alpha, 'A', '102')
        make_unit(self.beta, 'B', '101')

    def test_lists_never_cross_tenants(self):
        alpha_rows = api_client(self.alpha_treas).get(reverse('units:collection')).json()['data']
        beta_rows = api_client(self.beta_treas).get(reverse('units:collection')).json()['data']
        self.assertEqual(len(alpha_rows), 2)
        self.assertEqual(len(beta_rows), 1)
        self.assertFalse({r['id'] for r in alpha_rows} & {r['id'] for r in beta_rows})

    def test_detail_of_another_tenants_unit_is_a_404(self):
        victim = make_unit(self.alpha, 'C', '301')
        response = api_client(self.beta_treas).get(reverse('units:detail', args=[victim.pk]))
        self.assertEqual(response.status_code, 404)

    def test_client_supplied_society_is_ignored_on_write(self):
        """A body claiming a different society must not move the row."""
        response = api_client(self.beta_treas).post(
            reverse('units:collection'),
            {'building': 'D', 'floor': 1, 'unit_number': '401', 'unit_type': '1BHK',
             'owner_name': 'Squatter', 'owner_phone': '9876543210', 'society': self.alpha.pk},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.json())
        from apps.units.models import Unit

        row = Unit.objects.get(society=self.beta, building='D', unit_number='401')
        self.assertEqual(row.society_id, self.beta.pk)
        self.assertFalse(Unit.objects.filter(society=self.alpha, building='D').exists())

    def test_user_without_a_society_cannot_reach_tenant_endpoints(self):
        orphan = make_user(None, '+91-9800000321', CustomUser.UserRole.RESIDENT)
        response = api_client(orphan).get(reverse('units:collection'))
        self.assertEqual(response.status_code, 403)

    def test_my_society_resolves_from_the_token(self):
        body = api_client(self.alpha_res).get(reverse('society:me')).json()['data']
        self.assertEqual(body['subdomain'], 'alpha')


class ClientIPTests(TestCase):
    """Who called us: the address the edge recorded, never the one a client sent."""

    def _get(self, **meta):
        return RequestFactory().get('/api/health/', **meta)

    def test_without_an_edge_the_socket_peer_is_the_whole_story(self):
        # settings.PROXY_HOPS is 0 unless BEHIND_PROXY is on, so a header cannot
        # appear out of nowhere in development.
        request = self._get(REMOTE_ADDR='203.0.113.9',
                            HTTP_X_FORWARDED_FOR='1.1.1.1, 2.2.2.2')
        self.assertEqual(get_client_ip(request), '203.0.113.9')
        self.assertTrue(is_direct_socket_peer(request))

    def test_behind_the_edge_the_forged_left_most_value_is_ignored(self):
        # Our edge overwrites XFF, so what reaches Django is the one address it saw.
        # If someone reconfigures it to append, the right-most is still the honest
        # one — the left-most is whatever the caller typed.
        with override_settings(PROXY_HOPS=1):
            request = self._get(REMOTE_ADDR='172.20.0.3',
                                HTTP_X_FORWARDED_FOR='1.1.1.1, 203.0.113.7')
            self.assertEqual(get_client_ip(request), '203.0.113.7')
            self.assertFalse(is_direct_socket_peer(request))

    def test_a_proxy_chain_walks_left_one_address_per_trusted_hop(self):
        with override_settings(PROXY_HOPS=2):
            request = self._get(REMOTE_ADDR='10.0.0.5',
                                HTTP_X_FORWARDED_FOR='203.0.113.7, 198.51.100.4')
            self.assertEqual(get_client_ip(request), '203.0.113.7')

    def test_missing_header_under_an_edge_degrades_to_the_socket_peer(self):
        # An edge that forgets to set X-Forwarded-For must not 500 the request.
        with override_settings(PROXY_HOPS=1):
            self.assertEqual(get_client_ip(self._get(REMOTE_ADDR='172.20.0.3')), '172.20.0.3')

    def test_more_hops_than_addresses_does_not_raise(self):
        with override_settings(PROXY_HOPS=3):
            request = self._get(REMOTE_ADDR='10.0.0.5', HTTP_X_FORWARDED_FOR='203.0.113.7')
            self.assertEqual(get_client_ip(request), '203.0.113.7')

    def test_proxy_hardening_stays_off_in_development(self):
        # Dev has no edge, so nothing may claim https or set Secure-only cookies.
        self.assertFalse(getattr(settings, 'USE_X_FORWARDED_HOST', False))
        self.assertIsNone(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))
        self.assertEqual(settings.PROXY_HOPS, 0)

"""Phone identity, OTP lifecycle and JWT session tests (doc 04 §3, §16)."""

import hashlib
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import api_client, login_payload, make_society, make_user, otp_for

from .models import CustomUser, OTPVerification
from .utils import (
    consume_otp,
    create_otp,
    hash_otp,
    normalize_phone,
    seconds_until_resend_allows,
)


class PhoneNormalisationTests(TestCase):
    """Phone is the identity key, so every spelling must land on one value."""

    def test_every_common_format_collapses_to_the_same_key(self):
        for raw in ('9876543210', '+91 98765 43210', '098765-43210', '91-9876543210',
                    '+919876543210', ' +91 (98765) 43210 '):
            self.assertEqual(normalize_phone(raw), '+91-9876543210', raw)

    def test_invalid_numbers_are_rejected_not_stored(self):
        for raw in ('1234567890', '5876543210', '987654321', '98765432101', '', None, 'abc'):
            self.assertIsNone(normalize_phone(raw), raw)


class OtpLifecycleTests(TestCase):
    def setUp(self):
        self.phone = '+91-9800000111'

    def test_only_the_hash_is_stored(self):
        record, code = create_otp(phone=self.phone, purpose='LOGIN')
        self.assertNotEqual(record.otp, code)
        self.assertEqual(record.otp, hash_otp(code))
        self.assertEqual(record.otp, hashlib.sha256(code.encode()).hexdigest())

    def test_code_is_six_digits_and_random(self):
        codes = {create_otp(phone=self.phone, purpose='LOGIN')[1] for _ in range(5)}
        self.assertTrue(all(len(c) == 6 and c.isdigit() for c in codes))
        self.assertEqual(len(codes), 5, 'OTPs must not repeat')

    def test_correct_code_validates_once_only(self):
        _record, code = create_otp(phone=self.phone, purpose='LOGIN')
        ok, problem = consume_otp(phone=self.phone, otp=code, purpose='LOGIN')
        self.assertTrue(ok, problem)
        reused, problem = consume_otp(phone=self.phone, otp=code, purpose='LOGIN')
        self.assertFalse(reused)
        self.assertEqual(problem[0], 'INVALID_OTP')

    def test_attempt_ceiling_locks_the_code(self):
        from django.conf import settings

        _record, _code = create_otp(phone=self.phone, purpose='LOGIN')
        for _attempt in range(settings.OTP_MAX_ATTEMPTS):
            valid, problem = consume_otp(phone=self.phone, otp='000000', purpose='LOGIN')
        self.assertFalse(valid)
        self.assertEqual(problem[0], 'OTP_MAX_ATTEMPTS')

    def test_expired_code_is_rejected(self):
        record, code = create_otp(phone=self.phone, purpose='LOGIN')
        OTPVerification.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        valid, problem = consume_otp(phone=self.phone, otp=code, purpose='LOGIN')
        self.assertFalse(valid)
        self.assertEqual(problem[0], 'INVALID_OTP')

    def test_purpose_is_part_of_the_key(self):
        _record, code = create_otp(phone=self.phone, purpose='REGISTRATION')
        valid, _problem = consume_otp(phone=self.phone, otp=code, purpose='LOGIN')
        self.assertFalse(valid, 'a REGISTRATION code must not satisfy a LOGIN check')

    def test_resend_cooldown_is_enforced_after_issue(self):
        create_otp(phone=self.phone, purpose='LOGIN')
        self.assertGreater(seconds_until_resend_allows(self.phone), 0)


class AuthEndpointTests(TestCase):
    def setUp(self):
        self.society = make_society()
        self.resident = make_user(self.society, '+91-9800000100')

    def send(self, phone, purpose='LOGIN'):
        return self.client.post(reverse('users_auth:send-otp'),
                                {'phone': phone, 'purpose': purpose}, content_type='application/json')

    def test_send_otp_then_second_send_is_throttled(self):
        first = self.send(self.resident.phone)
        self.assertEqual(first.status_code, 200, first.json())
        self.assertTrue(first.json()['success'])

        second = self.send(self.resident.phone)
        self.assertEqual(second.status_code, 429)
        self.assertGreater(second.json()['data']['retry_after'], 0)

    def test_send_otp_rejects_a_malformed_phone(self):
        response = self.send('12345')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    def test_verify_otp_returns_a_token_pair_and_role_claims(self):
        body = login_payload(self.resident)
        response = self.client.post(reverse('users_auth:verify-otp'), body, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()['data']
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertEqual(data['user']['role'], 'RESIDENT')

    def test_verify_otp_creates_a_new_unassigned_account(self):
        phone = '+91-9812345678'
        body = {'phone': phone, 'otp': otp_for(phone), 'purpose': 'LOGIN'}
        response = self.client.post(reverse('users_auth:verify-otp'), body, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.json())
        created = CustomUser.objects.get(phone=phone)
        self.assertIsNone(created.society_id, 'self-registered users stay UNASSIGNED until a flat matches')

    def test_refresh_rotates_and_burns_the_old_refresh(self):
        """Regression: resolving the user off ``RefreshToken.user`` broke every refresh."""
        data = login_payload(self.resident)
        response = self.client.post(reverse('users_auth:verify-otp'), data, content_type='application/json')
        refresh = response.json()['data']['refresh']

        rotated = self.client.post(reverse('users_auth:refresh-token'), {'refresh': refresh},
                                   content_type='application/json')
        self.assertEqual(rotated.status_code, 200, rotated.json())
        new = rotated.json()['data']
        self.assertNotEqual(new['refresh'], refresh)

        replay = self.client.post(reverse('users_auth:refresh-token'), {'refresh': refresh},
                                  content_type='application/json')
        self.assertEqual(replay.status_code, 401)

        logout = self.client.post(reverse('users_auth:logout'), {'refresh': new['refresh']},
                                  content_type='application/json',
                                  HTTP_AUTHORIZATION=f"Bearer {new['access']}")
        self.assertEqual(logout.status_code, 200, logout.json())
        after = self.client.post(reverse('users_auth:refresh-token'), {'refresh': new['refresh']},
                                 content_type='application/json')
        self.assertEqual(after.status_code, 401, 'logout must revoke the session')

    def test_me_exposes_the_computed_role_flags(self):
        """Regression: ``ReadOnlyField(source=<same name>)`` crashed this response."""
        client = api_client(self.resident)
        response = client.get(reverse('users:me'))
        self.assertEqual(response.status_code, 200, response)
        data = response.json()['data']
        self.assertEqual(data['phone'], self.resident.phone)
        self.assertIs(data['is_rwa_admin'], False)
        self.assertIs(data['is_resident_or_landlord'], True)

    def test_phone_cannot_be_self_edited(self):
        client = api_client(self.resident)
        response = client.patch(reverse('users:me-update'), {'phone': '+91-9999999999'},
                               content_type='application/json')
        self.assertEqual(response.status_code, 403)

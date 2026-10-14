"""
Tests for Task 1 (signup verification emails, callback, resend with cooldown)
and Task 2 (password eye toggle accessibility and structure).
"""

import os
import time
import pytest
from unittest.mock import MagicMock
from bs4 import BeautifulSoup

import app as app_module
from app import (
    _get_site_url,
    _resend_cooldowns,
    _resend_cooldowns_lock,
    RESEND_COOLDOWN_SECONDS,
)


# ---------------------------------------------------------------------------
# 1. Tests for Site URL Resolution (localhost, 127.0.0.1, and LAN IP)
# ---------------------------------------------------------------------------

def test_get_site_url_with_env_var(monkeypatch):
    """When SITE_URL is explicitly configured, it must be prioritized."""
    monkeypatch.setenv('SITE_URL', 'http://192.168.254.108:5000')
    assert _get_site_url() == 'http://192.168.254.108:5000'

    monkeypatch.setenv('SITE_URL', 'http://192.168.254.108:5000/')
    assert _get_site_url() == 'http://192.168.254.108:5000'


def test_get_site_url_fallback_to_request_host(monkeypatch):
    """When SITE_URL is not set, dynamically fall back to request.host_url."""
    monkeypatch.delenv('SITE_URL', raising=False)
    client = app_module.app.test_client()

    with app_module.app.test_request_context(base_url='http://192.168.254.108:5000/'):
        assert _get_site_url() == 'http://192.168.254.108:5000'

    with app_module.app.test_request_context(base_url='http://127.0.0.1:5000/'):
        assert _get_site_url() == 'http://127.0.0.1:5000'


# ---------------------------------------------------------------------------
# 2. Tests for Signup Route with Email Verification Options
# ---------------------------------------------------------------------------

def test_signup_passes_email_redirect_to(monkeypatch):
    """Signup must pass email_redirect_to with dynamic callback URL to Supabase."""
    monkeypatch.setenv('SITE_URL', 'http://192.168.254.108:5000')

    captured_args = {}

    class FakeAuth:
        def sign_up(self, credentials):
            captured_args.update(credentials)
            mock_user = MagicMock()
            mock_user.id = 'test-uuid-123'
            mock_user.identities = [MagicMock()]
            mock_res = MagicMock()
            mock_res.user = mock_user
            mock_res.session = None  # Requires confirmation
            return mock_res

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{'id': 1}]
    fake_supabase.table.return_value.insert.return_value.execute.return_value.data = []

    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.post('/signup', data={
        'first_name': 'John',
        'last_name': 'Doe',
        'program': 'BSIT',
        'email': 'john.doe@example.com',
        'username': 'johndoe',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })

    assert resp.status_code == 302
    assert '/login' in resp.location
    assert captured_args.get('email') == 'john.doe@example.com'
    assert 'options' in captured_args
    assert captured_args['options'].get('email_redirect_to') == 'http://192.168.254.108:5000/auth/callback'


def test_signup_already_registered_unconfirmed_routes_to_resend(monkeypatch):
    """When Supabase returns empty identities, user is routed to resend verification."""
    class FakeAuth:
        def sign_up(self, credentials):
            mock_user = MagicMock()
            mock_user.id = 'existing-uuid-456'
            mock_user.identities = []  # Empty identities indicates already registered
            mock_res = MagicMock()
            mock_res.user = mock_user
            mock_res.session = None
            return mock_res

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{'id': 1}]

    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.post('/signup', data={
        'first_name': 'Existing',
        'last_name': 'User',
        'program': 'BSIT',
        'email': 'existing@example.com',
        'username': 'existing',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }, follow_redirects=True)

    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert "already registered" in html.lower()
    assert "verification" in html.lower()


def test_signup_rate_limit_surfaces_clear_message(monkeypatch):
    """When Supabase returns 429 over_email_send_rate_limit, surface clear message."""
    class FakeAuth:
        def sign_up(self, credentials):
            raise Exception("over_email_send_rate_limit: email rate limit exceeded (HTTP 429)")

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{'id': 1}]

    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.post('/signup', data={
        'first_name': 'Rate',
        'last_name': 'Limited',
        'program': 'BSIT',
        'email': 'ratelimit@example.com',
        'username': 'ratelimit',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })

    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert "rate limit" in html.lower()


# ---------------------------------------------------------------------------
# 3. Tests for Resend Verification Route with 60-second Cooldown
# ---------------------------------------------------------------------------

def test_resend_verification_calls_supabase(monkeypatch):
    """Resend verification calls supabase.auth.resend with callback url."""
    captured_resend = {}

    class FakeAuth:
        def resend(self, params):
            captured_resend.update(params)
            return MagicMock()

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setenv('SITE_URL', 'http://127.0.0.1:5000')

    # Clear cooldown for test email
    test_email = 'fresh_user@example.com'
    with _resend_cooldowns_lock:
        _resend_cooldowns.pop(test_email, None)

    client = app_module.app.test_client()
    resp = client.post('/resend_verification', data={'email': test_email}, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert "verification email has been sent" in html.lower()
    assert captured_resend.get('email') == test_email
    assert captured_resend.get('type') == 'signup'
    assert captured_resend.get('options', {}).get('email_redirect_to') == 'http://127.0.0.1:5000/auth/callback'


def test_resend_verification_enforces_cooldown(monkeypatch):
    """Subsequent request within 60 seconds is rejected by cooldown."""
    fake_supabase = MagicMock()
    fake_supabase.auth.resend.return_value = MagicMock()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    test_email = 'cooldown_user@example.com'
    with _resend_cooldowns_lock:
        _resend_cooldowns[test_email] = time.time()  # set right now

    client = app_module.app.test_client()
    resp = client.post('/resend_verification', data={'email': test_email}, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert "please wait" in html.lower()
    assert "seconds" in html.lower()


# ---------------------------------------------------------------------------
# 4. Tests for Email-Confirmation Callback Route (/auth/callback)
# ---------------------------------------------------------------------------

def test_auth_callback_pkce_code_flow(monkeypatch):
    """PKCE flow exchange_code_for_session confirms session and authenticates user."""
    mock_session = MagicMock()
    mock_session.access_token = 'mock-jwt-token'
    mock_session.refresh_token = 'mock-refresh-token'
    mock_user = MagicMock()
    mock_user.id = 'verified-user-uuid'
    mock_user.email = 'verified@example.com'
    mock_user.user_metadata = {'username': 'verifieduser', 'role': 'scheduler', 'program_id': 1}
    mock_session.user = mock_user

    class FakeAuth:
        def exchange_code_for_session(self, params):
            assert params.get('auth_code') == 'valid-pkce-code'
            mock_res = MagicMock()
            mock_res.session = mock_session
            return mock_res

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {'id': 'verified-user-uuid', 'role': 'scheduler', 'program_id': 1}
    ]
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.get('/auth/callback?code=valid-pkce-code', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location == '/schedules' or resp.location == '/'

    with client.session_transaction() as sess:
        assert sess.get('user_id') == 'verified-user-uuid'
        assert sess.get('jwt_token') == 'mock-jwt-token'


def test_auth_callback_token_hash_flow(monkeypatch):
    """Token hash OTP flow verify_otp confirms session."""
    mock_session = MagicMock()
    mock_session.access_token = 'mock-jwt-token-otp'
    mock_session.refresh_token = 'mock-refresh-token-otp'
    mock_user = MagicMock()
    mock_user.id = 'otp-user-uuid'
    mock_user.email = 'otp@example.com'
    mock_user.user_metadata = {'username': 'otpuser', 'role': 'scheduler'}
    mock_session.user = mock_user

    class FakeAuth:
        def verify_otp(self, params):
            assert params.get('token_hash') == 'hash-12345'
            mock_res = MagicMock()
            mock_res.session = mock_session
            return mock_res

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {'id': 'otp-user-uuid', 'role': 'scheduler', 'program_id': 1}
    ]
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.get('/auth/callback?token_hash=hash-12345&type=signup', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location == '/schedules' or resp.location == '/'

    with client.session_transaction() as sess:
        assert sess.get('user_id') == 'otp-user-uuid'


def test_auth_callback_client_hash_post(monkeypatch):
    """POST /auth/callback sets session from client-side parsed access_token."""
    mock_session = MagicMock()
    mock_session.access_token = 'hash-jwt-123'
    mock_session.refresh_token = 'hash-refresh-123'
    mock_user = MagicMock()
    mock_user.id = 'hash-user-uuid'
    mock_user.email = 'hash@example.com'
    mock_user.user_metadata = {'username': 'hashuser', 'role': 'scheduler'}
    mock_session.user = mock_user

    class FakeAuth:
        def set_session(self, at, rt):
            mock_res = MagicMock()
            mock_res.session = mock_session
            return mock_res

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    fake_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {'id': 'hash-user-uuid', 'role': 'scheduler', 'program_id': 1}
    ]
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.post('/auth/callback', json={
        'access_token': 'hash-jwt-123',
        'refresh_token': 'hash-refresh-123'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True


# ---------------------------------------------------------------------------
# 5. Tests for Show/Hide Password Eye Toggle (Login and Signup)
# ---------------------------------------------------------------------------

def test_password_eye_toggles_structure_in_login_template():
    """Verify HTML structure, button type, Bootstrap Icons, accessibility, and autocomplete attributes."""
    client = app_module.app.test_client()
    resp = client.get('/login')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Login password
    login_pwd = soup.find('input', {'id': 'password'})
    assert login_pwd is not None
    assert login_pwd.get('type') == 'password'
    assert login_pwd.get('autocomplete') == 'current-password'

    login_wrapper = login_pwd.find_parent('div', class_='password-field-wrapper')
    assert login_wrapper is not None

    login_btn = login_wrapper.find('button', class_='password-toggle-btn')
    assert login_btn is not None
    assert login_btn.get('type') == 'button'  # Never submits form
    assert login_btn.get('aria-label') == 'Show password'
    assert login_btn.get('aria-pressed') == 'false'
    assert login_btn.find('i', class_='bi-eye') is not None

    # 2. Signup password
    signup_pwd = soup.find('input', {'id': 'signupPassword'})
    assert signup_pwd is not None
    assert signup_pwd.get('type') == 'password'
    assert signup_pwd.get('autocomplete') == 'new-password'

    signup_wrapper = signup_pwd.find_parent('div', class_='password-field-wrapper')
    assert signup_wrapper is not None
    signup_btn = signup_wrapper.find('button', class_='password-toggle-btn')
    assert signup_btn is not None
    assert signup_btn.get('type') == 'button'
    assert signup_btn.find('i', class_='bi-eye') is not None

    # 3. Signup confirm password
    confirm_pwd = soup.find('input', {'id': 'confirmPassword'})
    assert confirm_pwd is not None
    assert confirm_pwd.get('type') == 'password'
    assert confirm_pwd.get('autocomplete') == 'new-password'

    confirm_wrapper = confirm_pwd.find_parent('div', class_='password-field-wrapper')
    assert confirm_wrapper is not None
    confirm_btn = confirm_wrapper.find('button', class_='password-toggle-btn')
    assert confirm_btn is not None
    assert confirm_btn.get('type') == 'button'
    assert confirm_btn.find('i', class_='bi-eye') is not None


def test_login_unconfirmed_email_displays_resend_option(monkeypatch):
    """When login encounters unconfirmed email error, user is presented with resend option."""
    class FakeAuth:
        def sign_in_with_password(self, creds):
            raise Exception("Email not confirmed (HTTP 400)")

    fake_supabase = MagicMock()
    fake_supabase.auth = FakeAuth()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)

    client = app_module.app.test_client()
    resp = client.post('/login', data={
        'username': 'unconfirmed@example.com',
        'password': 'Password123!'
    })
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert "email has not been verified" in html.lower()

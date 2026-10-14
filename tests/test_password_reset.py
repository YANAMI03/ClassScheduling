"""Forgot / reset password feature (Supabase Auth built-in recovery)."""

from types import SimpleNamespace

import pytest

import app as app_module


class FakeAuthResponse:
    def __init__(self, user_id='user-1'):
        user = SimpleNamespace(id=user_id)
        self.user = user
        self.session = SimpleNamespace(access_token='at', refresh_token='rt', user=user)


class FakeUserResponse:
    def __init__(self):
        self.user = SimpleNamespace(id='user-1')


class FakeAuth:
    def __init__(self, sink):
        self.sink = sink

    def reset_password_for_email(self, email, options=None):
        self.sink.append(('reset', email, options))

    def exchange_code_for_session(self, params):
        self.sink.append(('exchange', params))
        return FakeAuthResponse()

    def verify_otp(self, params):
        self.sink.append(('verify', params))
        return FakeAuthResponse()

    def set_session(self, access, refresh):
        self.sink.append(('set_session', access))
        return FakeAuthResponse()

    def update_user(self, attrs):
        self.sink.append(('update_user', attrs))
        return FakeUserResponse()

    def sign_out(self):
        self.sink.append(('sign_out',))


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []
        self._limit = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append(('eq', col, str(val).lower()))
        return self

    def ilike(self, col, val):
        self._filters.append(('ilike', col, str(val).lower()))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        rows = list(self.rows)
        for op, col, val in self._filters:
            if op == 'eq':
                rows = [r for r in rows if str(r.get(col, '')).lower() == val]
            else:
                rows = [r for r in rows if val.strip('%') in str(r.get(col, '')).lower()]
        if self._limit is not None:
            rows = rows[:self._limit]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, users):
        self._users = users
        self.sink = []
        self.auth = FakeAuth(self.sink)

    def table(self, name):
        if name == 'users':
            return FakeQuery(self._users)
        return FakeQuery([])


@pytest.fixture
def client():
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    with app_module._password_reset_throttles_lock:
        app_module._password_reset_throttles.clear()
    return app.test_client()


def _install(monkeypatch, users):
    fake = FakeSupabase(users)
    monkeypatch.setattr(app_module, 'supabase', fake)
    monkeypatch.setattr(app_module, 'log_activity', lambda *a, **k: None)
    return fake


def test_forgot_password_page_renders(client):
    res = client.get('/forgot-password')
    assert res.status_code == 200
    assert b'forgotForm' in res.data
    assert b'Forgot password' in res.data


def test_forgot_password_active_user_sends_and_is_generic(client, monkeypatch):
    fake = _install(monkeypatch, [{'id': 'u1', 'email': 'a@example.com', 'username': 'alice', 'disabled': False}])
    res = client.post('/forgot-password', data={'email': 'a@example.com'}, follow_redirects=True)
    assert res.status_code == 200
    assert any(evt[0] == 'reset' for evt in fake.sink)
    assert b'If an account exists for that email, a reset link has been sent.' in res.data


def test_forgot_password_disabled_user_skips_send_but_is_generic(client, monkeypatch):
    fake = _install(monkeypatch, [{'id': 'u1', 'email': 'a@example.com', 'username': 'alice', 'disabled': True}])
    res = client.post('/forgot-password', data={'email': 'a@example.com'}, follow_redirects=True)
    assert res.status_code == 200
    assert not any(evt[0] == 'reset' for evt in fake.sink)
    assert b'If an account exists for that email, a reset link has been sent.' in res.data


def test_forgot_password_unknown_user_skips_send_but_is_generic(client, monkeypatch):
    fake = _install(monkeypatch, [])
    res = client.post('/forgot-password', data={'email': 'nobody@example.com'}, follow_redirects=True)
    assert res.status_code == 200
    assert not any(evt[0] == 'reset' for evt in fake.sink)
    assert b'If an account exists for that email, a reset link has been sent.' in res.data


def test_forgot_password_rate_limited(client, monkeypatch):
    _install(monkeypatch, [{'id': 'u1', 'email': 'a@example.com', 'username': 'alice', 'disabled': False}])
    last = None
    for _ in range(6):
        last = client.post('/forgot-password', data={'email': 'a@example.com'})
    assert last.status_code == 429


def test_reset_password_without_session_shows_expired(client):
    res = client.get('/reset-password')
    assert res.status_code == 200
    assert b'Request a New Link' in res.data
    assert res.headers.get('Cache-Control', '').startswith('no-store')


def test_reset_password_code_exchange_then_form(client, monkeypatch):
    fake = _install(monkeypatch, [])
    res = client.get('/reset-password?code=abc123')
    assert res.status_code == 302
    assert res.headers['Location'].endswith('/reset-password')
    assert any(evt[0] == 'exchange' for evt in fake.sink)
    with client.session_transaction() as sess:
        assert sess.get('recovery_access_token') == 'at'
    # Clean GET now renders the form (token is in the session, not the URL).
    res2 = client.get('/reset-password')
    assert b'resetForm' in res2.data


def test_reset_password_mismatch_rejected(client, monkeypatch):
    fake = _install(monkeypatch, [])
    with client.session_transaction() as sess:
        sess['recovery_access_token'] = 'at'
        sess['recovery_refresh_token'] = 'rt'
    res = client.post('/reset-password', data={'password': 'longenough1', 'confirm_password': 'different1'})
    assert b'Passwords do not match' in res.data
    assert not any(evt[0] == 'update_user' for evt in fake.sink)


def test_reset_password_too_short_rejected(client, monkeypatch):
    fake = _install(monkeypatch, [])
    with client.session_transaction() as sess:
        sess['recovery_access_token'] = 'at'
    res = client.post('/reset-password', data={'password': 'short', 'confirm_password': 'short'})
    assert b'at least 8 characters' in res.data
    assert not any(evt[0] == 'update_user' for evt in fake.sink)


def test_reset_password_success_updates_and_redirects(client, monkeypatch):
    fake = _install(monkeypatch, [])
    with client.session_transaction() as sess:
        sess['recovery_access_token'] = 'at'
        sess['recovery_refresh_token'] = 'rt'
        sess['recovery_user_id'] = 'u1'
    res = client.post('/reset-password', data={'password': 'brandnewpass1', 'confirm_password': 'brandnewpass1'})
    assert res.status_code == 302
    assert res.headers['Location'].endswith('/login')
    assert any(evt[0] == 'update_user' and evt[1].get('password') == 'brandnewpass1' for evt in fake.sink)
    assert any(evt[0] == 'sign_out' for evt in fake.sink)
    with client.session_transaction() as sess:
        assert 'recovery_access_token' not in sess


def test_admin_send_reset_link(client, monkeypatch):
    fake = _install(monkeypatch, [
        {'id': 'admin-1', 'role': 'admin', 'disabled': False},
        {'id': 'target-1', 'email': 't@example.com', 'username': 'target', 'disabled': False},
    ])
    with client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'admin'
    res = client.post('/send_reset_link/target-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json().get('success') is True
    assert any(evt[0] == 'reset' for evt in fake.sink)


def test_set_temp_password_without_service_role(client, monkeypatch):
    _install(monkeypatch, [
        {'id': 'admin-1', 'role': 'admin', 'disabled': False},
        {'id': 'target-1', 'username': 'target'},
    ])
    monkeypatch.setattr(app_module, 'supabase_admin', None)
    with client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'admin'
    res = client.post('/set_temp_password/target-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 503
    assert 'SUPABASE_SERVICE_ROLE_KEY' in res.get_json().get('message', '')

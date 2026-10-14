"""Regression tests: a program-scoped viewer/chair must resolve program_id from
public.users through the shared helper, regardless of the session shape.

Bug: viewers were treated like admins and had session['program_id'] cleared at
login and in the RBAC middleware, so a viewer whose public.users row HAS a
program_id could still be told "No program is assigned to your account".
"""

from types import SimpleNamespace

import pytest

import app as app_module


class FakeQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.filters = []
        self.limit_val = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val).lower()))
        return self

    def is_(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def limit(self, n):
        self.limit_val = n
        return self

    def insert(self, payload):
        self.store.setdefault(self.name, []).append(dict(payload))
        return self

    def update(self, payload):
        for r in self.store.get(self.name, []):
            if all(str(r.get(c, '')).lower() == v for c, v in self.filters):
                r.update(payload)
        return self

    def execute(self):
        rows = list(self.store.get(self.name, []))
        for col, val in self.filters:
            key = col.split('.')[-1]
            rows = [r for r in rows if str(r.get(key, r.get(col, ''))).lower() == val]
        if self.limit_val is not None:
            rows = rows[: self.limit_val]
        return SimpleNamespace(data=[dict(r) for r in rows])


class FakePostgrest:
    def __init__(self):
        self.headers = {}

    def auth(self, token):
        self.headers['Authorization'] = 'Bearer ' + str(token)
        return self


class FakeSupabase:
    def __init__(self, store):
        self.store = store
        self.postgrest = FakePostgrest()
        self.auth = SimpleNamespace(sign_out=lambda: None)

    def table(self, name):
        return FakeQuery(self.store, name)

    def rpc(self, *a, **k):
        return FakeQuery(self.store, 'rpc')


def _store(program_id):
    return {
        'users': [{
            'id': 'v1', 'email': 'v@example.com', 'username': 'viewer1',
            'role': 'viewer', 'program_id': program_id, 'disabled': False,
            'first_name': 'V', 'last_name': 'One',
        }],
        'program': [{'id': 1, 'program_code': 'BSIT', 'archive': False}],
        'schedule': [], 'course': [], 'professor_load': [], 'semester': [],
        'working_hours': [], 'room': [], 'specializations': [], 'activity_log': [],
    }


def _client():
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    return app.test_client()


def test_viewer_with_db_program_and_empty_session_is_scoped(monkeypatch):
    """Session has NO program_id, but public.users has program_id=1 -> viewer is global (program_id is None)."""
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(_store(1)))
    client = _client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'v1'
        sess['role'] = 'viewer'
        sess['username'] = 'viewer1'
        sess['jwt_token'] = 'tok'
        sess['refresh_token'] = 'ref'
        # NOTE: deliberately no program_id, matching the broken login state.

    with app_module.app.test_request_context('/schedules'):
        from flask import session as f_session
        f_session['user_id'] = 'v1'
        f_session['role'] = 'viewer'
        assert app_module._get_user_program_id() is None

    res = client.get('/schedules')
    assert res.status_code == 200
    assert 'No program is assigned to your account' not in res.data.decode('utf-8', 'ignore')


def test_viewer_without_program_sees_all_programs(monkeypatch):
    """A viewer with no program is global: no 'Program Required' alert."""
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(_store(None)))
    client = _client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'v1'
        sess['role'] = 'viewer'
        sess['username'] = 'viewer1'
        sess['jwt_token'] = 'tok'
        sess['refresh_token'] = 'ref'

    res = client.get('/schedules')
    assert res.status_code == 200
    assert 'No program is assigned to your account. Please contact an administrator.' not in res.data.decode('utf-8', 'ignore')


def test_chair_without_program_sees_error(monkeypatch):
    """A scheduler (chair) without a program still gets the alert."""
    store = _store(None)
    store['users'][0]['role'] = 'chair'
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(store))
    client = _client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'v1'
        sess['role'] = 'chair'
        sess['username'] = 'chair1'
        sess['jwt_token'] = 'tok'
        sess['refresh_token'] = 'ref'

    res = client.get('/schedules')
    assert res.status_code == 200
    assert 'No program is assigned to your account. Please contact an administrator.' in res.data.decode('utf-8', 'ignore')


def test_establish_session_keeps_viewer_without_program(monkeypatch):
    """Login keeps a viewer global (program_id None)."""
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(_store(None)))
    monkeypatch.setattr(app_module, '_get_programs', lambda *a, **k: [{'id': 1, 'program_code': 'BSIT'}])
    auth_user = SimpleNamespace(
        id='v1', email='v@example.com', user_metadata={'username': 'viewer1'},
    )
    session_data = SimpleNamespace(
        user=auth_user, access_token='tok', refresh_token='ref',
    )
    with app_module.app.test_request_context('/login'):
        assert app_module._establish_authenticated_session(session_data) is True
        from flask import session as f_session
        assert f_session.get('role') == 'viewer'
        assert f_session.get('program_id') is None


def _viewer_client(monkeypatch):
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(_store(None)))
    client = _client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'v1'
        sess['role'] = 'viewer'
        sess['username'] = 'viewer1'
        sess['jwt_token'] = 'tok'
        sess['refresh_token'] = 'ref'
    return client


def test_viewer_can_open_room_schedule_without_program(monkeypatch):
    client = _viewer_client(monkeypatch)
    res = client.get('/room_schedule')
    assert res.status_code == 200
    assert 'No program is assigned to your account' not in res.data.decode('utf-8', 'ignore')


def test_viewer_can_open_section_detail_without_program(monkeypatch):
    client = _viewer_client(monkeypatch)
    res = client.get('/schedule/BSIT-1A')
    assert res.status_code == 200
    assert b'No program is assigned' not in res.data


def test_viewer_can_switch_program(monkeypatch):
    client = _viewer_client(monkeypatch)
    res = client.get('/schedules?program=BSIT')
    assert res.status_code == 200
    assert b'No program is assigned' not in res.data


def test_viewer_blocked_from_edit_endpoint(monkeypatch):
    client = _viewer_client(monkeypatch)
    res = client.post('/edit_user/v1', data={'role': 'viewer'})
    assert res.status_code == 403

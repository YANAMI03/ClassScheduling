import json
import pytest
import app as app_module


class FakeTableQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.last_action = None
        self.last_payload = None
        self.eq_filters = {}
        self.ilike_filters = {}
        self.or_expr = None

    def select(self, *args, **kwargs):
        self.last_action = 'select'
        return self

    def insert(self, payload):
        self.last_action = 'insert'
        self.last_payload = payload
        return self

    def update(self, payload):
        self.last_action = 'update'
        self.last_payload = payload
        return self

    def delete(self):
        self.last_action = 'delete'
        return self

    def upsert(self, payload, on_conflict=None):
        self.last_action = 'upsert'
        self.last_payload = payload
        return self

    def eq(self, column, value):
        self.eq_filters[column] = str(value)
        return self

    def ilike(self, column, value):
        self.ilike_filters[column] = str(value)
        return self

    def or_(self, expr):
        self.or_expr = expr
        return self

    def limit(self, count):
        return self

    def execute(self):
        class Response:
            def __init__(self, data):
                self.data = data

        if self.last_action == 'select':
            rows = self.store.get(self.table_name, [])
            filtered = []
            for r in rows:
                match = True
                for col, val in self.eq_filters.items():
                    if str(r.get(col, '')).lower() != val.lower():
                        match = False
                        break
                for col, val in self.ilike_filters.items():
                    if val.lower() not in str(r.get(col, '')).lower():
                        match = False
                        break
                if self.or_expr:
                    or_match = False
                    parts = self.or_expr.split(',')
                    for part in parts:
                        if '.ilike.' in part:
                            c, v = part.split('.ilike.', 1)
                            if v.lower() in str(r.get(c, '')).lower():
                                or_match = True
                                break
                    if not or_match:
                        match = False
                if match:
                    filtered.append(dict(r))
            return Response(filtered)

        if self.last_action == 'update':
            updated_rows = []
            for r in self.store.get(self.table_name, []):
                for col, val in self.eq_filters.items():
                    if str(r.get(col)) == val:
                        r.update(self.last_payload)
                        updated_rows.append(dict(r))
            return Response(updated_rows)

        if self.last_action == 'delete':
            remaining = []
            for r in self.store.get(self.table_name, []):
                match = True
                for col, val in self.eq_filters.items():
                    if str(r.get(col)) == val:
                        match = False
                if match:
                    remaining.append(r)
            self.store[self.table_name] = remaining
            return Response([])

        if self.last_action in ('insert', 'upsert'):
            data = self.last_payload
            if isinstance(data, list):
                self.store.setdefault(self.table_name, []).extend(data)
            else:
                # Handle upsert on email
                existing = None
                for idx, item in enumerate(self.store.setdefault(self.table_name, [])):
                    if item.get('email') == data.get('email') or item.get('id') == data.get('id'):
                        existing = idx
                        break
                if existing is not None:
                    self.store[self.table_name][existing].update(data)
                else:
                    self.store[self.table_name].append(data)
            return Response(data)

        return Response([])


class FakeSupabaseClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.postgrest = self

        class FakeAuth:
            def sign_up(self, credentials):
                email = credentials.get('email', '')
                if email == 'duplicate@example.com':
                    raise Exception('User already registered')
                class AuthUser:
                    id = f"gen-{email.split('@')[0]}"
                class AuthResponse:
                    user = AuthUser()
                return AuthResponse()

        self.auth = FakeAuth()

    def table(self, name):
        return FakeTableQuery(name, self.store)

    def rpc(self, fn_name, params=None):
        params = params or {}
        class RPCQuery:
            def __init__(self, store):
                self.store = store
            def execute(self):
                class Response:
                    def __init__(self, data):
                        self.data = data
                if fn_name == 'get_email_by_username':
                    ident = params.get('p_username', '').strip().lower()
                    for u in self.store.get('users', []):
                        if (u.get('username') or '').strip().lower() == ident or (u.get('email') or '').strip().lower() == ident:
                            return Response(u.get('email') or f"{(u.get('username') or ident).lower()}@example.com")
                    return Response(None)
                return Response(None)
        return RPCQuery(self.store)


@pytest.fixture
def mock_db(monkeypatch):
    store = {
        'users': [
            {
                'id': 'user-1-admin',
                'username': 'adminuser',
                'first_name': 'Admin',
                'last_name': 'Super',
                'email': 'admin@example.com',
                'role': 'admin',
                'program': None,
                'profile_picture': 'default.png',
            },
            {
                'id': 'user-2-scheduler',
                'username': 'scheduser',
                'first_name': 'Sarah',
                'last_name': 'Scheduler',
                'email': 'scheduler@example.com',
                'role': 'Scheduler',
                'program': 'BSIT',
                'profile_picture': None,
            },
            {
                'id': 'user-3-viewer',
                'username': 'viewuser',
                'first_name': 'Victor',
                'last_name': 'Viewer',
                'email': 'viewer@example.com',
                'role': 'Viewer',
                'program': 'BSIT',
                'profile_picture': None,
            },
        ],
        'activity_log': [],
    }
    fake_client = FakeSupabaseClient(store)
    monkeypatch.setattr(app_module, 'supabase', fake_client)
    return store


def test_users_route_unauthenticated():
    client = app_module.app.test_client()
    res = client.get('/users')
    assert res.status_code == 302
    assert '/login' in res.location


def test_users_route_non_admin_redirected():
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-3-viewer'
        sess['role'] = 'Viewer'
        sess['username'] = 'viewuser'

    res = client.get('/users')
    assert res.status_code == 302
    assert '/schedules' in res.location


def test_users_route_admin_success(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.get('/users')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'User Management' in html
    assert 'admin@example.com' in html
    assert 'scheduler@example.com' in html
    assert 'viewer@example.com' in html


def test_users_route_search_query_param(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.get('/users?search=scheduler@example.com')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'scheduler@example.com' in html
    assert 'admin@example.com' not in html


def test_search_users_filter_by_name(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.get('/search_users?q=Sarah')
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert 'users' in data
    assert len(data['users']) == 1
    assert data['users'][0]['first_name'] == 'Sarah'


def test_search_users_filter_by_email(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.get('/search_users?q=viewer@example.com')
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert len(data['users']) == 1
    assert data['users'][0]['email'] == 'viewer@example.com'


def test_search_users_filter_by_role(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.get('/search_users?q=admin')
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert any(u['role'] == 'admin' for u in data['users'])


def test_edit_user(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/edit_user/user-2-scheduler', data={
        'first_name': 'Sarah Updated',
        'last_name': 'Scheduler',
        'email': 'scheduler_updated@example.com',
        'role': 'Scheduler',
        'program': 'BSIT',
    })
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is True

    # Check that in-memory mock store was updated
    updated = [u for u in mock_db['users'] if u['id'] == 'user-2-scheduler'][0]
    assert updated['first_name'] == 'Sarah Updated'
    assert updated['email'] == 'scheduler_updated@example.com'


def test_edit_user_not_found(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/edit_user/non-existent-user-id', data={
        'first_name': 'Nobody',
        'last_name': 'Here',
        'email': 'nobody@example.com',
        'role': 'Viewer',
        'program': 'BSIT',
    })
    assert res.status_code == 404
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False
    assert 'not found' in data['message'].lower()


def test_edit_user_missing_program_for_scheduler(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/edit_user/user-2-scheduler', data={
        'first_name': 'Sarah',
        'last_name': 'Scheduler',
        'email': 'scheduler@example.com',
        'role': 'Scheduler',
        'program': '',  # missing
    })
    assert res.status_code == 400
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False
    assert 'program is required' in data['message'].lower()


def test_admin_cannot_demote_self(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/edit_user/user-1-admin', data={
        'first_name': 'Admin',
        'last_name': 'Super',
        'email': 'admin@example.com',
        'role': 'Viewer',
        'program': 'BSIT',
    })
    assert res.status_code == 403
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False
    assert 'administrator' in data['message'].lower()


def test_delete_user(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/delete_user/user-3-viewer')
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is True

    remaining_ids = [u['id'] for u in mock_db['users']]
    assert 'user-3-viewer' not in remaining_ids


def test_admin_cannot_delete_self(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/delete_user/user-1-admin')
    assert res.status_code == 403
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False


def test_create_user_success(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/create_user', data={
        'first_name': 'New',
        'last_name': 'Scheduler',
        'email': 'newsched@example.com',
        'password': 'Password123!',
        'role': 'Scheduler',
        'program': 'BSIT',
    })
    assert res.status_code == 200
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is True

    created = [u for u in mock_db['users'] if u['email'] == 'newsched@example.com']
    assert len(created) == 1
    assert created[0]['first_name'] == 'New'


def test_create_user_missing_fields(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/create_user', data={
        'first_name': 'New',
        'last_name': '',  # missing
        'email': 'incomplete@example.com',
        'password': 'Password123!',
        'role': 'Viewer',
        'program': 'BSIT',
    })
    assert res.status_code == 400
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False


def test_create_user_duplicate_email(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1-admin'
        sess['role'] = 'admin'
        sess['username'] = 'adminuser'

    res = client.post('/create_user', data={
        'first_name': 'Duplicate',
        'last_name': 'User',
        'email': 'duplicate@example.com',
        'password': 'Password123!',
        'role': 'Viewer',
        'program': 'BSIT',
    })
    assert res.status_code == 400
    data = json.loads(res.data.decode('utf-8'))
    assert data['success'] is False
    assert 'already exists' in data['message'].lower()


def test_find_email_by_username_or_email(mock_db):
    # Direct email with @ returns immediately
    assert app_module._find_email_by_username_or_email('direct@example.com') == 'direct@example.com'

    # Username lookup via RPC
    assert app_module._find_email_by_username_or_email('scheduser') == 'scheduler@example.com'

    # Non-existent username falls back to @example.com
    assert app_module._find_email_by_username_or_email('unknownperson') == 'unknownperson@example.com'


def test_set_admin_role_route(mock_db):
    client = app_module.app.test_client()
    res = client.get('/set_admin_role/viewer@example.com')
    assert res.status_code == 200
    assert 'updated to admin role' in res.data.decode('utf-8')

    viewer = [u for u in mock_db['users'] if u['email'] == 'viewer@example.com'][0]
    assert viewer['role'] == 'admin'


def test_user_to_dict_helper():
    # Database dictionary row
    row = {
        'id': 'abc-123',
        'email': 'test@example.com',
        'username': 'testuser',
        'first_name': 'Test',
        'last_name': 'User',
        'program': 'BSIT',
        'role': 'Scheduler',
        'profile_picture': 'pic.png',
    }
    d = app_module._user_to_dict(row)
    assert d['id'] == 'abc-123'
    assert d['email'] == 'test@example.com'
    assert d['username'] == 'testuser'
    assert d['role'] == 'Scheduler'


def test_signup_success(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Alice',
        'last_name': 'Wonder',
        'program': 'BSIT',
        'email': 'alice@example.com',
        'username': 'alicew',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 302
    assert '/login' in res.location

    # Check activity log was recorded
    logs = [l for l in mock_db['activity_log'] if 'Self-registered: alicew' in l.get('target_detail', '')]
    assert len(logs) == 1
    assert 'alice@example.com' in logs[0]['target_detail']


def test_signup_email_only_success(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Emma',
        'last_name': 'EmailOnly',
        'program': 'BSIT',
        'email': 'emma@example.com',
        'username': '',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 302
    assert '/login' in res.location

    # Check activity log was recorded with email
    logs = [l for l in mock_db['activity_log'] if 'Self-registered: emma@example.com' in l.get('target_detail', '')]
    assert len(logs) == 1


def test_signup_username_only_success(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Ulysses',
        'last_name': 'UserOnly',
        'program': 'BSBA',
        'email': '',
        'username': 'ulyssesu',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 302
    assert '/login' in res.location

    # Check activity log was recorded with username
    logs = [l for l in mock_db['activity_log'] if 'Self-registered: ulyssesu' in l.get('target_detail', '')]
    assert len(logs) == 1


def test_signup_missing_both_email_and_username(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Nobody',
        'last_name': 'NoIdent',
        'program': 'BSIT',
        'email': '',
        'username': '',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert 'Please provide an email, a username, or both.' in res.data.decode('utf-8')


def test_signup_missing_required_fields(mock_db):
    client = app_module.app.test_client()
    # Missing first name
    res = client.post('/signup', data={
        'first_name': '',
        'last_name': 'Wonder',
        'program': 'BSIT',
        'email': 'alice@example.com',
        'username': '',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert 'All fields are required.' in res.data.decode('utf-8')

    # Missing password
    res = client.post('/signup', data={
        'first_name': 'Alice',
        'last_name': 'Wonder',
        'program': 'BSIT',
        'email': '',
        'username': 'alicew',
        'password': '',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert 'All fields are required.' in res.data.decode('utf-8')


def test_find_email_simultaneous_resolution(mock_db):
    # Admin user has username 'adminuser' and email 'admin@example.com'
    # Resolve by username
    email_by_username = app_module._find_email_by_username_or_email('adminuser')
    assert email_by_username == 'admin@example.com'

    # Resolve by email
    email_by_email = app_module._find_email_by_username_or_email('admin@example.com')
    assert email_by_email == 'admin@example.com'

    # Sched user: Sarah Scheduler (username 'scheduser', email 'scheduler@example.com')
    assert app_module._find_email_by_username_or_email('scheduser') == 'scheduler@example.com'
    assert app_module._find_email_by_username_or_email('scheduler@example.com') == 'scheduler@example.com'

    # Unregistered username fallback convention
    assert app_module._find_email_by_username_or_email('newunknown') == 'newunknown@example.com'


def test_signup_invalid_email(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Alice',
        'last_name': 'Wonder',
        'program': 'BSIT',
        'email': 'not-an-email',
        'username': 'alicew',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert 'Please enter a valid email address.' in res.data.decode('utf-8')


def test_signup_password_mismatch(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Alice',
        'last_name': 'Wonder',
        'program': 'BSIT',
        'email': 'alice@example.com',
        'username': 'alicew',
        'password': 'Password123!',
        'confirm_password': 'DifferentPassword!',
    })
    assert res.status_code == 200
    assert 'Passwords do not match.' in res.data.decode('utf-8')


def test_signup_duplicate_email(mock_db):
    client = app_module.app.test_client()
    res = client.post('/signup', data={
        'first_name': 'Duplicate',
        'last_name': 'User',
        'program': 'BSIT',
        'email': 'duplicate@example.com',
        'username': 'dupuser',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert 'Email or username already registered.' in res.data.decode('utf-8')


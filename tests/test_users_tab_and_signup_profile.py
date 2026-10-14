import os
import re
import uuid
import pytest
from bs4 import BeautifulSoup
import app as app_module


class MockAuthUser:
    def __init__(self, uid, email, raw_user_meta_data=None, created_at='2026-10-12T00:00:00Z'):
        self.id = uid
        self.email = email
        self.raw_user_meta_data = raw_user_meta_data or {}
        self.created_at = created_at


class MockAuthResponse:
    def __init__(self, user, session=None):
        self.user = user
        self.session = session


class MockPostgrestResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else (len(data) if isinstance(data, list) else 0)


class MockDatabase:
    """Simulates PostgreSQL with public.users table and handle_new_user trigger."""
    def __init__(self):
        self.users = []
        self.programs = [
            {'id': 1, 'program_name': 'BSIT'},
            {'id': 2, 'program_name': 'BSBA'},
            {'id': 9, 'program_name': 'BSDS'},
        ]
        self.auth_users = []
        self.trigger_error_simulate = False

    def handle_new_user_trigger(self, new_auth_user):
        """Simulates public.handle_new_user() trigger from migrations/20261012_profile_on_signup.sql."""
        if self.trigger_error_simulate:
            # Trigger handles exception with RAISE WARNING and allows auth signup to succeed
            return

        meta = new_auth_user.raw_user_meta_data or {}
        first_name = (meta.get('first_name') or '').strip()
        last_name = (meta.get('last_name') or '').strip()

        # Derive username from email local part
        email = new_auth_user.email or 'user'
        base_username = (email.split('@')[0] if '@' in email else email).lower().strip()
        if not base_username:
            base_username = 'user'

        # Collision avoidance with numeric suffix
        v_username = base_username
        suffix = 1
        existing_usernames = [u.get('username', '').lower() for u in self.users if str(u.get('id')) != str(new_auth_user.id)]
        while v_username.lower() in existing_usernames:
            v_username = f"{base_username}{suffix}"
            suffix += 1

        # Check if row with this id already exists (ON CONFLICT (id) DO NOTHING)
        for u in self.users:
            if str(u.get('id')) == str(new_auth_user.id):
                return

        new_row = {
            'id': str(new_auth_user.id),
            'email': new_auth_user.email,
            'username': v_username,
            'first_name': first_name,
            'last_name': last_name,
            'role': 'viewer',  # Hard-coded viewer
            'program_id': None,  # Hard-coded NULL
            'created_at': getattr(new_auth_user, 'created_at', '2026-10-12T00:00:00Z'),
            'profile_picture': None,
        }
        self.users.append(new_row)

    def backfill_orphans(self):
        """Simulates the backfill loop in migrations/20261012_profile_on_signup.sql."""
        user_ids = {str(u['id']) for u in self.users}
        for au in self.auth_users:
            if str(au.id) not in user_ids:
                self.handle_new_user_trigger(au)


class MockTableQuery:
    def __init__(self, table_name, db):
        self.table_name = table_name
        self.db = db
        self.action = 'select'
        self.payload = None
        self.eq_filters = {}
        self.order_col = None
        self.order_desc = False

    def select(self, *args, **kwargs):
        self.action = 'select'
        return self

    def insert(self, payload):
        self.action = 'insert'
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.action = 'upsert'
        self.payload = payload
        return self

    def update(self, payload):
        self.action = 'update'
        self.payload = payload
        return self

    def delete(self):
        self.action = 'delete'
        return self

    def eq(self, col, val):
        self.eq_filters[col] = str(val)
        return self

    def order(self, col, desc=False):
        self.order_col = col
        self.order_desc = desc
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.table_name == 'program':
            return MockPostgrestResponse(list(self.db.programs))

        if self.table_name == 'users':
            if self.action == 'select':
                rows = [dict(u) for u in self.db.users]
                for col, val in self.eq_filters.items():
                    rows = [r for r in rows if str(r.get(col, '')).lower() == str(val).lower()]
                for r in rows:
                    p_id = r.get('program_id')
                    if p_id:
                        p_match = next((p for p in self.db.programs if p['id'] == p_id), None)
                        r['program'] = {'id': p_id, 'program_name': p_match['program_name']} if p_match else None
                    else:
                        r['program'] = None
                return MockPostgrestResponse(rows)

            elif self.action == 'insert':
                payload = self.payload if isinstance(self.payload, list) else [self.payload]
                for p in payload:
                    self.db.users.append(dict(p))
                return MockPostgrestResponse(payload)

            elif self.action == 'upsert':
                p = dict(self.payload)
                uid = str(p.get('id'))
                existing = next((u for u in self.db.users if str(u.get('id')) == uid), None)
                if existing:
                    existing.update(p)
                else:
                    self.db.users.append(p)
                return MockPostgrestResponse([p])

            elif self.action == 'update':
                updated = []
                for u in self.db.users:
                    match = all(str(u.get(k)) == v for k, v in self.eq_filters.items())
                    if match:
                        u.update(self.payload)
                        updated.append(dict(u))
                return MockPostgrestResponse(updated)

            elif self.action == 'delete':
                self.db.users = [
                    u for u in self.db.users
                    if not all(str(u.get(k)) == v for k, v in self.eq_filters.items())
                ]
                return MockPostgrestResponse([])

        if self.table_name == 'activity_log':
            return MockPostgrestResponse([{'id': 1}])

        return MockPostgrestResponse([])


@pytest.fixture
def mock_env(monkeypatch):
    db = MockDatabase()
    # Initial existing users
    admin_id = '033cabba-8da2-47ba-aa0b-30d4262debf7'
    sched_id = '077438bb-a507-4c8b-a947-e1a08a7092e4'
    db.users = [
        {
            'id': admin_id,
            'username': 'admin',
            'role': 'admin',
            'first_name': 'Admin',
            'last_name': 'User',
            'email': 'admin@example.com',
            'program_id': None,
            'created_at': '2026-10-01T10:00:00Z',
        },
        {
            'id': sched_id,
            'username': 'scheduler',
            'role': 'scheduler',
            'first_name': 'Scheduler',
            'last_name': 'User',
            'email': 'scheduler@example.com',
            'program_id': 1,
            'created_at': '2026-10-02T10:00:00Z',
        },
    ]

    class MockSupabase:
        def table(self, table_name):
            return MockTableQuery(table_name, db)

        class auth:
            @staticmethod
            def sign_up(params):
                email = params.get('email')
                password = params.get('password')
                meta = params.get('options', {}).get('data', {})
                uid = str(uuid.uuid4())
                auth_user = MockAuthUser(uid, email, meta, created_at='2026-10-12T10:00:00Z')
                db.auth_users.append(auth_user)
                # Database trigger executes on auth.users AFTER INSERT
                db.handle_new_user_trigger(auth_user)
                # Email confirmation is required -> session is None!
                return MockAuthResponse(user=auth_user, session=None)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())
    monkeypatch.setattr(app_module, '_get_programs', lambda *args, **kwargs: db.programs)

    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    client = app.test_client()
    return client, db, admin_id


def test_unconfirmed_signup_creates_profile_and_appears_in_users_tab(mock_env):
    """
    Test 1:
    A new signup creates both auth user and public.users row (viewer, program NULL)
    even before email is confirmed, and shows up in the Users tab with Viewer badge and 'No program'.
    """
    client, db, admin_id = mock_env

    # 1. User performs self-signup
    signup_data = {
        'first_name': 'Jane',
        'last_name': 'Doe',
        'email': 'jane.doe@example.com',
        'username': 'janedoe',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }
    res = client.post('/signup', data=signup_data, follow_redirects=True)
    assert res.status_code == 200

    # Verify public.users row exists
    created_user = next((u for u in db.users if u['email'] == 'jane.doe@example.com'), None)
    assert created_user is not None
    assert created_user['role'] == 'viewer'
    assert created_user['program_id'] is None
    assert created_user['first_name'] == 'Jane'
    assert created_user['last_name'] == 'Doe'
    assert created_user['username'] == 'jane.doe'

    # 2. Admin logs in and opens /users
    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    users_page = client.get('/users')
    assert users_page.status_code == 200
    html = users_page.get_data(as_text=True)

    soup = BeautifulSoup(html, 'html.parser')
    # Row containing jane.doe@example.com must be present
    jane_row = None
    for tr in soup.find_all('tr'):
        if 'jane.doe@example.com' in tr.get_text():
            jane_row = tr
            break
    assert jane_row is not None, "Jane Doe's row must be visible in /users"

    # Must display 'No program'
    assert 'No program' in jane_row.get_text()

    # Must render a Viewer role badge
    badge = jane_row.find('span', class_=re.compile(r'badge-viewer'))
    assert badge is not None
    assert 'viewer' in badge.get_text().lower()


def test_signup_role_manipulation_ignored(mock_env):
    """
    Test 2:
    A signup that sends role=admin in the form or metadata still creates a viewer.
    """
    client, db, admin_id = mock_env

    # Malicious signup trying to claim admin role
    signup_data = {
        'first_name': 'Hacker',
        'last_name': 'One',
        'email': 'hacker@example.com',
        'username': 'hacker',
        'role': 'admin',  # client attempts to send role=admin
        'program': 'BSIT',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }
    client.post('/signup', data=signup_data, follow_redirects=True)

    hacker_user = next((u for u in db.users if u['email'] == 'hacker@example.com'), None)
    assert hacker_user is not None
    assert hacker_user['role'] == 'viewer', "Role must strictly be 'viewer'"
    assert hacker_user['program_id'] is None, "Program must strictly be None"


def test_two_signups_same_email_local_part_get_distinct_usernames(mock_env):
    """
    Test 3:
    Two signups with the same email local part get distinct usernames (numeric suffix collision avoidance).
    """
    client, db, _ = mock_env

    # User 1: alex@domainA.com
    client.post('/signup', data={
        'first_name': 'Alex',
        'last_name': 'First',
        'email': 'alex@domainA.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })

    # User 2: alex@domainB.com
    client.post('/signup', data={
        'first_name': 'Alex',
        'last_name': 'Second',
        'email': 'alex@domainB.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })

    user1 = next((u for u in db.users if (u.get('email') or '').lower() == 'alex@domaina.com'), None)
    user2 = next((u for u in db.users if (u.get('email') or '').lower() == 'alex@domainb.com'), None)

    assert user1 is not None and user2 is not None
    assert user1['username'] != user2['username']
    assert user1['username'] == 'alex'
    assert user2['username'] == 'alex1'


def test_orphan_auth_users_backfilled_and_listed(mock_env):
    """
    Test 4:
    Existing auth users without a profile are backfilled and listed.
    """
    client, db, admin_id = mock_env

    # Simulate existing orphaned auth users (unconfirmed and confirmed)
    orphan1 = MockAuthUser('orphan-1-uuid', 'orphan1@example.com', {'first_name': 'Orphan', 'last_name': 'One'})
    orphan2 = MockAuthUser('orphan-2-uuid', 'orphan2@example.com', {'first_name': 'Orphan', 'last_name': 'Two'})
    db.auth_users.extend([orphan1, orphan2])

    # Trigger backfill
    db.backfill_orphans()

    # Verify both are now in public.users as Viewers
    o1 = next((u for u in db.users if u['email'] == 'orphan1@example.com'), None)
    o2 = next((u for u in db.users if u['email'] == 'orphan2@example.com'), None)

    assert o1 is not None and o1['role'] == 'viewer' and o1['program_id'] is None
    assert o2 is not None and o2['role'] == 'viewer' and o2['program_id'] is None

    # Check that admin sees them in /users
    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['role'] = 'admin'
    res = client.get('/users')
    html = res.get_data(as_text=True)
    assert 'orphan1@example.com' in html
    assert 'orphan2@example.com' in html


def test_existing_schedulers_and_admins_unchanged_and_search_finds_viewer(mock_env):
    """
    Test 5:
    Existing schedulers and admins are unchanged and still listed, and search finds a Viewer by email, name, role.
    """
    client, db, admin_id = mock_env

    # Add a viewer
    viewer_row = {
        'id': 'viewer-uuid-1',
        'username': 'vieweruser',
        'role': 'viewer',
        'first_name': 'Alice',
        'last_name': 'Wonderland',
        'email': 'alice@wonderland.com',
        'program_id': None,
        'created_at': '2026-10-12T05:00:00Z',
    }
    db.users.append(viewer_row)

    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['role'] = 'admin'

    # Check /users displays admin and scheduler unchanged
    res = client.get('/users')
    html = res.get_data(as_text=True)
    assert 'Global / All Programs' in html  # For admin
    assert 'BSIT' in html  # For scheduler
    assert 'admin@example.com' in html
    assert 'scheduler@example.com' in html
    assert 'alice@wonderland.com' in html

    # Search by email
    search_res = client.get('/users?search=alice@wonderland.com')
    search_html = search_res.get_data(as_text=True)
    assert 'alice@wonderland.com' in search_html
    assert 'scheduler@example.com' not in search_html

    # Search by role
    search_role = client.get('/users?search=viewer')
    search_role_html = search_role.get_data(as_text=True)
    assert 'alice@wonderland.com' in search_role_html
    assert 'admin@example.com' not in search_role_html

    # JSON search endpoint /search_users
    api_res = client.get('/search_users?q=Alice')
    assert api_res.status_code == 200
    json_data = api_res.get_json()
    assert any(u['email'] == 'alice@wonderland.com' for u in json_data['users'])


def test_signup_succeeds_even_if_profile_insert_fails(mock_env):
    """
    Test 6:
    Signup still works when the profile insert would fail (auth user is created and failure is handled safely).
    """
    client, db, _ = mock_env

    db.trigger_error_simulate = True
    res = client.post('/signup', data={
        'first_name': 'Robust',
        'last_name': 'Test',
        'email': 'robust@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }, follow_redirects=True)

    assert res.status_code == 200
    # Auth user was created in auth.users
    auth_user = next((au for au in db.auth_users if au.email == 'robust@example.com'), None)
    assert auth_user is not None


def test_admin_edit_user_role_and_program_rules(mock_env):
    """
    Test 7:
    Admin can edit role: Scheduler requires program, Viewer / Admin clears program.
    """
    client, db, admin_id = mock_env

    # Create a viewer
    v_id = str(uuid.uuid4())
    db.users.append({
        'id': v_id,
        'username': 'targetuser',
        'role': 'viewer',
        'first_name': 'Target',
        'last_name': 'User',
        'email': 'target@example.com',
        'program_id': None,
        'created_at': '2026-10-12T01:00:00Z',
    })

    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['role'] = 'admin'

    # Edit to Scheduler without program -> rejected 400
    res = client.post(f'/edit_user/{v_id}', data={
        'first_name': 'Target',
        'last_name': 'User',
        'email': 'target@example.com',
        'role': 'scheduler',
        'program': '',
    })
    assert res.status_code == 400
    assert 'Program is required' in res.get_json().get('message', '')

    # Edit to Chair (formerly Scheduler) with BSBA -> success
    res2 = client.post(f'/edit_user/{v_id}', data={
        'first_name': 'Target',
        'last_name': 'User',
        'email': 'target@example.com',
        'role': 'chair',
        'program': 'BSBA',
    })
    assert res2.status_code == 200
    target_row = next(u for u in db.users if u['id'] == v_id)
    assert target_row['role'] == 'chair'
    assert target_row['program_id'] == 2

    # Edit to Viewer with BSBA -> success (Viewer requires program)
    res3 = client.post(f'/edit_user/{v_id}', data={
        'first_name': 'Target',
        'last_name': 'User',
        'email': 'target@example.com',
        'role': 'viewer',
        'program': 'BSBA',
    })
    assert res3.status_code == 200
    target_row = next(u for u in db.users if u['id'] == v_id)
    assert target_row['role'] == 'viewer'
    assert target_row['program_id'] == 2

    # Edit to Admin -> program cleared to None (only Admin has NULL program)
    res4 = client.post(f'/edit_user/{v_id}', data={
        'first_name': 'Target',
        'last_name': 'User',
        'email': 'target@example.com',
        'role': 'admin',
        'program': '',
    })
    assert res4.status_code == 200
    target_row = next(u for u in db.users if u['id'] == v_id)
    assert target_row['role'] == 'admin'
    assert target_row['program_id'] is None


def test_newest_users_appear_first(mock_env):
    """
    Test 8:
    Users tab orders users with newest signups first.
    """
    client, db, admin_id = mock_env

    db.users = [
        {'id': '1', 'username': 'old', 'role': 'viewer', 'email': 'old@example.com', 'created_at': '2026-10-01T00:00:00Z'},
        {'id': '2', 'username': 'newest', 'role': 'viewer', 'email': 'newest@example.com', 'created_at': '2026-10-12T12:00:00Z'},
        {'id': '3', 'username': 'mid', 'role': 'viewer', 'email': 'mid@example.com', 'created_at': '2026-10-05T00:00:00Z'},
    ]

    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['role'] = 'admin'

    res = client.get('/users')
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    pos_newest = html.find('newest@example.com')
    pos_mid = html.find('mid@example.com')
    pos_old = html.find('old@example.com')

    assert pos_newest < pos_mid < pos_old, "Newest user must appear first in HTML table"

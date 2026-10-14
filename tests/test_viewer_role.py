import os
import json
import time
import pytest
from bs4 import BeautifulSoup
import app as app_module
from app import _normalize_role, ROUTE_PERMISSIONS


class FakeTableQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.last_action = None
        self.last_payload = None
        self.eq_filters = {}
        self.is_filters = {}
        self.limit_val = None

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

    def is_(self, column, value):
        self.is_filters[column] = value
        return self

    def limit(self, count):
        self.limit_val = count
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def neq(self, *args, **kwargs):
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
                    col_key = col.split('.')[-1] if '.' in col else col
                    r_val = r.get(col_key, r.get(col, ''))
                    if str(r_val).lower() != str(val).lower():
                        match = False
                        break
                if match:
                    filtered.append(dict(r))
            if self.limit_val is not None:
                filtered = filtered[:self.limit_val]
            return Response(filtered)

        if self.last_action == 'update':
            updated_rows = []
            for r in self.store.get(self.table_name, []):
                match = True
                for col, val in self.eq_filters.items():
                    if str(r.get(col)) != str(val):
                        match = False
                        break
                if match:
                    r.update(self.last_payload)
                    updated_rows.append(dict(r))
            return Response(updated_rows)

        if self.last_action in ('insert', 'upsert'):
            data = self.last_payload
            if isinstance(data, list):
                self.store.setdefault(self.table_name, []).extend(data)
            else:
                existing = None
                for idx, item in enumerate(self.store.setdefault(self.table_name, [])):
                    if item.get('id') == data.get('id') or item.get('email') == data.get('email'):
                        existing = idx
                        break
                if existing is not None:
                    self.store[self.table_name][existing].update(data)
                else:
                    self.store[self.table_name].append(data)
            return Response(data if isinstance(data, list) else [data])

        return Response([])


class FakePostgrest:
    def __init__(self):
        self.headers = {}
    def auth(self, token):
        self.headers['Authorization'] = f'Bearer {token}'
        return self


class FakeViewerSupabase:
    def __init__(self, store):
        self.store = store
        self.postgrest = FakePostgrest()

        class FakeAuth:
            def __init__(self, store_ref):
                self.store = store_ref

            def sign_up(self, credentials):
                email = credentials.get('email', '')
                user_metadata = (credentials.get('options') or {}).get('data') or {}
                auth_id = f"auth-{email.split('@')[0]}"
                # Simulate PostgreSQL handle_new_auth_user trigger creating profile row
                self.store.setdefault('users', []).append({
                    'id': auth_id,
                    'email': email,
                    'username': user_metadata.get('username') or email.split('@')[0],
                    'first_name': user_metadata.get('first_name', ''),
                    'last_name': user_metadata.get('last_name', ''),
                    'role': 'viewer',  # Server-side & trigger enforced
                    'program_id': None,
                })
                class AuthUser:
                    id = auth_id
                class AuthResp:
                    user = AuthUser()
                return AuthResp()

        self.auth = FakeAuth(self.store)

    def table(self, name):
        return FakeTableQuery(name, self.store)

    def rpc(self, fn_name, params=None):
        class RPCQuery:
            def __init__(self, store):
                self.store = store
            def execute(self):
                class Response:
                    def __init__(self, data):
                        self.data = data
                return Response([])
        return RPCQuery(self.store)


@pytest.fixture
def viewer_env(monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False

    store = {
        'users': [
            {
                'id': 'admin-uuid',
                'username': 'adminuser',
                'email': 'admin@test.com',
                'role': 'admin',
                'program_id': None,
                'program': None,
                'first_name': 'Admin',
                'last_name': 'User',
            },
            {
                'id': 'scheduler-uuid',
                'username': 'scheduser',
                'email': 'scheduler@test.com',
                'role': 'scheduler',
                'program_id': 1,
                'program': 'BSIT',
                'first_name': 'Sarah',
                'last_name': 'Scheduler',
            },
            {
                'id': 'viewer-uuid',
                'username': 'viewuser',
                'email': 'viewer@test.com',
                'role': 'viewer',
                'program_id': 1,
                'program': 'BSIT',
                'first_name': 'Victor',
                'last_name': 'Viewer',
            },
            {
                'id': 'viewer-noprog-uuid',
                'username': 'noproguser',
                'email': 'noprog@test.com',
                'role': 'viewer',
                'program_id': None,
                'program': None,
                'first_name': 'No',
                'last_name': 'Program',
            },
        ],
        'program': [
            {'id': 1, 'program_code': 'BSIT', 'program_code': 'BSIT'},
            {'id': 2, 'program_code': 'BSCS', 'program_code': 'BSCS'},
        ],
        'activity_log': [],
        'schedule': [
            {
                'schedule_id': 101,
                'section': 'BSIT-1A',
                'course_id': 1,
                'room_id': 1,
                'day': 'Monday',
                'start_time': '08:00',
                'session_type': 'Lecture',
                'archive': False,
                'program_id': 1,
            }
        ],
        'semester': [{'id': 1, 'term': '1st Semester', 'is_active': True}],
        'room': [{'id': 1, 'room_id': 1, 'room_name': 'Lab 1', 'room_type': 'Lecture'}],
        'course': [{'id': 1, 'course_id': 1, 'course_code': 'IT101', 'course_name': 'Intro to IT', 'program_id': 1}],
        'professor': [{'id': 1, 'first_name': 'Alan', 'last_name': 'Turing'}],
        'professor_load': [],
        'working_hours': [],
        'academic_ranking': [],
        'delete_requests': [],
    }

    fake_client = FakeViewerSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', fake_client)

    with app_module._USER_ROLE_CACHE_LOCK:
        app_module._USER_ROLE_CACHE.clear()
    app_module._LIVE_USER_ROLE_UPDATES.clear()

    return {'client': app_module.app.test_client(), 'store': store}


def _login_session(client, user_id, role, username='user', program_id=None, program=''):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['role'] = role
        sess['username'] = username
        sess['program_id'] = program_id
        sess['program'] = program
        sess['jwt_token'] = f"jwt-{user_id}"


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 & 2: Signup creates a Viewer with no program and ignores client inputs
# ──────────────────────────────────────────────────────────────────────────────

def test_signup_creates_viewer_with_no_program(viewer_env):
    """Rule 1: Every self-signup creates a profile with role='viewer' and program_id=NULL."""
    client = viewer_env['client']
    store = viewer_env['store']

    res = client.post('/signup', data={
        'first_name': 'Jane',
        'last_name': 'Doe',
        'email': 'jane.doe@example.com',
        'username': 'janedoe',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 302
    assert '/login' in res.location or '/schedules' in res.location

    # Verify profile created in database store
    created_user = next((u for u in store['users'] if u.get('email') == 'jane.doe@example.com'), None)
    assert created_user is not None
    assert created_user.get('role') == 'viewer'
    assert created_user.get('program_id') is None


def test_signup_ignores_client_supplied_role_and_program(viewer_env):
    """Rule 1: A signup request sending role=admin or program_id still creates a Viewer with program_id=None."""
    client = viewer_env['client']
    store = viewer_env['store']

    res = client.post('/signup', data={
        'first_name': 'Attacker',
        'last_name': 'Smith',
        'email': 'attacker@example.com',
        'username': 'attack1',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
        'role': 'admin',                 # Client attempted privilege escalation
        'program': 'BSIT',                # Client attempted program assignment
        'program_id': '1',
        'user_metadata': json.dumps({'role': 'super_admin'}),
    })
    assert res.status_code == 302

    created_user = next((u for u in store['users'] if u.get('email') == 'attacker@example.com'), None)
    assert created_user is not None
    # Must be forced to viewer and NULL program_id regardless of form data
    assert created_user.get('role') == 'viewer'
    assert created_user.get('program_id') is None


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: Database Privilege Escalation Protection (Migration & Trigger Check)
# ──────────────────────────────────────────────────────────────────────────────

def test_migration_and_privilege_escalation_guards():
    """Rule 1: Check idempotent migration for role default, backfill, trigger, RLS, and schema reload."""
    migration_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', '20261011_viewer_role.sql')
    assert os.path.exists(migration_path), f"Migration file not found at {migration_path}"

    with open(migration_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    # 1. BEGIN and COMMIT transaction structure
    assert 'BEGIN;' in sql
    assert 'COMMIT;' in sql

    # 2. Schema facts: role default 'viewer' and backfill
    assert "ALTER TABLE public.users ALTER COLUMN role SET DEFAULT 'viewer';" in sql
    assert "UPDATE public.users" in sql and "SET role = 'viewer'" in sql

    # 3. Security definer helper with fixed search_path
    assert "FUNCTION public.get_user_role()" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, auth, pg_temp" in sql
    # Must NOT trust user_metadata claim for role
    assert "auth.jwt() -> 'user_metadata' ->> 'role'" not in sql

    # 4. Privilege escalation protection trigger
    assert "protect_user_role_and_program" in sql
    assert "trg_protect_user_role_and_program" in sql
    assert "BEFORE UPDATE ON public.users" in sql

    # 5. Schema reload notification
    assert "NOTIFY pgrst, 'reload schema';" in sql


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Viewer is blocked with HTTP 403 on Professor Schedule and Management Routes
# ──────────────────────────────────────────────────────────────────────────────

def test_viewer_403_on_professor_schedule_and_management_routes(viewer_env):
    """Rule 3: Viewer gets 403 on professor schedule (page, API, exports) and at least 10 management URLs."""
    client = viewer_env['client']
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser')

    # Professor Schedule page, APIs, and exports MUST return 403
    prof_endpoints = [
        '/professor_schedule',
        '/api/professor_availability/1',
        '/api/professor_workload/1',
        '/professor_schedule/1/export',
        '/professor_schedule/1/export_pdf',
        '/export/professor_schedule/1',
        '/export/professor_schedule/1/pdf',
    ]

    for ep in prof_endpoints:
        res = client.get(ep)
        assert res.status_code == 403, f"Viewer should get 403 on {ep}, got {res.status_code}"

    # Section & Room exports MUST also return 403 for Viewers
    export_endpoints = [
        '/schedule/BSIT-1A/export',
        '/schedule/BSIT-1A/export_pdf',
        '/export/section_schedule/BSIT-1A',
        '/export/section_schedule/BSIT-1A/pdf',
        '/room_schedule/1/export',
        '/room_schedule/1/export_pdf',
        '/export/room_schedule/1',
        '/export/room_schedule/1/pdf',
    ]
    for ep in export_endpoints:
        res = client.get(ep)
        assert res.status_code == 403, f"Viewer should get 403 on export endpoint {ep}, got {res.status_code}"

    # At least 10 management URLs typed directly MUST return 403
    management_endpoints = [
        ('GET', '/programs'),
        ('GET', '/courses'),
        ('GET', '/rooms'),
        ('GET', '/working_hours'),
        ('GET', '/users'),
        ('GET', '/activity_log'),
        ('GET', '/backup'),
        ('POST', '/restore'),
        ('POST', '/confirm_preview'),
        ('GET', '/schedule_archive'),
        ('GET', '/generate_schedule'),
        ('GET', '/preview_schedule'),
        ('GET', '/professor_load'),
        ('GET', '/professor_load/import/template'),
        ('POST', '/notifications/mark_read'),
    ]

    for method, url in management_endpoints:
        if method == 'POST':
            res = client.post(url)
        else:
            res = client.get(url)
        assert res.status_code == 403, f"Viewer should receive 403 on management URL {url}, got {res.status_code}"
        # Verify friendly 403 error page structure with link back to schedules for HTML requests
        if 'text/html' in res.content_type:
            soup = BeautifulSoup(res.data.decode('utf-8', errors='ignore'), 'html.parser')
            assert 'Access Denied' in soup.get_text() or '403' in soup.get_text()
            back_link = soup.find('a', href=lambda h: h and '/schedules' in h)
            assert back_link is not None, f"Friendly 403 page on {url} should contain link back to /schedules"


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: Viewer can view schedules, but export features are hidden and blocked
# ──────────────────────────────────────────────────────────────────────────────

def test_viewer_schedules_viewable_but_exports_blocked(viewer_env, monkeypatch):
    """Rule 3: Viewer can view Section and Room schedules, but CANNOT export (Excel/PDF hidden & 403)."""
    client = viewer_env['client']
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser', program_id=1, program='BSIT')

    # 1. Section Schedule (view allowed, read-only)
    res_sec = client.get('/schedules')
    assert res_sec.status_code == 200
    soup_sec = BeautifulSoup(res_sec.data.decode('utf-8'), 'html.parser')

    # Read-only check: No create/edit/archive buttons
    assert soup_sec.find(lambda t: t.name == 'a' and 'Generate New Schedule' in t.get_text()) is None
    assert soup_sec.find('button', class_='delete-schedule-btn') is None
    assert soup_sec.find('button', class_='archive-schedule-btn') is None

    # Export buttons MUST NOT be visible for Viewers on /schedules
    assert soup_sec.find('a', href=lambda h: h and '/export' in h) is None
    assert soup_sec.find('a', href=lambda h: h and '/export_pdf' in h) is None

    # Viewers can switch programs (All Programs / BSDS / BSIT)
    assert soup_sec.find('select', id='programFilter') is not None
    assert soup_sec.find('select', id='yearFilter') is not None
    assert soup_sec.find('select', id='semesterFilter') is not None

    # Admin CAN switch programs
    _login_session(client, 'admin-uuid', 'admin', 'adminuser')
    res_admin = client.get('/schedules')
    assert res_admin.status_code == 200
    soup_admin = BeautifulSoup(res_admin.data.decode('utf-8'), 'html.parser')
    assert soup_admin.find('select', id='programFilter') is not None

    # Re-login as viewer for remaining checks
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser', program_id=1, program='BSIT')

    # 2. Section detail view /schedule/BSIT-1A (view allowed, export hidden)
    res_detail = client.get('/schedule/BSIT-1A')
    assert res_detail.status_code == 200
    soup_detail = BeautifulSoup(res_detail.data.decode('utf-8'), 'html.parser')
    assert soup_detail.find('a', id='exportExcelBtn') is None
    assert soup_detail.find('select', id='exportThemeSelect') is None

    # 3. Room Schedule list & detail view (view allowed, export hidden)
    res_room = client.get('/room_schedule')
    assert res_room.status_code == 200

    res_room_detail = client.get('/room_schedule/1')
    assert res_room_detail.status_code == 200
    soup_room = BeautifulSoup(res_room_detail.data.decode('utf-8'), 'html.parser')
    assert soup_room.find('a', href=lambda h: h and '/export' in h) is None

    # 4. Direct export attempts by Viewer MUST return HTTP 403
    for path in ['/schedule/BSIT-1A/export', '/schedule/BSIT-1A/export_pdf', '/room_schedule/1/export', '/room_schedule/1/export_pdf']:
        res = client.get(path)
        assert res.status_code == 403, f"Viewer direct export to {path} should be 403, got {res.status_code}"

    # 5. Schedulers and Admins CAN still export
    import io
    monkeypatch.setattr(app_module, 'generate_timetable_excel', lambda *args, **kwargs: io.BytesIO(b'fake-excel-data'))
    monkeypatch.setattr(app_module, 'generate_timetable_pdf', lambda *args, **kwargs: io.BytesIO(b'fake-pdf-data'))

    _login_session(client, 'scheduler-uuid', 'scheduler', 'scheduser', program_id=1, program='BSIT')
    assert client.get('/schedule/BSIT-1A/export').status_code == 200
    assert client.get('/schedule/BSIT-1A/export_pdf').status_code == 200
    assert client.get('/room_schedule/1/export').status_code == 200
    assert client.get('/room_schedule/1/export_pdf').status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# Test 6: Admin edits Viewer to Scheduler for BSIT (gains access without re-signup)
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_promotes_viewer_to_scheduler_immediately(viewer_env):
    """Rule 2: Admin edits Viewer to Scheduler for BSIT -> user gains scheduling on next request."""
    client = viewer_env['client']
    store = viewer_env['store']

    # Log in as Admin
    _login_session(client, 'admin-uuid', 'admin', 'adminuser')

    # Admin promotes viewer-uuid to Chair (formerly Scheduler) for BSIT (program_id=1)
    res_edit = client.post('/edit_user/viewer-uuid', data={
        'first_name': 'Victor',
        'last_name': 'Viewer',
        'email': 'viewer@test.com',
        'role': 'chair',
        'program_id': '1',
    })
    assert res_edit.status_code == 200
    res_json = res_edit.get_json()
    assert res_json.get('success') is True

    # Check activity_log was written with actor, target user, old role, new role, program
    act_logs = store['activity_log']
    assert len(act_logs) > 0
    role_change_log = next((l for l in act_logs if 'role' in l.get('target_detail', '').lower()), None)
    assert role_change_log is not None
    assert 'viewer' in role_change_log['target_detail'].lower()
    assert 'chair' in role_change_log['target_detail'].lower()
    assert 'bsit' in role_change_log['target_detail'].lower()

    # Now the user (viewer-uuid) makes their next request
    # With same session / token, live cache / DB refresh grants them chair access immediately
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser')  # Session still had old 'viewer' cached

    # Accessing chair page /professor_load
    res_next = client.get('/professor_load')
    # Should NOT be 403 Forbidden because live role check refreshed role to chair
    assert res_next.status_code != 403
    assert res_next.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# Test 7: Admin demotes Scheduler to Viewer (access removed immediately, requires program)
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_demotes_scheduler_to_viewer_immediately(viewer_env):
    """Rule 2: Admin sets Scheduler back to Viewer -> requires program, access is removed on next request."""
    client = viewer_env['client']
    store = viewer_env['store']

    # Log in as Admin
    _login_session(client, 'admin-uuid', 'admin', 'adminuser')

    # Demoting to Viewer no longer requires a program; program_id is cleared.
    res_demote = client.post('/edit_user/scheduler-uuid', data={
        'first_name': 'Sarah',
        'last_name': 'Scheduler',
        'email': 'scheduler@test.com',
        'role': 'viewer',
        'program_id': '',
    })
    assert res_demote.status_code == 200
    assert res_demote.get_json().get('success') is True

    sched_row = next((u for u in store['users'] if u.get('id') == 'scheduler-uuid'), None)
    assert sched_row is not None
    assert sched_row.get('role') == 'viewer'
    assert sched_row.get('program_id') is None

    # Setting to Admin keeps program_id None
    res_admin = client.post('/edit_user/scheduler-uuid', data={
        'first_name': 'Sarah',
        'last_name': 'Scheduler',
        'email': 'scheduler@test.com',
        'role': 'admin',
        'program_id': '',
    })
    assert res_admin.status_code == 200
    assert sched_row.get('role') == 'admin'
    assert sched_row.get('program_id') is None

    # Reset back to viewer
    client.post('/edit_user/scheduler-uuid', data={
        'first_name': 'Sarah',
        'last_name': 'Scheduler',
        'email': 'scheduler@test.com',
        'role': 'viewer',
        'program_id': '',
    })

    # Now the user (scheduler-uuid) makes their next request with existing session
    _login_session(client, 'scheduler-uuid', 'scheduler', 'scheduser', program_id=1, program='BSIT')

    # Must be immediately blocked on scheduler route /professor_load
    res_blocked = client.get('/professor_load')
    assert res_blocked.status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# Test 8: Landing page redirects Viewer to /schedules
# ──────────────────────────────────────────────────────────────────────────────

def test_viewer_landing_redirects_to_schedules(viewer_env):
    """Rule 3.4: After login and when a Viewer opens '/', redirect to Section Schedule."""
    client = viewer_env['client']
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser')

    res = client.get('/')
    assert res.status_code == 302
    assert '/schedules' in res.headers.get('Location', '')


# ──────────────────────────────────────────────────────────────────────────────
# Test 9: Viewer program scoping and missing-program alert
# ──────────────────────────────────────────────────────────────────────────────

def test_viewer_sees_all_programs(viewer_env):
    """Viewers are global: no program required, program filter available, can narrow."""
    client = viewer_env['client']

    # 1. Viewer with no program still sees schedules (no "Program Required" alert).
    _login_session(client, 'viewer-noprog-uuid', 'viewer', 'noproguser', program_id=None, program=None)
    res_no_prog = client.get('/schedules')
    assert res_no_prog.status_code == 200
    html_no_prog = res_no_prog.data.decode('utf-8')
    assert "No program is assigned to your account. Please contact an administrator." not in html_no_prog
    soup = BeautifulSoup(html_no_prog, 'html.parser')
    assert soup.find('select', id='programFilter') is not None

    # 2. Viewer with program_id=1 can still switch programs (no 403).
    _login_session(client, 'viewer-uuid', 'viewer', 'viewuser', program_id=1, program='BSIT')
    res_other = client.get('/schedules?program=BSBA')
    assert res_other.status_code == 200

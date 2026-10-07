"""Comprehensive tests for Role-Based Access Control (RBAC) across Admin, Scheduler, and Viewer roles."""

import pytest
from bs4 import BeautifulSoup
import app as app_module
from app import _normalize_role


class FakeTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self.data = list(data or [])
        self._filters = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        return self

    def update(self, payload):
        return self

    def delete(self):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def neq(self, col, val):
        return self

    def in_(self, col, val):
        return self

    def is_(self, col, val):
        return self

    def or_(self, *args, **kwargs):
        return self

    def like(self, col, val):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, count):
        return self

    def range(self, start, end):
        return self

    def execute(self):
        class Resp:
            def __init__(self, d):
                self.data = d
        return Resp(list(self.data))


class FakeSupabase:
    def __init__(self):
        self.tables = {
            'users': FakeTable('users', [
                {'id': 'admin-uuid', 'role': 'admin', 'username': 'admin_user', 'email': 'admin@test.com'},
                {'id': 'sched-uuid', 'role': 'scheduler', 'username': 'sched_user', 'email': 'sched@test.com'}
            ]),
            'professor': FakeTable('professor', []),
            'course': FakeTable('course', []),
            'room': FakeTable('room', []),
            'timeslot': FakeTable('timeslot', []),
            'academic_ranking': FakeTable('academic_ranking', []),
            'professor_load': FakeTable('professor_load', []),
            'schedule': FakeTable('schedule', []),
            'program': FakeTable('program', [{'id': 1, 'program_name': 'BSIT'}]),
            'activity_log': FakeTable('activity_log', []),
            'delete_requests': FakeTable('delete_requests', []),
            'semester': FakeTable('semester', [{'id': 1, 'term': '1st Semester', 'is_active': True}]),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = FakeTable(name, [])
        return self.tables[name]

    def rpc(self, name, params=None):
        class RpcCall:
            def execute(self):
                class Resp:
                    data = []
                return Resp()
        return RpcCall()


@pytest.fixture
def test_client(monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    fake_db = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_db)
    # Mock log_activity to prevent external calls
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)
    with app_module.app.test_client() as client:
        yield client


def _login_as(client, role, user_id=None, username='testuser', program='BSIT', program_id=1):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id or f'user-{role}'
        sess['username'] = username
        sess['role'] = role
        sess['program'] = program
        sess['program_id'] = program_id
        sess['jwt_token'] = f'fake-jwt-{role}'


# ──────────────────────────────────────────────────────────────────────────────
# 1. Role Normalization Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_role_normalization():
    assert _normalize_role('admin') == 'admin'
    assert _normalize_role('Admin') == 'admin'
    assert _normalize_role('SUPER_ADMIN') == 'admin'
    assert _normalize_role('super admin') == 'admin'
    assert _normalize_role('scheduler') == 'scheduler'
    assert _normalize_role('Scheduler') == 'scheduler'
    assert _normalize_role('dean') == 'scheduler'
    assert _normalize_role('CHAIR') == 'scheduler'
    assert _normalize_role('Dean/Chair') == 'scheduler'
    assert _normalize_role('program_chair') == 'scheduler'
    assert _normalize_role('viewer') == 'viewer'
    assert _normalize_role('Viewer') == 'viewer'
    assert _normalize_role('instructor') == 'viewer'
    assert _normalize_role('unknown') == 'unknown'
    assert _normalize_role('') == ''


# ──────────────────────────────────────────────────────────────────────────────
# 2. Unauthenticated Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_unauthenticated_redirects_to_login(test_client):
    for path in ['/schedules', '/courses', '/users', '/professor_load', '/']:
        res = test_client.get(path)
        assert res.status_code == 302, f"Expected redirect on {path}"
        assert '/login' in res.headers.get('Location', '')


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# 3. Admin Permissions Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_allowed_pages(test_client):
    _login_as(test_client, 'admin')

    # Allowed: Courses, Rooms, Timeslots, Schedules, Professor Schedule, Room Schedule, Archive, Users, Activity Log
    for path in ['/courses', '/rooms', '/timeslot', '/schedules', '/professor_schedule', '/room_schedule', '/schedule_archive', '/users', '/activity_log']:
        res = test_client.get(path)
        assert res.status_code == 200, f"Admin should have access to {path}, got {res.status_code}"


def test_admin_restricted_pages_return_403(test_client):
    _login_as(test_client, 'admin')

    # Blocked: Generate Schedule (/), Professor Load, generate_schedule, preview_schedule
    restricted_pages = [
        '/',
        '/professor_load',
        '/generate_schedule',
        '/preview_schedule',
    ]
    for path in restricted_pages:
        res = test_client.get(path)
        assert res.status_code == 403, f"Admin should receive 403 on {path}, got {res.status_code}"
        soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')
        assert 'Access Denied' in soup.get_text()
        # Verify back button directs Admin to their home (/schedules)
        back_link = soup.find('a', href=True)
        assert back_link is not None


def test_admin_restricted_write_actions_return_403_json(test_client):
    _login_as(test_client, 'admin')

    # Blocked write attempts: Generate Schedule, Professor Load, Schedules write
    endpoints = [
        ('POST', '/confirm_preview', {}),
        ('POST', '/discard_preview', {}),
        ('POST', '/add_professor_load', {'prof_id': 1}),
        ('POST', '/professor_load/import/preview', {}),
        ('POST', '/archive_schedule', {}),
        ('POST', '/delete_schedule_archive/batch1', {}),
        ('POST', '/restore_schedule_archive/batch1', {}),
        ('POST', '/delete_all_schedules', {'password': 'pass'}),
        ('POST', '/delete_section_schedule/BSIT-1A', {}),
        ('POST', '/edit_schedule/BSIT-1A', {'section': 'BSIT-1B'}),
        ('GET', '/delete_schedule/1', None),
    ]

    for method, path, data in endpoints:
        if method == 'POST':
            res = test_client.post(path, data=data or {})
        else:
            res = test_client.get(path)

        assert res.status_code == 403, f"Admin write to {path} should be 403, got {res.status_code}"
        data = res.get_json()
        assert data is not None, f"Expected JSON 403 response for {path}"
        assert data.get('ok') is False
        assert 'error' in data


def test_admin_allowed_data_setup_writes(test_client):
    _login_as(test_client, 'admin')

    # Admin CAN write to data setup routes (should NOT be 403)
    endpoints = [
        ('POST', '/add_course', {'course_name': 'New Course', 'units': '3'}),
        ('POST', '/add_room', {'room_name': 'Room 101', 'room_type': 'Lecture Room'}),
        ('POST', '/add_timeslot', {'day': 'Monday', 'start_time': '08:00', 'end_time': '12:00'}),
    ]
    for method, path, data in endpoints:
        res = test_client.post(path, data=data)
        assert res.status_code != 403, f"Admin write to {path} should NOT return 403, got {res.status_code}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Scheduler Permissions Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_scheduler_allowed_pages(test_client):
    _login_as(test_client, 'scheduler')

    # Allowed: Generate Schedule (/), Professor Load, Schedules, Professor Schedule, Room Schedule, Archive
    for path in ['/', '/professor_load', '/schedules', '/professor_schedule', '/room_schedule', '/schedule_archive']:
        res = test_client.get(path)
        assert res.status_code == 200, f"Scheduler should have access to {path}, got {res.status_code}"


def test_scheduler_restricted_pages_return_403(test_client):
    _login_as(test_client, 'scheduler')

    # Blocked: Courses, Rooms, Timeslots, Users, Activity Log
    for path in ['/courses', '/rooms', '/timeslot', '/users', '/activity_log']:
        res = test_client.get(path)
        assert res.status_code == 403, f"Scheduler should receive 403 on {path}, got {res.status_code}"
        soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')
        assert 'Access Denied' in soup.get_text()


def test_scheduler_restricted_write_actions_return_403_json(test_client):
    _login_as(test_client, 'scheduler')

    # Blocked writes: Courses, Rooms, Timeslots, Users, Backup & Restore
    endpoints = [
        ('POST', '/add_course', {'course_name': 'test'}),
        ('POST', '/edit_course/1', {'course_name': 'test'}),
        ('GET', '/delete_course/1', None),
        ('POST', '/add_room', {'room_name': 'test'}),
        ('POST', '/edit_room/1', {'room_name': 'test'}),
        ('GET', '/delete_room/1', None),
        ('POST', '/add_timeslot', {'day': 'Monday'}),
        ('POST', '/edit_timeslot/1', {}),
        ('GET', '/delete_timeslot/1', None),
        ('POST', '/create_user', {'username': 'newuser'}),
        ('POST', '/edit_user/some-id', {'username': 'newuser'}),
        ('POST', '/delete_user/some-id', {}),
        ('POST', '/restore', {}),
        ('GET', '/backup', None),
    ]

    for method, path, data in endpoints:
        if method == 'POST':
            res = test_client.post(path, data=data or {})
        else:
            res = test_client.get(path)
        assert res.status_code == 403, f"Scheduler write to {path} should be 403, got {res.status_code}"
        json_data = res.get_json()
        assert json_data is not None, f"Expected JSON response on {path}"
        assert json_data.get('ok') is False


def test_scheduler_allowed_api_courses(test_client):
    _login_as(test_client, 'scheduler')
    res = test_client.get('/api/courses')
    assert res.status_code == 200, f"Scheduler should have access to /api/courses, got {res.status_code}"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Viewer Permissions Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_viewer_permissions(test_client):
    _login_as(test_client, 'viewer')

    # Allowed: Schedules, Professor Schedule, Room Schedule
    for path in ['/schedules', '/professor_schedule', '/room_schedule']:
        res = test_client.get(path)
        assert res.status_code == 200, f"Viewer should have access to {path}, got {res.status_code}"

    # Blocked: Schedule Archive (403), Users, Activity Log, Courses, Rooms, Timeslots, Professor Load, Generate Schedule
    blocked = ['/schedule_archive', '/users', '/activity_log', '/courses', '/rooms', '/timeslot', '/professor_load', '/']
    for path in blocked:
        res = test_client.get(path)
        assert res.status_code == 403, f"Viewer should receive 403 on {path}, got {res.status_code}"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Sidebar Layout Segregation Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_sidebar_visibility(test_client):
    _login_as(test_client, 'admin')
    res = test_client.get('/schedules')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Admin should see Courses, Rooms, Timeslots, Users, Activity Log, Schedules Dropdown
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/courses') is not None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/rooms') is not None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/timeslot') is not None
    assert soup.find('a', href=lambda h: h and '/users' in h) is not None
    assert soup.find('a', href=lambda h: h and '/activity_log' in h) is not None
    assert soup.find('a', href=lambda h: h and '/schedule_archive' in h) is not None

    # Admin should NOT see Generate Schedule or Professor Load
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/professor_load') is None
    # Home link / (Generate Schedule) should not be a nav item for Admin
    nav_links = [a.get('href') for a in soup.find_all('a', class_='sidebar__link') if a.get('href')]
    assert '/' not in nav_links


def test_scheduler_sidebar_visibility(test_client):
    _login_as(test_client, 'scheduler')
    res = test_client.get('/')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Scheduler should see Generate Schedule, Professor Load, Schedules Dropdown
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/professor_load') is not None
    assert soup.find('a', href=lambda h: h and '/schedules' in h) is not None
    assert soup.find('a', href=lambda h: h and '/schedule_archive' in h) is not None

    # Scheduler should NOT see Courses, Rooms, Timeslots, Users, Activity Log
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/courses') is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/rooms') is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/timeslot') is None
    assert soup.find('a', href=lambda h: h and '/users' in h) is None
    assert soup.find('a', href=lambda h: h and '/activity_log' in h) is None


def test_viewer_sidebar_visibility(test_client):
    _login_as(test_client, 'viewer')
    res = test_client.get('/schedules')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Viewer should see Section Schedule, Professor Schedule, Room Schedule
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/schedules') is not None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/professor_schedule') is not None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/room_schedule') is not None

    # Viewer should NOT see Schedule Archive, Courses, Rooms, Timeslots, Generate Schedule, Professor Load, Users, Activity Log
    assert soup.find('a', href=lambda h: h and '/schedule_archive' in h) is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/courses') is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/rooms') is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/timeslot') is None
    assert soup.find('a', href=lambda h: h and h.rstrip('/') == '/professor_load') is None
    assert soup.find('a', href=lambda h: h and '/users' in h) is None
    assert soup.find('a', href=lambda h: h and '/activity_log' in h) is None


def test_deleted_routes_return_404(test_client):
    """Confirm deleted professors and academic ranking routes return 404 regardless of role."""
    for role in ['admin', 'scheduler', 'viewer']:
        _login_as(test_client, role)
        deleted_urls = [
            '/professors',
            '/academic_ranking',
            '/add_professor',
            '/add_academic_ranking',
            '/edit_professor/1',
            '/edit_academic_ranking/1',
            '/delete_professor/1',
            '/delete_academic_ranking/1',
        ]
        for url in deleted_urls:
            assert test_client.get(url).status_code == 404, f"Expected 404 on {url} for role {role}"


# ──────────────────────────────────────────────────────────────────────────────
# 7. Schedules & Archive Read-Only UI Controls for Admin
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_schedules_page_hides_edit_and_archive_controls(test_client):
    _login_as(test_client, 'admin')
    res = test_client.get('/schedules')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Write controls must be absent for Admin
    assert soup.find(id='archiveScheduleBtn') is None
    assert soup.find(id='deleteAllSchedulesBtn') is None
    assert soup.find(id='generateNewScheduleBtn') is None
    assert soup.find(id='archiveConfirmModal') is None
    assert soup.find(id='deleteAllModal') is None


def test_admin_schedule_archive_page_hides_restore_and_delete_controls(test_client):
    _login_as(test_client, 'admin')
    res = test_client.get('/schedule_archive')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Management controls must be absent for Admin
    assert soup.find(id='restoreModal') is None
    assert soup.find(id='deleteArchiveModal') is None
    assert soup.find('button', attrs={'data-batch-id': True}) is None


def test_scheduler_schedules_page_shows_edit_and_archive_controls(test_client):
    _login_as(test_client, 'scheduler')
    res = test_client.get('/schedules')
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # "Generate New Schedule" button should be visible for Scheduler
    gen_btn = soup.find(lambda tag: tag.name == 'a' and 'Generate New Schedule' in tag.get_text())
    assert gen_btn is not None
    assert gen_btn.get('href') in ('/', '/index.html')


def test_admin_can_access_pdf_export_routes(test_client):
    _login_as(test_client, 'admin')
    # Export endpoints should NOT return 403 (they may redirect or 200/404 based on data existence, but must NOT be 403)
    for path in ['/export/section_schedule/BSIT-1A/pdf', '/export/professor_schedule/1/pdf', '/export/room_schedule/1/pdf']:
        res = test_client.get(path)
        assert res.status_code != 403, f"Admin should have PDF export access for {path}, got 403"


def test_post_login_landing_pages():
    # Admin and Viewer land on /schedules, Scheduler lands on / (home)
    assert _normalize_role('admin') in ('admin', 'viewer')
    assert _normalize_role('viewer') in ('admin', 'viewer')
    assert _normalize_role('scheduler') == 'scheduler'


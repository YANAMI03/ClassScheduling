import pytest
import app as app_module
import re


class MockTable:
    def __init__(self, name, data=None):
        self.name = name
        self.data = data or []
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filters = {}
        self._select_cols = '*'

    def select(self, cols='*', **kwargs):
        self._select_cols = cols
        return self

    def insert(self, rows):
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows:
            record = dict(r)
            if 'id' not in record and 'course_id' not in record and 'prof_id' not in record:
                record['id'] = len(self.data) + len(self.inserted) + 100
            self.inserted.append(record)
            self.data.append(record)
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def delete(self):
        self._is_delete = True
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def neq(self, col, val):
        return self

    def in_(self, col, vals):
        return self

    def ilike(self, col, val):
        return self

    def order(self, col, desc=False):
        return self

    def or_(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def single(self):
        return self

    def execute(self):
        if getattr(self, '_update_payload', None) is not None:
            payload = self._update_payload
            self._update_payload = None
            affected = []
            for row in self.data:
                match = True
                for k, v in self._filters.items():
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if match:
                    row.update(payload)
                    affected.append(dict(row))
            self._filters = {}
            class UpdResp:
                def __init__(self, d):
                    self.data = d
                    self.count = len(d)
            return UpdResp(affected)

        if getattr(self, '_is_delete', False):
            self._is_delete = False
            for row in list(self.data):
                match = True
                for k, v in self._filters.items():
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if match:
                    self.deleted.append(row)
                    self.data.remove(row)
            self._filters = {}
            class DelResp:
                data = []
                count = 0
            return DelResp()

        filtered = list(self.data)
        for k, v in self._filters.items():
            filtered = [r for r in filtered if str(r.get(k)) == str(v)]
        self._filters = {}

        class Resp:
            def __init__(self, d):
                self.data = d
                self.count = len(d)
        return Resp(filtered)


class MockSupabase:
    def __init__(self):
        self.tables = {
            'program': MockTable('program', [
                {'id': 1, 'program_name': 'BSIT', 'department': 'CICT'},
                {'id': 2, 'program_name': 'BSBA', 'department': 'CMBT'},
            ]),
            'semester': MockTable('semester', [
                {'id': 10, 'program_id': 1, 'school_year': '2026-2027', 'term': '1st Semester', 'is_active': True},
                {'id': 11, 'program_id': 1, 'school_year': '2026-2027', 'term': '2nd Semester', 'is_active': False},
            ]),
            'section_config': MockTable('section_config', [
                {'id': 1, 'semester_id': 10, 'year_level': 1, 'number_of_sections': 3},
                {'id': 2, 'semester_id': 10, 'year_level': 2, 'number_of_sections': 2},
            ]),
            'course': MockTable('course', [
                {'course_id': 1, 'course_name': 'IT101', 'units': 3, 'lecture_hours': 3, 'lab_hours': 0, 'year_level': '1', 'program': 'BSIT', 'program_id': 1, 'semester_id': 10, 'semester': '1st Semester'},
            ]),
            'professor': MockTable('professor', [
                {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'department': 'CICT', 'program_id': 1, 'time_designation': 4, 'academic_ranking_id': 1},
                {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'department': 'CICT', 'program_id': 1, 'time_designation': 5, 'academic_ranking_id': 1},
            ]),
            'academic_ranking': MockTable('academic_ranking', [
                {'academic_ranking_id': 1, 'name': 'Professor I', 'program_id': 1, 'program': 'BSIT', 'min_units': 12, 'max_units': 24, 'min_hours': 20, 'max_hours': 40},
            ]),
            'room': MockTable('room', [
                {'room_id': 1, 'room_name': 'Lab 101', 'room_type': 'Laboratory', 'department': 'CICT', 'program_id': 1},
            ]),
            'timeslot': MockTable('timeslot', [
                {'timeslot_id': 1, 'semester_id': 10, 'start_day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'},
            ]),
            'professor_load': MockTable('professor_load', []),
            'schedule': MockTable('schedule', []),
            'users': MockTable('users', [
                {'id': 1, 'email': 'scheduler@example.com', 'username': 'sched', 'role': 'Scheduler', 'program_id': 1},
                {'id': 2, 'email': 'dean@example.com', 'username': 'dean_it', 'role': 'Dean', 'program_id': 1},
                {'id': 3, 'email': 'admin@example.com', 'username': 'admin', 'role': 'super_admin', 'program_id': None},
            ]),
            'delete_requests': MockTable('delete_requests', []),
            'activity_log': MockTable('activity_log', []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name, [])
        return self.tables[name]


@pytest.fixture
def mock_db(monkeypatch):
    db = MockSupabase()
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)
    return db


def test_scheduler_nav_has_exactly_four_tabs(mock_db):
    """Requirement 8: Limit Scheduler UI to exactly 4 tabs with zero CRUD."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    resp = client.get('/schedules')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_hrefs = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*\b(?:sidebar__link|nav__link)\b[^"\']*["\']', html)
    if not nav_hrefs:
        nav_hrefs = re.findall(r'<a\s+[^>]*class=["\'][^"\']*\b(?:sidebar__link|nav__link)\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', html)

    # Filter out logout / settings if present
    feature_links = [h for h in nav_hrefs if not any(x in h for x in ('logout', 'profile', 'login', '#'))]

    # Scheduler should see functional tabs: Generate (/), Schedules (/schedules), Professor Schedule, Room Schedule, Courses, Professors
    expected_endpoints = ['/', '/schedules', '/professor_schedule', '/room_schedule']
    for ep in expected_endpoints:
        assert any(ep == h or h.endswith(ep) for h in feature_links), f"Expected endpoint {ep} in scheduler nav"

    # Must NOT have administrative links
    forbidden_endpoints = ['/rooms', '/timeslot', '/academic_ranking', '/semesters', '/section_config', '/users']
    for f_ep in forbidden_endpoints:
        assert not any(f_ep == h or h.endswith(f_ep) for h in feature_links), f"Forbidden endpoint {f_ep} found in scheduler nav"


def test_scheduler_zero_crud_enforcement(mock_db):
    """Scheduler must be denied CUD operations across admin-only entities like rooms."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # POST to Add Room -> Denied (403)
    r3 = client.post('/add_room', data={'room_name': 'Room 999', 'room_type': 'Lecture'})
    assert r3.status_code == 403

    # POST to Save Section Config -> Removed (404)
    r4 = client.post('/save_section_config', data={'semester_id': 10, 'sections_1': 5})
    assert r4.status_code == 404

    # POST to Add Semester -> Removed (404)
    r5 = client.post('/add_semester', data={'school_year': '2027-2028', 'term': '1st Semester'})
    assert r5.status_code == 404


def test_scheduler_read_only_access(mock_db):
    """Scheduler has no access to Courses or Rooms (both Admin-only, Scheduler receives 403)."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    assert client.get('/courses').status_code == 403
    assert client.get('/rooms').status_code == 403

    # Admin CAN view courses and rooms
    with client.session_transaction() as session:
        session['role'] = 'Admin'

    for path in ['/courses', '/rooms']:
        resp = client.get(path)
        assert resp.status_code == 200, f"Admin should be able to view {path}"


def test_semester_and_section_config_routes_removed(mock_db):
    """Change 1: Semester and section_config creation routes and tables removed."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['username'] = 'dean_it'
        session['role'] = 'Dean'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # Endpoints must return 404
    assert client.get('/semesters').status_code == 404
    assert client.post('/add_semester', data={'term': '1st Semester'}).status_code == 404
    assert client.get('/section_config').status_code == 404
    assert client.post('/save_section_config', data={'sections_1': 3}).status_code == 404


def test_professor_time_designation_day_capping():
    """Requirement 6: Support professor time_designation (4 vs 5 days) in schedule generation."""
    prof_4_days = {'prof_id': 10, 'first_name': 'Alan', 'time_designation': 4}
    prof_5_days = {'prof_id': 20, 'first_name': 'Grace', 'time_designation': 5}

    # Simulate scheduled days
    scheduled_days = {
        10: {'Monday', 'Tuesday', 'Wednesday', 'Thursday'},
        20: {'Monday', 'Tuesday', 'Wednesday', 'Thursday'},
    }

    def can_teach(prof, day):
        pid = prof['prof_id']
        days = scheduled_days.get(pid, set())
        if day in days:
            return True
        max_d = int(prof.get('time_designation') or 5)
        return len(days) < max_d

    # 4-day prof already has 4 days -> cannot teach on Friday
    assert can_teach(prof_4_days, 'Monday') is True   # already scheduled day
    assert can_teach(prof_4_days, 'Friday') is False  # 5th day rejected

    # 5-day prof has 4 days -> can teach on Friday (5th day)
    assert can_teach(prof_5_days, 'Friday') is True   # 5th day accepted
    scheduled_days[20].add('Friday')
    assert can_teach(prof_5_days, 'Saturday') is False # 6th day rejected

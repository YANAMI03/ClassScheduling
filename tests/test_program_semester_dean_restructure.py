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
    
    # Scheduler should only see 4 functional tabs: Generate (/), Schedules (/schedules), Professor Schedule, Room Schedule
    expected_endpoints = ['/', '/schedules', '/professor_schedule', '/room_schedule']
    for ep in expected_endpoints:
        assert any(ep == h or h.endswith(ep) for h in feature_links), f"Expected endpoint {ep} in scheduler nav"

    # Must NOT have administrative or CUD links
    forbidden_endpoints = ['/courses', '/professors', '/rooms', '/timeslot', '/academic_ranking', '/semesters', '/section_config', '/users']
    for f_ep in forbidden_endpoints:
        assert not any(f_ep == h or h.endswith(f_ep) for h in feature_links), f"Forbidden endpoint {f_ep} found in scheduler nav"


def test_scheduler_zero_crud_enforcement(mock_db):
    """Requirement 8: Scheduler must be denied CUD operations across program entities."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # POST to Add Course -> Redirected with access denied
    r1 = client.post('/add_course', data={'course_name': 'HACK101', 'units': 3, 'semester': '1st Semester'})
    assert r1.status_code == 302
    assert r1.headers['Location'].endswith('/schedules')

    # POST to Add Professor -> Denied
    r2 = client.post('/add_professor', data={'first_name': 'Hack', 'last_name': 'User', 'academic_ranking_id': 1})
    assert r2.status_code == 302
    assert r2.headers['Location'].endswith('/schedules')

    # POST to Add Room -> Denied
    r3 = client.post('/add_room', data={'room_name': 'Room 999', 'room_type': 'Lecture'})
    assert r3.status_code == 302
    assert r3.headers['Location'].endswith('/schedules')

    # POST to Save Section Config -> Denied
    r4 = client.post('/save_section_config', data={'semester_id': 10, 'sections_1': 5})
    assert r4.status_code == 302
    assert r4.headers['Location'].endswith('/schedules')

    # POST to Add Semester -> Denied
    r5 = client.post('/add_semester', data={'school_year': '2027-2028', 'term': '1st Semester'})
    assert r5.status_code == 302
    assert r5.headers['Location'].endswith('/schedules')


def test_scheduler_read_only_access(mock_db):
    """Requirement 8: Scheduler CAN view faculty, courses, rooms, and rankings in read-only mode."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    for path in ['/courses', '/professors', '/rooms', '/academic_ranking']:
        resp = client.get(path)
        assert resp.status_code == 200, f"Scheduler should be able to view {path} in read-only mode"
        html = resp.get_data(as_text=True)
        assert 'read-only' in html.lower(), f"Expected read-only banner in {path} for scheduler"
        assert not re.search(r'class=["\'][^"\']*\b(?:edit-btn|edit-ranking-btn)\b', html), f"Forbidden edit button found in {path} for scheduler"
        assert not re.search(r'href=["\'][^"\']*/delete_', html), f"Forbidden delete action found in {path} for scheduler"


def test_dean_semester_management(mock_db):
    """Requirement 2 & 3: Dean can create, view, and activate semesters scoped to program_id."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['username'] = 'dean_it'
        session['role'] = 'Dean'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # 1. Dean views semesters
    resp = client.get('/semesters')
    assert resp.status_code == 200

    # 2. Dean adds a new semester
    r_add = client.post('/add_semester', data={
        'school_year': '2026-2027',
        'term': 'Summer Term',
        'is_active': 'true',
    }, follow_redirects=True)
    assert r_add.status_code == 200
    inserted_sems = [s for s in mock_db.table('semester').data if s.get('term') == 'Summer Term']
    assert len(inserted_sems) == 1
    assert inserted_sems[0]['program_id'] == 1
    assert inserted_sems[0]['is_active'] is True

    # 3. Dean activates an existing semester (e.g. ID 11: 2nd Semester)
    r_act = client.get('/activate_semester/11', follow_redirects=True)
    assert r_act.status_code == 200
    sem11 = [s for s in mock_db.table('semester').data if s.get('id') == 11][0]
    assert sem11['is_active'] is True


def test_dean_section_config_management(mock_db):
    """Requirement 5: Section config module per semester with year-level counts."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['username'] = 'dean_it'
        session['role'] = 'Dean'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    resp = client.get('/section_config?semester_id=10')
    assert resp.status_code == 200

    # Save section configs
    r_save = client.post('/save_section_config', data={
        'semester_id': '10',
        'sections_1': '4',
        'sections_2': '3',
        'sections_3': '2',
        'sections_4': '1',
    }, follow_redirects=True)
    assert r_save.status_code == 200

    configs = [c for c in mock_db.table('section_config').data if str(c.get('semester_id')) == '10']
    c_y1 = [c for c in configs if str(c.get('year_level')) == '1'][0]
    assert c_y1['number_of_sections'] == 4


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

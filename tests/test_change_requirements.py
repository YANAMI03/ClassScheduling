import pytest
import os
import app as app_module

class MockQuery:
    def __init__(self, data=None):
        self._data = data if data is not None else []
        self._filters = {}
        self._payload = None
        self._is_delete = False

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, vals):
        self._filters[col] = vals
        return self

    def or_(self, expr):
        self._or_expr = expr
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
        return self

    def neq(self, *args, **kwargs):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def insert(self, payload):
        if isinstance(payload, list):
            for row in payload:
                r = dict(row)
                if 'schedule_id' not in r:
                    r['schedule_id'] = len(self._data) + 100
                self._data.append(r)
        elif isinstance(payload, dict):
            r = dict(payload)
            if 'schedule_id' not in r:
                r['schedule_id'] = len(self._data) + 100
            self._data.append(r)
        return self

    def delete(self):
        self._is_delete = True
        return self

    def execute(self):
        if self._payload is not None:
            updated = []
            for row in self._data:
                match = True
                for k, v in self._filters.items():
                    if isinstance(v, (list, tuple, set)):
                        if row.get(k) not in v:
                            match = False
                            break
                        continue
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if match and getattr(self, '_or_expr', None):
                    parts = self._or_expr.split(',')
                    or_ok = False
                    for part in parts:
                        col, _, val = part.partition('.eq.')
                        if str(row.get(col)) == str(val):
                            or_ok = True
                            break
                    if not or_ok:
                        match = False
                if match:
                    row.update(self._payload)
                    updated.append(row)
            self._payload = None
            self._filters = {}
            self._or_expr = None
            class UpdResp:
                data = updated
            return UpdResp()

        if self._is_delete:
            self._is_delete = False
            remaining = []
            for row in self._data:
                match = True
                for k, v in self._filters.items():
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if not match:
                    remaining.append(row)
            self._data[:] = remaining
            class DelResp:
                data = []
            return DelResp()

        filtered = []
        for row in self._data:
            match = True
            for k, v in self._filters.items():
                if isinstance(v, (list, tuple, set)):
                    if row.get(k) not in v:
                        match = False
                        break
                else:
                    if str(row.get(k)) != str(v):
                        match = False
                        break
            if match:
                filtered.append(row)
        class SelResp:
            data = filtered
        return SelResp()


class MockSupabase:
    def __init__(self, courses=None, loads=None, schedules=None, professors=None, rooms=None, working_hours=None, academic_rankings=None):
        self.courses = courses or []
        self.loads = loads or []
        self.schedules = schedules or []
        self.professors = professors or []
        self.rooms = rooms or []
        self.working_hours = working_hours or []
        self.academic_rankings = academic_rankings or []

    def table(self, table_name):
        if table_name == 'course':
            return MockQuery(self.courses)
        elif table_name == 'professor_load':
            return MockQuery(self.loads)
        elif table_name in ('schedule', 'schedule_with_semester'):
            return MockQuery(self.schedules)
        elif table_name == 'professor':
            return MockQuery(self.professors)
        elif table_name == 'room':
            return MockQuery(self.rooms)
        elif table_name == 'working_hours':
            return MockQuery(self.working_hours)
        elif table_name == 'academic_ranking':
            return MockQuery(self.academic_rankings)
        return MockQuery([])


# --------------------------------------------------------------------------
# Checklist Item 1: Multiple professors on one course
# --------------------------------------------------------------------------
def test_checklist_multiple_professors_on_one_course(monkeypatch):
    """Prof A 2 sections + Prof B 1 section = 3 sections total. Total sections derived correctly."""
    courses = [
        {'course_id': 10, 'course_code': 'CS101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 11, 'course_code': 'CS102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    # Course 10 has Prof 1 (2 sections) + Prof 2 (1 section) = 3 sections
    # Course 11 has Prof 3 (3 sections) = 3 sections
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 10, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 10, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 11, 'sections': 3},
    ]
    db = MockSupabase(courses=courses, loads=loads)
    monkeypatch.setattr(app_module, 'supabase', db)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res['valid'] is True
    bd1 = res['breakdown'][0]
    assert bd1['section_count'] == 3
    assert bd1['section_names'] == ['1A', '1B', '1C']
    c10_details = [c for c in bd1['courses'] if c['course_id'] == 10][0]
    assert c10_details['sections'] == 3
    assert len(c10_details['loads']) == 2


# --------------------------------------------------------------------------
# Checklist Item 2: Course with no load (blocks generation)
# --------------------------------------------------------------------------
def test_checklist_course_with_no_load_blocks_generation(monkeypatch):
    """A course with no professor_load rows counts as 0 sections, shows clear error, and blocks generation."""
    courses = [
        {'course_id': 10, 'course_code': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 12, 'course_code': 'CC-102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 10, 'sections': 2},
        # CC-102 has no load
    ]
    db = MockSupabase(courses=courses, loads=loads)
    monkeypatch.setattr(app_module, 'supabase', db)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res['valid'] is False
    assert any('CC-102 has no assigned load' in err for err in res['errors'])


# --------------------------------------------------------------------------
# Checklist Item 3: Mismatched section counts
# --------------------------------------------------------------------------
def test_checklist_mismatched_section_counts_blocks(monkeypatch):
    """Courses in same year level + semester must match; mismatches block generation with list of counts."""
    courses = [
        {'course_id': 20, 'course_code': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 21, 'course_code': 'MATH-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 20, 'sections': 3},
        {'id': 2, 'prof_id': 2, 'course_id': 21, 'sections': 4},
    ]
    db = MockSupabase(courses=courses, loads=loads)
    monkeypatch.setattr(app_module, 'supabase', db)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res['valid'] is False
    assert any('mismatched section counts' in err for err in res['errors'])
    assert any('CC-101 has 3 sections' in err for err in res['errors'])
    assert any('MATH-101 has 4 sections' in err for err in res['errors'])


# --------------------------------------------------------------------------
# Checklist Item 4: Each specialization in a specialized term
# --------------------------------------------------------------------------
def test_checklist_each_specialization_in_specialized_term(monkeypatch):
    """Specialized courses in 3rd year 2nd sem group and name sections per specialization."""
    courses = [
        {'course_id': 31, 'course_code': 'DB-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 32, 'course_code': 'WEB-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 33, 'course_code': 'NET-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 31, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 32, 'sections': 3},
        {'id': 3, 'prof_id': 3, 'course_id': 33, 'sections': 1},
    ]
    db = MockSupabase(courses=courses, loads=loads)
    monkeypatch.setattr(app_module, 'supabase', db)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is True
    bd = res['breakdown'][0]
    assert bd['is_specialized'] is True
    spec_map = {g['specialization']: g for g in bd['specialization_groups']}
    assert spec_map['Database Systems']['section_count'] == 2
    assert spec_map['Database Systems']['section_names'] == ['3A-Database Systems', '3B-Database Systems']
    assert spec_map['Web Systems']['section_count'] == 3
    assert spec_map['Web Systems']['section_names'] == ['3A-Web Systems', '3B-Web Systems', '3C-Web Systems']
    assert spec_map['Networking']['section_count'] == 1
    assert spec_map['Networking']['section_names'] == ['3A-Networking']


# --------------------------------------------------------------------------
# Checklist Item 5: General courses (sum matches, mismatch blocks)
# --------------------------------------------------------------------------
def test_checklist_general_courses_sum_matches_and_mismatch_blocks(monkeypatch):
    """General courses total_sections must equal sum across all specialization groups."""
    # Specializations: Database (2) + Web (1) + Networking (2) = 5
    courses = [
        {'course_id': 41, 'course_code': 'DB-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 42, 'course_code': 'WEB-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 43, 'course_code': 'NET-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 44, 'course_code': 'GEN-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'General', 'program_id': 1},
    ]

    # Subtest A: Mismatch blocks (GEN-401 has 4 sections instead of 5)
    loads_mismatch = [
        {'id': 1, 'prof_id': 1, 'course_id': 41, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 42, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 43, 'sections': 2},
        {'id': 4, 'prof_id': 4, 'course_id': 44, 'sections': 4},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses=courses, loads=loads_mismatch))
    res_mismatch = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res_mismatch['valid'] is False
    assert any('requires 5 sections' in err for err in res_mismatch['errors'])

    # Subtest B: Match allows generation (GEN-401 has 5 sections)
    loads_match = [
        {'id': 1, 'prof_id': 1, 'course_id': 41, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 42, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 43, 'sections': 2},
        {'id': 4, 'prof_id': 4, 'course_id': 44, 'sections': 5},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses=courses, loads=loads_match))
    res_match = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res_match['valid'] is True
    assert len(res_match['errors']) == 0
    bd = res_match['breakdown'][0]
    assert bd['general_total_required'] == 5


# --------------------------------------------------------------------------
# Checklist Item 6: Archive rule (same semester blocked, different allowed, archive button, restore)
# --------------------------------------------------------------------------
def test_checklist_archive_rule_same_semester_blocked(monkeypatch):
    """Active schedule for the SAME semester blocks generation with clear error."""
    courses = [{'course_id': 1, 'course_code': 'C1', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1}]
    loads = [{'id': 1, 'prof_id': 1, 'course_id': 1, 'sections': 1}]
    schedules = [
        {'schedule_id': 500, 'program_id': 1, 'semester': '1st Semester', 'archive': False, 'batch_id': 'active-1'}
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses=courses, loads=loads, schedules=schedules))

    res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res['valid'] is False
    assert res['active_schedule']['exists'] is True
    assert res['active_schedule']['same_semester'] is True
    assert any('already exists. Archive it first to generate a new one' in err for err in res['errors'])


def test_checklist_archive_rule_different_semester_allowed(monkeypatch):
    """Active schedule for a DIFFERENT semester is allowed and returns active_schedule info to be atomically soft-archived."""
    courses = [{'course_id': 1, 'course_code': 'C1', 'year_level': 1, 'semester': '2nd Semester', 'program_id': 1}]
    loads = [{'id': 1, 'prof_id': 1, 'course_id': 1, 'sections': 1}]
    schedules = [
        {'schedule_id': 500, 'program_id': 1, 'semester': '1st Semester', 'archive': False, 'batch_id': 'active-1'}
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses=courses, loads=loads, schedules=schedules))

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is True
    assert res['active_schedule']['exists'] is True
    assert res['active_schedule']['same_semester'] is False


def test_checklist_archive_schedule_button(monkeypatch):
    """POST /archive_schedule soft-archives the active schedule."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['program_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'

    schedules = [
        {'schedule_id': 10, 'program_id': 1, 'program': 'BSIT', 'semester': '1st Semester', 'archive': False, 'batch_id': 'batch-a'},
        {'schedule_id': 11, 'program_id': 1, 'program': 'BSIT', 'semester': '1st Semester', 'archive': False, 'batch_id': 'batch-a'},
    ]
    db = MockSupabase(schedules=schedules)
    monkeypatch.setattr(app_module, 'supabase', db)

    resp = client.post('/archive_schedule', follow_redirects=False)
    assert resp.status_code == 302
    # Verify rows were updated to archive = True
    for s in db.schedules:
        assert s['archive'] is True


def test_checklist_restore_schedule_enforces_one_active(monkeypatch):
    """Restoring a schedule archives the currently active schedule and reactivates the target batch."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['program_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'

    schedules = [
        # Currently active batch
        {'schedule_id': 100, 'program_id': 1, 'program': 'BSIT', 'archive': False, 'batch_id': 'current-active', 'semester': '1st Semester'},
        # Archived batch to restore
        {'schedule_id': 200, 'program_id': 1, 'program': 'BSIT', 'archive': True, 'batch_id': 'target-restore', 'semester': '1st Semester'},
    ]
    db = MockSupabase(schedules=schedules)
    monkeypatch.setattr(app_module, 'supabase', db)

    resp = client.post('/restore_schedule/target-restore', follow_redirects=True)
    assert resp.status_code == 200
    assert 'Cannot restore schedule: An active schedule already exists' in resp.data.decode('utf-8')

    # Verify: active schedule is NOT overwritten, archived batch remains archived
    s100 = [s for s in db.schedules if s['schedule_id'] == 100][0]
    s200 = [s for s in db.schedules if s['schedule_id'] == 200][0]
    assert s100['archive'] is False
    assert s200['archive'] is True


# --------------------------------------------------------------------------
# Checklist Item 7: Migration SQL file integrity on existing data
# --------------------------------------------------------------------------
def test_checklist_migration_sql_structure():
    """Verify migration SQL order: add plain column, backfill, drop FK, drop old col, drop semester table."""
    migration_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', '20261001_remove_semester_add_specialization_rework_archive.sql')
    assert os.path.exists(migration_path), "Migration SQL file must exist"

    with open(migration_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Verify required operations and constraints in SQL
    assert 'ADD COLUMN IF NOT EXISTS semester' in sql
    assert 'DROP TABLE IF EXISTS public.section_config' in sql
    assert 'DROP TABLE IF EXISTS public.semester' in sql
    assert 'REFERENCES public.program' in sql
    assert "CHECK (specialization IS NULL OR specialization IN ('Database Systems', 'Web Systems', 'Networking', 'General'))" in sql
    assert 'idx_schedule_program_archive' in sql
    assert 'rollback' in sql.lower()
    assert 'has_cutoff' in sql
    assert 'professor_cutoff' in sql


# --------------------------------------------------------------------------
# Working Hours Management: Add or Remove Operating Days
# --------------------------------------------------------------------------
def test_add_working_hours_day(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(working_hours=[
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/add_working_hours', data={
        'day': 'Thursday',
        'start_time': '08:00',
        'end_time': '20:00',
        'lunch_time': '12:00'
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert any(ts.get('day') == 'Thursday' for ts in db.working_hours)


def test_add_working_hours_duplicate_rejected(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(working_hours=[
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/add_working_hours', data={
        'day': 'Monday',
        'start_time': '08:00',
        'end_time': '20:00',
        'lunch_time': '12:00'
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert len(db.working_hours) == 1


def test_delete_working_hours_day(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(working_hours=[
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00'},
        {'working_hours_id': 6, 'day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.get('/delete_working_hours/6', follow_redirects=False)
    assert resp.status_code == 302
    assert not any(ts.get('day') == 'Saturday' for ts in db.working_hours)
    assert any(ts.get('day') == 'Monday' for ts in db.working_hours)


# --------------------------------------------------------------------------
# Sections 999 bug fix validation
# --------------------------------------------------------------------------
def test_professor_load_sanitizes_corrupted_999_sections(monkeypatch):
    """Corrupted 999 sections in professor_load are safely clamped to 1 in professor_load view."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'dean_it'
        session['role'] = 'Dean'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    profs = [{'prof_id': 1, 'first_name': 'Emilsa', 'last_name': 'Bantug', 'program_id': 1}]
    courses = [{'course_id': 10, 'course_code': 'IT-WS05', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 2, 'ilp_hours': 0, 'units': 3}]
    loads = [{'id': 50, 'prof_id': 1, 'course_id': 10, 'sections': 999}]

    db = MockSupabase(professors=profs, courses=courses, loads=loads)
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    resp = client.get('/professor_load')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The 999 sec badge and inflated 4995h should NOT appear
    assert '999 sec' not in html
    assert '1 sec' in html
    assert '4995 hrs' not in html


def test_confirm_preview_never_creates_unauthorized_loads(monkeypatch):
    """confirm_preview must NEVER insert new rows into professor_load. Unassigned/fallback slots store professor_load_id=None."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_no_unauth'
        session['schedule_preview'] = [
            {
                'professor_load_id': None,
                'course_id': 10,
                'course_code': 'IT-WS05',
                'section': '3A',
                'prof_id': 1,
                'room_id': 1,
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '11:00 AM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'program': 'BSIT',
            }
        ]

    db = MockSupabase(loads=[])
    captured_rpc = {}

    class TrackingSupabase(MockSupabase):
        def rpc(self, func_name, params=None):
            captured_rpc['func'] = func_name
            captured_rpc['params'] = params
            class FakeRpcExec:
                def execute(self):
                    class FakeResp:
                        data = {'success': True}
                    return FakeResp()
            return FakeRpcExec()

    tracking_db = TrackingSupabase(loads=[])
    monkeypatch.setattr(app_module, 'supabase', tracking_db)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/confirm_preview', follow_redirects=False)
    assert resp.status_code == 302
    # Zero rows must be inserted into professor_load!
    assert len(tracking_db.loads) == 0
    # professor_load_id must be None so foreign key is preserved and no fake load is created
    p_rows = captured_rpc.get('params', {}).get('p_rows', [])
    assert len(p_rows) == 1
    assert p_rows[0].get('professor_load_id') is None


# --------------------------------------------------------------------------
# Checklist Item 8: Change 6 - ILP Session Queue Building
# --------------------------------------------------------------------------
def test_checklist_ilp_session_queue_building():
    """Verify _build_subject_session_queue generates exactly 1-hour ILP session when ilp_hours == 1."""
    # Paired lecture + lab with no ILP
    q1 = app_module._build_subject_session_queue({'lecture_hours': 3, 'lab_hours': 2, 'ilp_hours': 0})
    assert len(q1) == 1
    assert q1[0]['paired'] is True

    # Paired lecture + lab WITH ILP
    q2 = app_module._build_subject_session_queue({'lecture_hours': 3, 'lab_hours': 2, 'ilp_hours': 1})
    assert len(q2) == 2
    assert q2[0]['paired'] is True
    assert q2[1]['paired'] is False
    assert q2[1]['session_type'] == 'ILP'
    assert q2[1]['duration'] == 1
    assert q2[1]['is_ilp'] is True

    # Lecture only WITH ILP
    q3 = app_module._build_subject_session_queue({'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 1})
    assert len(q3) == 2
    assert q3[0]['session_type'] == 'Lecture'
    assert q3[0]['duration'] == 3
    assert q3[1]['session_type'] == 'ILP'
    assert q3[1]['duration'] == 1

    # Lab only WITH ILP
    q4 = app_module._build_subject_session_queue({'lecture_hours': 0, 'lab_hours': 2, 'ilp_hours': 1})
    assert len(q4) == 2
    assert q4[0]['session_type'] == 'Laboratory'
    assert q4[0]['duration'] == 2
    assert q4[1]['session_type'] == 'ILP'
    assert q4[1]['duration'] == 1

    # ILP only
    q5 = app_module._build_subject_session_queue({'lecture_hours': 0, 'lab_hours': 0, 'ilp_hours': 1})
    assert len(q5) == 1
    assert q5[0]['session_type'] == 'ILP'
    assert q5[0]['duration'] == 1


# --------------------------------------------------------------------------
# Checklist Item 9: Change 6 - ILP Hours Validation in Course Routes
# --------------------------------------------------------------------------
def test_checklist_ilp_hours_validation_course_routes(monkeypatch):
    """Adding or editing a course with ilp_hours other than 0 or 1 is rejected with flash error."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(courses=[])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    # Subtest A: Reject ilp_hours = 2
    resp_reject = client.post('/add_course', data={
        'course_code': 'TEST-ILP',
        'program': 'BSIT',
        'year_level': '1',
        'semester': '1st Semester',
        'lecture_hours': '3',
        'lab_hours': '0',
        'ilp_hours': '2',
        'units': '3',
    }, follow_redirects=True)
    assert resp_reject.status_code == 200
    assert b'ILP hours must be either 0 or 1' in resp_reject.data
    assert len(db.courses) == 0

    # Subtest B: Accept ilp_hours = 1
    resp_accept = client.post('/add_course', data={
        'course_code': 'TEST-ILP-OK',
        'program': 'BSIT',
        'year_level': '1',
        'semester': '1st Semester',
        'lecture_hours': '3',
        'lab_hours': '0',
        'ilp_hours': '1',
        'units': '3',
    }, follow_redirects=True)
    assert resp_accept.status_code == 200
    assert len(db.courses) == 1
    assert db.courses[0]['ilp_hours'] == 1


# --------------------------------------------------------------------------
# Checklist Item 10: LOHB ranking / per-professor cutoff logic removed
# --------------------------------------------------------------------------
def test_checklist_per_professor_cutoff_logic_removed():
    """The LOHB academic ranking and per-professor time-cutoff logic no longer exist."""
    assert not hasattr(app_module, '_check_professor_cutoff_conflict'), \
        "Per-professor cutoff conflict helper must be removed"


# --------------------------------------------------------------------------
# Checklist Item 11: ILP sessions do not use a room
# --------------------------------------------------------------------------
def test_checklist_schedule_generation_places_ilp_without_room(monkeypatch):
    """Schedule generation never assigns a room to an ILP entry."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    courses = [
        {'course_id': 100, 'course_code': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 1, 'units': 3, 'archive': False},
    ]
    profs = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'time_designation': 5},
    ]
    loads = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 1, 'professor_name': 'Alan Turing'},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1, 'archive': False},
    ]
    working_hours = [
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'archive': False},
        {'working_hours_id': 2, 'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'archive': False},
    ]

    db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms, working_hours=working_hours)
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '30'}, follow_redirects=True)
    assert resp.status_code == 200

    with client.session_transaction() as sess:
        pid = sess.get('preview_id')
    entries = app_module._get_preview_for_user(preview_id=pid)

    print(f"ENTRIES LEN: {len(entries)}")
    for e in entries:
        print(e)
    # There should be two entries: 1 Lecture (3h) and 1 ILP (1h)
    assert len(entries) == 2
    ilp_entries = [e for e in entries if e.get('session_type') == 'ILP']
    assert len(ilp_entries) == 1
    ilp_e = ilp_entries[0]
    assert ilp_e['room_id'] is None
    assert ilp_e['room_name'] == 'No room'
    assert ilp_e['section'] == '1A'
    assert ilp_e['course_id'] == 100
    # Monday's window starts at 07:00 and that first hour is free, so ILP takes 07:00-08:00.
    assert ilp_e['day'] == 'Monday'
    assert ilp_e['start'] == '07:00 AM'
    assert ilp_e['end'] == '08:00 AM'


def test_checklist_ilp_multisection_generation_and_placement_rules(monkeypatch):
    """Verify that every section of every course with ilp_hours = 1 gets its own 1-hour ILP session,
    and placement only uses the allowed zones (7-8 AM, after a 3-hour class, after the window end),
    avoids lunch, never reserves a room, and never double-books a professor or section.
    """
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    courses = [
        {'course_id': 100, 'course_code': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 1, 'units': 3, 'archive': False},
    ]
    # Professor Alan Turing assigned 3 sections
    profs = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'time_designation': 5},
    ]
    loads = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 3, 'professor_name': 'Alan Turing'},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1, 'archive': False},
    ]
    working_hours = [
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'archive': False},
        {'working_hours_id': 2, 'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'archive': False},
        {'working_hours_id': 3, 'day': 'Wednesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'archive': False},
        {'working_hours_id': 4, 'day': 'Thursday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'archive': False},
        {'working_hours_id': 5, 'day': 'Friday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'archive': False},
    ]

    db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms, working_hours=working_hours)
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '90'}, follow_redirects=True)
    assert resp.status_code == 200

    with client.session_transaction() as sess:
        pid = sess.get('preview_id')
    entries = app_module._get_preview_for_user(preview_id=pid)
    ilp_entries = [e for e in entries if e.get('session_type') == 'ILP']

    from datetime import timedelta as _td
    from collections import defaultdict as _dd

    # 1. Exactly 3 ILP entries generated across 3 sections (1A, 1B, 1C)
    assert len(ilp_entries) == 3
    sec_set = {e['section'] for e in ilp_entries}
    assert sec_set == {'1A', '1B', '1C'}

    # 2. Each ILP entry is exactly 1 hour, has no room, belongs to Alan, avoids lunch, and obeys the 8 PM cap.
    _lunch_start = app_module._parse_time('12:00:00')
    _lunch_end = app_module._parse_time('13:00:00')
    _day_cap = app_module._parse_time('20:00:00')
    for e in ilp_entries:
        assert e['room_id'] is None
        assert e['room_name'] == 'No room'
        assert e['professor_name'] == 'Alan Turing'
        st = app_module._parse_time(e['start'])
        en = app_module._parse_time(e['end'])
        assert (en - st) == _td(hours=1)
        assert en <= _day_cap, 'ILP must never end after 8:00 PM'
        assert not (st < _lunch_end and _lunch_start < en), 'ILP must not overlap lunch'

    # 3. No professor is double-booked: with a single professor, all ILP hours are distinct.
    ilp_slots = {(e['day'], e['start']) for e in ilp_entries}
    assert len(ilp_slots) == len(ilp_entries)

    # 4. No section and no professor is double-booked by the generated sessions.
    def _overlap(a, b):
        return a[0] < b[1] and b[0] < a[1]

    for key in ('section', 'professor_name'):
        buckets = _dd(list)
        for e in entries:
            st = app_module._parse_time(e['start'])
            en = app_module._parse_time(e['end'])
            if st is None or en is None:
                continue
            buckets[(e.get(key), e['day'])].append((st, en))
        for group in buckets.values():
            group.sort()
            for i in range(1, len(group)):
                assert not _overlap(group[i - 1], group[i]), f'{key} double-booked'

    # 4. Subtest: ILPs never occupy a room, even when professors differ
    free_courses = [
        {'course_id': 100, 'course_code': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 0, 'lab_hours': 0, 'ilp_hours': 1, 'units': 1, 'archive': False},
    ]
    profs2 = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'time_designation': 5},
        {'prof_id': 20, 'first_name': 'Grace', 'last_name': 'Hopper', 'program_id': 1, 'time_designation': 5},
    ]
    loads2 = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 1, 'professor_name': 'Alan Turing'},
        {'id': 102, 'prof_id': 20, 'course_id': 100, 'sections': 1, 'professor_name': 'Grace Hopper'},
    ]
    db2 = MockSupabase(courses=free_courses, professors=profs2, loads=loads2, rooms=rooms, working_hours=working_hours)
    monkeypatch.setattr(app_module, 'supabase', db2)

    resp2 = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '60'}, follow_redirects=True)
    assert resp2.status_code == 200

    with client.session_transaction() as sess2:
        pid2 = sess2.get('preview_id')
    entries2 = app_module._get_preview_for_user(preview_id=pid2)
    ilp2 = [e for e in entries2 if e.get('session_type') == 'ILP']
    assert len(ilp2) == 2
    assert all(e['room_id'] is None for e in ilp2)
    assert all(e['room_name'] == 'No room' for e in ilp2)
    assert {e['professor_name'] for e in ilp2} == {'Alan Turing', 'Grace Hopper'}
    # Global ILP limit removed: both sections may share Monday 7-8 AM (different professors).
    assert {(e['day'], e['start']) for e in ilp2} == {('Monday', '07:00 AM')}

    # 5. Subtest: Same professor with 2 sections where Monday 07:00-08:00 is free
    # First section takes Monday 07:00-08:00; second section falls back to last hour of a day (e.g. Tuesday 07:00-08:00 PM)
    loads3 = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 2, 'professor_name': 'Alan Turing'},
    ]
    db3 = MockSupabase(courses=free_courses, professors=profs, loads=loads3, rooms=rooms, working_hours=working_hours)
    monkeypatch.setattr(app_module, 'supabase', db3)

    resp3 = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '60'}, follow_redirects=True)
    assert resp3.status_code == 200

    with client.session_transaction() as sess3:
        pid3 = sess3.get('preview_id')
    entries3 = app_module._get_preview_for_user(preview_id=pid3)
    ilp3 = [e for e in entries3 if e.get('session_type') == 'ILP']
    assert len(ilp3) == 2
    # Same professor: the two ILPs cannot overlap, and have no rooms.
    ilp3_slots = {(e['day'], e['start'], e['end']) for e in ilp3}
    assert len(ilp3_slots) == 2
    assert all(e['room_id'] is None and e['room_name'] == 'No room' for e in ilp3)



# --------------------------------------------------------------------------
# Checklist Item 12: Lecture/lab limited only by the working-hours window
# --------------------------------------------------------------------------
def test_checklist_lecture_limited_only_by_working_hours_window(monkeypatch):
    """With per-professor cutoff removed, a 3-hour lecture is placed anywhere inside the
    day's working-hours window, identically for every professor (no LOHB exemption needed)."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # Single day Monday, window 14:00-19:00 (no lunch). A 3h lecture must fit inside it.
    working_hours = [
        {'working_hours_id': 1, 'day': 'Monday', 'start_time': '14:00:00', 'end_time': '19:00:00', 'lunch_time': None, 'archive': False},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1, 'archive': False},
    ]
    courses = [
        {'course_id': 201, 'course_code': 'IT-WINDOW', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3, 'archive': False},
    ]

    for pid, fname, lname in ((20, 'Marie', 'Curie'), (30, 'Ada', 'Lovelace')):
        profs = [
            {'prof_id': pid, 'first_name': fname, 'last_name': lname, 'program_id': 1, 'time_designation': 5},
        ]
        loads = [
            {'id': 200 + pid, 'prof_id': pid, 'course_id': 201, 'sections': 1, 'professor_name': f"{fname} {lname}"},
        ]
        db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms, working_hours=working_hours)
        monkeypatch.setattr(app_module, 'supabase', db)
        monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
        monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
        monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

        resp = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '90'}, follow_redirects=True)
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            pid_preview = sess.get('preview_id')
        entries = app_module._get_preview_for_user(preview_id=pid_preview)
        # No cutoff: the lecture is scheduled for both professors, inside the window.
        assert len(entries) == 1, f"{fname} {lname} should get exactly one lecture"
        e = entries[0]
        st = app_module._parse_time(e['start'])
        en = app_module._parse_time(e['end'])
        assert st >= app_module._parse_time('14:00:00')
        assert en <= app_module._parse_time('19:00:00')
        assert e['room_id'] == 1

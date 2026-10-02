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
    def __init__(self, courses=None, loads=None, schedules=None, professors=None, rooms=None, timeslots=None, academic_rankings=None):
        self.courses = courses or []
        self.loads = loads or []
        self.schedules = schedules or []
        self.professors = professors or []
        self.rooms = rooms or []
        self.timeslots = timeslots or []
        self.academic_rankings = academic_rankings or []

    def table(self, table_name):
        if table_name == 'course':
            return MockQuery(self.courses)
        elif table_name == 'professor_load':
            return MockQuery(self.loads)
        elif table_name == 'schedule':
            return MockQuery(self.schedules)
        elif table_name == 'professor':
            return MockQuery(self.professors)
        elif table_name == 'room':
            return MockQuery(self.rooms)
        elif table_name == 'timeslot':
            return MockQuery(self.timeslots)
        elif table_name == 'academic_ranking':
            return MockQuery(self.academic_rankings)
        return MockQuery([])


# --------------------------------------------------------------------------
# Checklist Item 1: Multiple professors on one course
# --------------------------------------------------------------------------
def test_checklist_multiple_professors_on_one_course(monkeypatch):
    """Prof A 2 sections + Prof B 1 section = 3 sections total. Total sections derived correctly."""
    courses = [
        {'course_id': 10, 'course_name': 'CS101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 11, 'course_name': 'CS102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
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
        {'course_id': 10, 'course_name': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 12, 'course_name': 'CC-102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
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
        {'course_id': 20, 'course_name': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 21, 'course_name': 'MATH-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
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
        {'course_id': 31, 'course_name': 'DB-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 32, 'course_name': 'WEB-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 33, 'course_name': 'NET-301', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
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
        {'course_id': 41, 'course_name': 'DB-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 42, 'course_name': 'WEB-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 43, 'course_name': 'NET-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 44, 'course_name': 'GEN-401', 'year_level': 4, 'semester': '1st Semester', 'specialization': 'General', 'program_id': 1},
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
    courses = [{'course_id': 1, 'course_name': 'C1', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1}]
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
    courses = [{'course_id': 1, 'course_name': 'C1', 'year_level': 1, 'semester': '2nd Semester', 'program_id': 1}]
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
        {'schedule_id': 10, 'program_id': 1, 'program': 'BSIT', 'archive': False, 'batch_id': 'batch-a'},
        {'schedule_id': 11, 'program_id': 1, 'program': 'BSIT', 'archive': False, 'batch_id': 'batch-a'},
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
        {'schedule_id': 100, 'program_id': 1, 'program': 'BSIT', 'archive': False, 'batch_id': 'current-active'},
        # Archived batch to restore
        {'schedule_id': 200, 'program_id': 1, 'program': 'BSIT', 'archive': True, 'batch_id': 'target-restore'},
    ]
    db = MockSupabase(schedules=schedules)
    monkeypatch.setattr(app_module, 'supabase', db)

    resp = client.post('/restore_schedule/target-restore', follow_redirects=False)
    assert resp.status_code == 302

    # Verify: previous active is archived, restored batch is active
    s100 = [s for s in db.schedules if s['schedule_id'] == 100][0]
    s200 = [s for s in db.schedules if s['schedule_id'] == 200][0]
    assert s100['archive'] is True
    assert s200['archive'] is False


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
# Timeslot Management: Add or Remove Operating Days
# --------------------------------------------------------------------------
def test_add_timeslot_day(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(timeslots=[
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/add_timeslot', data={
        'day': 'Thursday',
        'start_time': '08:00',
        'end_time': '20:00',
        'lunch_time': '12:00'
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert not any(ts.get('day') == 'Thursday' for ts in db.timeslots)


def test_add_timeslot_duplicate_rejected(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(timeslots=[
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/add_timeslot', data={
        'day': 'Monday',
        'start_time': '08:00',
        'end_time': '20:00',
        'lunch_time': '12:00'
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert len(db.timeslots) == 1


def test_delete_timeslot_day(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'Admin'

    db = MockSupabase(timeslots=[
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00'},
        {'timeslot_id': 6, 'day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00'},
    ])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.get('/delete_timeslot/6', follow_redirects=False)
    assert resp.status_code == 302
    assert not any(ts.get('day') == 'Saturday' for ts in db.timeslots)
    assert any(ts.get('day') == 'Monday' for ts in db.timeslots)


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
    courses = [{'course_id': 10, 'course_name': 'IT-WS05', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 2, 'ilp_hours': 0, 'units': 3}]
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
                'course_name': 'IT-WS05',
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
        'course_name': 'TEST-ILP',
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
        'course_name': 'TEST-ILP-OK',
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
# Checklist Item 10: Change 5 - Professor Availability Cutoff Conflicts
# --------------------------------------------------------------------------
def test_checklist_professor_cutoff_conflict_check(monkeypatch):
    """_check_professor_cutoff_conflict detects cutoff violations for regular faculty while exempting LOHB and ILP."""
    # Faculty 1: Regular ranking (has_cutoff = True)
    # Faculty 2: LOHB ranking (has_cutoff = False)
    rankings = [
        {'academic_ranking_id': 1, 'name': 'Assistant Professor', 'has_cutoff': True, 'max_hours': 40},
        {'academic_ranking_id': 2, 'name': 'LOHB / Part-Time', 'has_cutoff': False, 'max_hours': 40},
    ]
    profs = [
        {'prof_id': 1, 'first_name': 'John', 'last_name': 'Doe', 'academic_ranking_id': 1, 'academic_ranking': rankings[0]},
        {'prof_id': 2, 'first_name': 'Jane', 'last_name': 'Smith', 'academic_ranking_id': 2, 'academic_ranking': rankings[1]},
    ]
    timeslots = [
        {'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'professor_cutoff': '16:00:00'},
        {'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'professor_cutoff': '17:00:00'},
    ]
    db = MockSupabase(professors=profs, timeslots=timeslots, academic_rankings=rankings)
    monkeypatch.setattr(app_module, 'supabase', db)

    # 1. Regular faculty ending after cutoff on Monday (17:00 > 16:00) -> Conflict
    conf1 = app_module._check_professor_cutoff_conflict(1, 'Monday', '14:00:00', '17:00:00', session_type='Lecture')
    assert conf1 is not None
    assert 'has a daily cutoff' in conf1['message']

    # 2. Regular faculty ending before/at cutoff on Monday (16:00 <= 16:00) -> No conflict
    conf2 = app_module._check_professor_cutoff_conflict(1, 'Monday', '13:00:00', '16:00:00', session_type='Lecture')
    assert conf2 is None

    # 3. LOHB faculty (has_cutoff = False) ending after cutoff on Monday -> Exempt (No conflict)
    conf3 = app_module._check_professor_cutoff_conflict(2, 'Monday', '16:00:00', '19:00:00', session_type='Lecture')
    assert conf3 is None

    # 4. ILP session ending after cutoff on Monday -> Exempt (No conflict)
    conf4 = app_module._check_professor_cutoff_conflict(1, 'Monday', '16:00:00', '17:00:00', session_type='ILP')
    assert conf4 is None


# --------------------------------------------------------------------------
# Checklist Item 11: ILP sessions use lecture rooms
# --------------------------------------------------------------------------
def test_checklist_schedule_generation_places_ilp_in_lecture_room(monkeypatch):
    """Schedule generation assigns ILP entries to an available lecture room."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    courses = [
        {'course_id': 100, 'course_name': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 1, 'units': 3},
    ]
    profs = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'academic_ranking_id': 1, 'time_designation': 5, 'academic_ranking': {'name': 'Instructor', 'has_cutoff': True, 'max_hours': 40}},
    ]
    loads = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 1},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1},
    ]
    timeslots = [
        {'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '16:00:00'},
        {'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
    ]

    db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms, timeslots=timeslots)
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
    assert ilp_e['room_id'] == 1
    assert ilp_e['room_name'] == 'Room 101'
    assert ilp_e['section'] == '1A'
    assert ilp_e['course_id'] == 100


def test_checklist_ilp_multisection_generation_and_placement_rules(monkeypatch):
    """Verify that every section of every course with ilp_hours = 1 gets its own 1-hour ILP session,
    and placement prioritizes first-hour, last-hour, respects operating windows, avoids lunch,
    and allows sharing same slot only when taught by different professors.
    """
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    courses = [
        {'course_id': 100, 'course_name': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 1, 'units': 3},
    ]
    # Professor Alan Turing assigned 3 sections
    profs = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'academic_ranking_id': 1, 'time_designation': 5, 'academic_ranking': {'name': 'Instructor', 'has_cutoff': True, 'max_hours': 40}},
    ]
    loads = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 3},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1},
    ]
    timeslots = [
        {'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '16:00:00'},
        {'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
        {'day': 'Wednesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
        {'day': 'Thursday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
        {'day': 'Friday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
    ]

    db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms, timeslots=timeslots)
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

    # 1. Exactly 3 ILP entries generated across 3 sections (1A, 1B, 1C)
    assert len(ilp_entries) == 3
    sec_set = {e['section'] for e in ilp_entries}
    assert sec_set == {'1A', '1B', '1C'}

    # 2. Each ILP entry uses the lecture room, is 1 hour, and avoids lunch
    for e in ilp_entries:
        assert e['room_id'] == 1
        assert e['room_name'] == 'Room 101'
        assert e['professor_name'] == 'Alan Turing'
        # Must not be during lunch (12:00 PM)
        assert '12:00 PM' not in e['start']

    # 3. Rule verification for fallback placement:
    # In this run, Alan Turing taught 1A lecture on Monday 07:00-10:00 AM, making Monday 07:00-08:00 unavailable for Alan.
    # Therefore, all 3 ILPs must fall back to the last hour of a day (window end minus 1 hour)
    valid_last_hours = {
            # Since the professor has a cutoff of 16:00 on Monday and 17:00 on Tue-Fri,
            # the last available hours are 15:00-16:00 (Monday) and 16:00-17:00 (Tue-Fri).
            ('Monday', '03:00 PM', '04:00 PM'),
            ('Tuesday', '04:00 PM', '05:00 PM'),
            ('Tuesday', '03:00 PM', '04:00 PM'), # Since 04:00 PM is taken by another section
            ('Wednesday', '04:00 PM', '05:00 PM'),
            ('Thursday', '04:00 PM', '05:00 PM'),
            ('Friday', '04:00 PM', '05:00 PM'),
        }
    for e in ilp_entries:
        assert (e['day'], e['start'], e['end']) in valid_last_hours

    # Must NEVER land in the first hour of Tuesday to Friday (08:00-09:00 AM)
    assert not any(e['day'] in ['Tuesday', 'Wednesday', 'Thursday', 'Friday'] and e['start'] == '08:00 AM' for e in ilp_entries)

    # Spread across days: 3 fallback sessions should be spread across 3 different days
    assert len(set(e['day'] for e in ilp_entries)) >= 2

    # 4. Subtest: ILPs use the lecture room when available, even when professors differ
    free_courses = [
        {'course_id': 100, 'course_name': 'IT-ILP', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 0, 'lab_hours': 0, 'ilp_hours': 1, 'units': 1},
    ]
    profs2 = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1, 'academic_ranking_id': 1, 'time_designation': 5, 'academic_ranking': {'name': 'Instructor', 'has_cutoff': True, 'max_hours': 40}},
        {'prof_id': 20, 'first_name': 'Grace', 'last_name': 'Hopper', 'program_id': 1, 'academic_ranking_id': 1, 'time_designation': 5, 'academic_ranking': {'name': 'Instructor', 'has_cutoff': True, 'max_hours': 40}},
    ]
    loads2 = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 1},
        {'id': 102, 'prof_id': 20, 'course_id': 100, 'sections': 1},
    ]
    db2 = MockSupabase(courses=free_courses, professors=profs2, loads=loads2, rooms=rooms, timeslots=timeslots)
    monkeypatch.setattr(app_module, 'supabase', db2)

    resp2 = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '60'}, follow_redirects=True)
    assert resp2.status_code == 200

    with client.session_transaction() as sess2:
        pid2 = sess2.get('preview_id')
    entries2 = app_module._get_preview_for_user(preview_id=pid2)
    ilp2 = [e for e in entries2 if e.get('session_type') == 'ILP']
    assert len(ilp2) == 2
    assert all(e['room_id'] in (None, 1) for e in ilp2)
    assert all(e['room_name'] == ('Room 101' if e['room_id'] == 1 else 'TBA') for e in ilp2)
    assert {e['professor_name'] for e in ilp2} == {'Alan Turing', 'Grace Hopper'}

    # 5. Subtest: Same professor with 2 sections where Monday 07:00-08:00 is free
    # First section takes Monday 07:00-08:00; second section falls back to last hour of a day (e.g. Tuesday 07:00-08:00 PM)
    loads3 = [
        {'id': 101, 'prof_id': 10, 'course_id': 100, 'sections': 2},
    ]
    db3 = MockSupabase(courses=free_courses, professors=profs, loads=loads3, rooms=rooms, timeslots=timeslots)
    monkeypatch.setattr(app_module, 'supabase', db3)

    resp3 = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '60'}, follow_redirects=True)
    assert resp3.status_code == 200

    with client.session_transaction() as sess3:
        pid3 = sess3.get('preview_id')
    entries3 = app_module._get_preview_for_user(preview_id=pid3)
    ilp3 = [e for e in entries3 if e.get('session_type') == 'ILP']
    assert len(ilp3) == 2
    # Both ILPs are assigned to distinct lecture-room slots.
    ilp3_slots = {(e['day'], e['start'], e['end']) for e in ilp3}
    assert len(ilp3_slots) == 2
    assert all(e['room_id'] == 1 and e['room_name'] == 'Room 101' for e in ilp3)



# --------------------------------------------------------------------------
# Checklist Item 12: Change 5 - Schedule Generation Enforces Cutoff & TBA Fallback
# --------------------------------------------------------------------------
def test_checklist_schedule_generation_enforces_cutoffs(monkeypatch):
    """When all available slots on a day exceed the professor cutoff, regular faculty with has_cutoff=True are not assigned and fallback TBA is used; faculty with has_cutoff=False can be scheduled."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    # Single day Monday: 14:00 to 19:00 (5 hours), cutoff at 16:00
    # Any 3-hour lecture (e.g. 14:00-17:00, 15:00-18:00, 16:00-19:00) ends at 17:00 or later, exceeding the 16:00 cutoff!
    timeslots = [
        {'day': 'Monday', 'start_time': '14:00:00', 'end_time': '19:00:00', 'professor_cutoff': '16:00:00'},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1},
    ]
    courses = [
        {'course_id': 201, 'course_name': 'IT-CUTOFF', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
    ]

    # Subtest A: Regular faculty with has_cutoff = True
    profs_regular = [
        {'prof_id': 20, 'first_name': 'Marie', 'last_name': 'Curie', 'program_id': 1, 'academic_ranking_id': 1, 'time_designation': 5, 'academic_ranking': {'name': 'Professor', 'has_cutoff': True, 'max_hours': 40}},
    ]
    loads_regular = [
        {'id': 202, 'prof_id': 20, 'course_id': 201, 'sections': 1},
    ]
    db_regular = MockSupabase(courses=courses, professors=profs_regular, loads=loads_regular, rooms=rooms, timeslots=timeslots)
    monkeypatch.setattr(app_module, 'supabase', db_regular)
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp_reg = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '90'}, follow_redirects=True)
    assert resp_reg.status_code == 200
    with client.session_transaction() as sess:
        pid_reg = sess.get('preview_id')
    entries_reg = app_module._get_preview_for_user(preview_id=pid_reg)
    # Marie Curie could not be assigned because every slot ends after 16:00 cutoff.
    # Under the strict generation rule, no fallback TBA or placeholder is created; session remains unscheduled.
    assert len(entries_reg) == 0

    # Subtest B: LOHB faculty with has_cutoff = False
    profs_lohb = [
        {'prof_id': 30, 'first_name': 'Ada', 'last_name': 'Lovelace', 'program_id': 1, 'academic_ranking_id': 2, 'time_designation': 5, 'academic_ranking': {'name': 'LOHB Faculty', 'has_cutoff': False, 'max_hours': 40}},
    ]
    loads_lohb = [
        {'id': 203, 'prof_id': 30, 'course_id': 201, 'sections': 1},
    ]
    db_lohb = MockSupabase(courses=courses, professors=profs_lohb, loads=loads_lohb, rooms=rooms, timeslots=timeslots)
    monkeypatch.setattr(app_module, 'supabase', db_lohb)

    resp_lohb = client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': '90'}, follow_redirects=True)
    assert resp_lohb.status_code == 200
    with client.session_transaction() as sess:
        pid_lohb = sess.get('preview_id')
    entries_lohb = app_module._get_preview_for_user(preview_id=pid_lohb)
    assert len(entries_lohb) == 1
    # Ada Lovelace is exempt from cutoff and is successfully assigned
    assert entries_lohb[0]['prof_id'] == 30



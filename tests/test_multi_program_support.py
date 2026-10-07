"""
Comprehensive test suite for Multi-Program Support:
- Programs CRUD and reference-protection deletion
- Courses scoped to programs with per-program code uniqueness
- Shared pools: rooms, timeslots, and cross-program professors
- Cross-program load validation and 'Also teaches in' badge computation
- Multi-program schedule isolation and cross-program conflict prevention
- Concurrency protection on confirm & restore
- Masked room and professor schedules for schedulers
"""

import pytest
import app as app_module
import professor_load_importer
from postgrest.exceptions import APIError


class MockQueryBuilder:
    def __init__(self, table):
        self.table = table
        self._filters = {}
        self._in_filters = {}
        self._is_null = {}
        self._ilike_filters = {}
        self._like_filters = {}
        self._is_insert = False
        self._is_update = False
        self._is_delete = False
        self._update_payload = {}
        self._last_inserted = []

    def select(self, *args, **kwargs):
        return self

    def insert(self, rows):
        self._is_insert = True
        self._last_inserted = []
        if isinstance(rows, list):
            for r in rows:
                new_row = dict(r)
                if 'id' not in new_row and 'program_id' not in new_row and 'course_id' not in new_row and 'schedule_id' not in new_row:
                    new_row['id'] = len(self.table.data) + 1
                self.table.data.append(new_row)
                self._last_inserted.append(new_row)
        else:
            new_row = dict(rows)
            if 'id' not in new_row and 'program_id' not in new_row and 'course_id' not in new_row and 'schedule_id' not in new_row:
                new_row['id'] = len(self.table.data) + 1
            self.table.data.append(new_row)
            self._last_inserted.append(new_row)
        return self

    def update(self, payload):
        self._is_update = True
        self._update_payload = payload
        return self

    def delete(self):
        self._is_delete = True
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def is_(self, col, val):
        self._is_null[col] = val
        return self

    def ilike(self, col, val):
        self._ilike_filters[col] = str(val)
        return self

    def like(self, col, val):
        self._like_filters[col] = str(val)
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        class Resp:
            data = []

        if self._is_insert:
            r = Resp()
            r.data = list(self._last_inserted)
            return r

        if self._is_update:
            for item in self.table.data:
                match = True
                for col, val in self._filters.items():
                    if str(item.get(col)) != str(val):
                        match = False
                        break
                if match:
                    item.update(self._update_payload)
            r = Resp()
            r.data = []
            return r

        if self._is_delete:
            new_data = []
            for item in self.table.data:
                match = True
                for col, val in self._filters.items():
                    if str(item.get(col)) != str(val):
                        match = False
                        break
                if not match:
                    new_data.append(item)
            self.table.data = new_data
            r = Resp()
            r.data = []
            return r

        filtered = []
        for item in self.table.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            for col, val_list in self._in_filters.items():
                if item.get(col) not in val_list and str(item.get(col)) not in [str(x) for x in val_list]:
                    match = False
                    break
            for col, val in self._is_null.items():
                if val == 'null' or val is None:
                    if item.get(col) is not None:
                        match = False
                        break
            for col, pattern in self._ilike_filters.items():
                p = pattern.strip('%').lower()
                val_str = str(item.get(col) or '').lower()
                if p not in val_str:
                    match = False
                    break
            for col, pattern in self._like_filters.items():
                p = pattern.strip('%')
                val_str = str(item.get(col) or '')
                if p not in val_str:
                    match = False
                    break
            if match:
                filtered.append(dict(item))

        r = Resp()
        r.data = filtered
        return r


class MockSupabaseTable:
    def __init__(self, name, data=None):
        self.name = name
        self.data = [dict(d) for d in (data or [])]

    def select(self, *args, **kwargs):
        return MockQueryBuilder(self).select(*args, **kwargs)

    def insert(self, rows):
        return MockQueryBuilder(self).insert(rows)

    def update(self, payload):
        return MockQueryBuilder(self).update(payload)

    def delete(self):
        return MockQueryBuilder(self).delete()

    def eq(self, col, val):
        return MockQueryBuilder(self).eq(col, val)


class MockSupabaseClient:
    def __init__(self, tables_dict):
        self.tables = {k: MockSupabaseTable(k, v) for k, v in tables_dict.items()}

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockSupabaseTable(name)
        return self.tables[name]

    def rpc(self, name, params=None):
        class Resp:
            data = {'success': True}
            def execute(self):
                return self
        return Resp()


@pytest.fixture
def test_client():
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test_secret'
    return app.test_client()


# -----------------------------------------------------------------------------
# 1. Programs CRUD & Delete Reference Protection
# -----------------------------------------------------------------------------
def test_admin_programs_crud(test_client, monkeypatch):
    programs_data = [
        {'id': 1, 'program_name': 'BSIT'},
        {'id': 2, 'program_name': 'BSDS'},
    ]
    users_data = [{'id': 1, 'username': 'admin', 'role': 'Admin', 'program_id': 1}]
    courses_data = [{'course_id': 101, 'course_name': 'IT101', 'program_id': 1}]
    mock_db = MockSupabaseClient({
        'program': programs_data,
        'users': users_data,
        'course': courses_data,
        'schedule': [],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: mock_db.table('program').data)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'Admin'

    # 1. List programs
    res = test_client.get('/programs')
    assert res.status_code == 200
    assert b'BSIT' in res.data
    assert b'BSDS' in res.data

    # 2. Add Program
    res_add = test_client.post('/add_program', data={'program_name': 'BSCS'}, follow_redirects=True)
    assert res_add.status_code == 200
    assert any(p['program_name'] == 'BSCS' for p in mock_db.table('program').data)

    # 3. Add Duplicate Program (case-insensitive) should fail
    res_dup = test_client.post('/add_program', data={'program_name': 'bscs'}, follow_redirects=True)
    assert b'already exists' in res_dup.data

    # 4. Edit Program (converts to uppercase)
    res_edit = test_client.post('/edit_program/2', data={'program_name': 'BSDS - Data Science'}, follow_redirects=True)
    assert res_edit.status_code == 200
    bsds = next(p for p in mock_db.table('program').data if p['id'] == 2)
    assert bsds['program_name'] == 'BSDS - DATA SCIENCE'

    # 5. Delete referenced Program 1 (referenced by Course 101 & User 1) should be blocked
    res_del_ref = test_client.post('/delete_program/1', follow_redirects=True)
    assert b'Cannot delete program' in res_del_ref.data
    assert any(p['id'] == 1 for p in mock_db.table('program').data)

    # 6. Delete unreferenced Program (BSCS) should succeed
    bscs_obj = next(p for p in mock_db.table('program').data if p['program_name'] == 'BSCS')
    res_del_ok = test_client.post(f"/delete_program/{bscs_obj['id']}", follow_redirects=True)
    assert res_del_ok.status_code == 200
    assert not any(p['program_name'] == 'BSCS' for p in mock_db.table('program').data)


# -----------------------------------------------------------------------------
# 2. Courses Scoped to Programs
# -----------------------------------------------------------------------------
def test_courses_program_scoping_and_duplicate_rules(test_client, monkeypatch):
    programs_data = [
        {'id': 1, 'program_name': 'BSIT'},
        {'id': 2, 'program_name': 'BSDS'},
    ]
    courses_data = [
        {'course_id': 1, 'course_name': 'CS101', 'program_id': 1, 'semester': '1st Semester'},
    ]
    mock_db = MockSupabaseClient({
        'program': programs_data,
        'course': courses_data,
        'professor_load': [],
        'schedule': [],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: programs_data)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'Admin'

    # Same course code in ANOTHER program (BSDS, program_id=2) should succeed!
    res_diff_prog = test_client.post('/add_course', data={
        'course_name': 'CS101',
        'program_id': '2',
        'year_level': '1',
        'semester': '1st Semester',
        'units': '3',
        'lecture_hours': '3',
        'lab_hours': '0',
    }, follow_redirects=True)
    assert res_diff_prog.status_code == 200
    assert len([c for c in mock_db.table('course').data if c['course_name'] == 'CS101']) == 2

    # Duplicate course code in the SAME program (BSIT, program_id=1) must be blocked
    res_same_prog = test_client.post('/add_course', data={
        'course_name': 'CS101',
        'program_id': '1',
        'year_level': '1',
        'semester': '1st Semester',
        'units': '3',
        'lecture_hours': '3',
        'lab_hours': '0',
    }, follow_redirects=True)
    assert b'already exists in BSIT' in res_same_prog.data


# -----------------------------------------------------------------------------
# 3. Professor Load Import Cross-Program Error & 'Also teaches in' Badge
# -----------------------------------------------------------------------------
def test_professor_load_cross_program_import_and_badge(test_client, monkeypatch):
    # Test validate_import_data distinguishes cross-program courses
    target_program_courses = [
        {'course_id': 10, 'course_name': 'IT101', 'program_id': 1},
    ]
    all_institution_courses = [
        {'course_id': 10, 'course_name': 'IT101', 'program_id': 1},
        {'course_id': 20, 'course_name': 'DS201', 'program_id': 2},
    ]
    professors = [{'prof_id': 1, 'first_name': 'Grace', 'last_name': 'Hopper'}]
    existing_loads = []

    parsed_rows = [
        {'row_number': 2, 'raw_professor': 'Grace Hopper', 'raw_course': 'DS201', 'raw_sections': 1},
        {'row_number': 3, 'raw_professor': 'Grace Hopper', 'raw_course': 'UNKNOWN999', 'raw_sections': 1},
    ]

    val_res = professor_load_importer.validate_import_data(
        parsed_rows=parsed_rows,
        dividers=[],
        professors_list=professors,
        courses_list=target_program_courses,
        existing_loads_list=existing_loads,
        all_courses_list=all_institution_courses,
        target_program_name='BSIT',
    )

    row_errors = [r['errors'] for r in val_res['rows']]
    # DS201 is in BSDS -> "Course not offered in BSIT"
    assert any("Course not offered in BSIT" in err for err in row_errors[0])
    # UNKNOWN999 is in no program -> "Course not found in catalog"
    assert any("Course not found in catalog" in err for err in row_errors[1])


def test_professor_load_view_cross_program_badge(test_client, monkeypatch):
    # Prof 1 teaches in BSIT (3 units, 1 sec) AND BSDS (3 units, 2 sec = 6 units)
    courses_data = [
        {'course_id': 1, 'course_name': 'IT101', 'program_id': 1, 'units': 3.0, 'lecture_hours': 3.0, 'lab_hours': 0.0, 'ilp_hours': 0.0, 'program': {'program_name': 'BSIT'}},
        {'course_id': 2, 'course_name': 'DS201', 'program_id': 2, 'units': 3.0, 'lecture_hours': 3.0, 'lab_hours': 0.0, 'ilp_hours': 0.0, 'program': {'program_name': 'BSDS'}},
    ]
    profs_data = [
        {'prof_id': 1, 'first_name': 'Grace', 'last_name': 'Hopper', 'specialization': 'CS', 'program_id': 1, 'academic_ranking': {'name': 'Prof', 'min_units': 6, 'max_units': 18, 'min_hours': 10, 'max_hours': 40}},
    ]
    pc_data = [
        {'id': 101, 'prof_id': 1, 'course_id': 1, 'sections': 1, 'course': courses_data[0], 'professor': profs_data[0]},
        {'id': 102, 'prof_id': 1, 'course_id': 2, 'sections': 2, 'course': courses_data[1], 'professor': profs_data[0]},
    ]
    programs_data = [{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}]

    mock_db = MockSupabaseClient({
        'program': programs_data,
        'professor': profs_data,
        'course': courses_data,
        'professor_load': pc_data,
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: programs_data)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 2
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = test_client.get('/professor_load')
    assert res.status_code == 200
    # Shows BSIT load for Hopper
    assert b'IT101' in res.data
    # Does NOT show DS201 load directly in BSIT table
    assert b'DS201' not in res.data
    # Shows badge indicating Grace Hopper also teaches in BSDS (6 units)
    assert b'Also teaches in: BSDS (6 units)' in res.data


# -----------------------------------------------------------------------------
# 4. Schedule Views: Scheduler Program Tampering Rejected with 403
# -----------------------------------------------------------------------------
def test_scheduler_cannot_tamper_program_in_schedules(test_client, monkeypatch):
    programs_data = [{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}]
    mock_db = MockSupabaseClient({
        'program': programs_data,
        'schedule': [],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: programs_data)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 2
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # Scheduler attempting to view BSDS via program parameter must be rejected with 403
    res = test_client.get('/schedules?program=BSDS')
    assert res.status_code == 403


# -----------------------------------------------------------------------------
# 5. Room Schedule Masking & Professor Schedule Masking for Schedulers
# -----------------------------------------------------------------------------
def test_room_and_professor_schedule_cross_program_masking(test_client, monkeypatch):
    rooms_data = [{'room_id': 101, 'room_name': 'Lab 101', 'room_type': 'Laboratory'}]
    profs_data = [{'prof_id': 1, 'first_name': 'Ada', 'last_name': 'Lovelace', 'program_id': 1}]
    # Schedule has 1 booking from BSIT and 1 booking from BSDS in the same room / same professor
    sched_data = [
        {
            'schedule_id': 1,
            'professor_load_id': 1,
            'room_id': 101,
            'program_id': 1,
            'section': 'IT-1A',
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'semester': '1st Semester',
            'archive': False,
            'session_type': 'Laboratory',
            'program': {'program_name': 'BSIT'},
            'professor_load': {'professor_load_id': 1, 'prof_id': 1, 'course': {'course_name': 'IT-Net1'}, 'professor': profs_data[0]},
        },
        {
            'schedule_id': 2,
            'professor_load_id': 2,
            'room_id': 101,
            'program_id': 2,
            'section': 'DS-1A',
            'day': 'Tuesday',
            'class_start': '10:00:00',
            'class_end': '12:00:00',
            'semester': '1st Semester',
            'archive': False,
            'session_type': 'Laboratory',
            'program': {'program_name': 'BSDS'},
            'professor_load': {'professor_load_id': 2, 'prof_id': 1, 'course': {'course_name': 'DS-Math'}, 'professor': profs_data[0]},
        },
    ]

    mock_db = MockSupabaseClient({
        'room': rooms_data,
        'professor': profs_data,
        'professor_load': [
            {'professor_load_id': 1, 'id': 1, 'prof_id': 1, 'course': {'course_name': 'IT-Net1'}},
            {'professor_load_id': 2, 'id': 2, 'prof_id': 1, 'course': {'course_name': 'DS-Math'}},
        ],
        'schedule': sched_data,
        'timeslot': [],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}])

    # Schedulers from BSIT viewing Room 101:
    with test_client.session_transaction() as sess:
        sess['user_id'] = 2
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res_room = test_client.get('/room_schedule/101')
    assert res_room.status_code == 200
    # BSIT booking is visible in full detail
    assert b'IT-Net1' in res_room.data
    assert b'IT-1A' in res_room.data
    # BSDS booking is masked as Occupied - BSDS
    assert b'Occupied - BSDS' in res_room.data

    # Schedulers from BSIT viewing Prof 1 (Ada Lovelace):
    res_prof = test_client.get('/professor_schedule/1')
    assert res_prof.status_code == 200
    # BSIT course is visible
    assert b'IT-Net1' in res_prof.data
    # BSDS course is masked as Busy - BSDS (DS-Math)
    assert b'Busy - BSDS (DS-Math)' in res_prof.data


# -----------------------------------------------------------------------------
# 6. Concurrency Protection on Confirm
# -----------------------------------------------------------------------------
def test_concurrency_conflict_protection_on_confirm(test_client, monkeypatch):
    # Another program (BSDS, program_id=2) has an active schedule in Room 101 on Monday 08:00 - 11:00
    existing_other_sched = [
        {
            'schedule_id': 99,
            'room_id': 101,
            'program_id': 2,
            'section': 'DS-1A',
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '11:00:00',
            'semester': '1st Semester',
            'archive': False,
            'professor_load': {'prof_id': 50},
        }
    ]

    # BSIT scheduler preview tries to book the same Room 101 on Monday 09:00 - 12:00
    preview = [
        {
            'id': 1,
            'section': 'IT-1A',
            'semester': '1st Semester',
            'program': 'BSIT',
            'room_id': 101,
            'day': 'Monday',
            'start': '09:00:00',
            'end': '12:00:00',
            'session_type': 'Lecture',
            'professor_load_id': 10,
        }
    ]

    mock_db = MockSupabaseClient({
        'room': [{'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'}],
        'schedule': existing_other_sched,
        'professor_load': [{'id': 10, 'prof_id': 1, 'course_id': 1}],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_preview_for_user', lambda *args, **kwargs: preview)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 2
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # Confirming must be blocked by the concurrency conflict check
    res_confirm = test_client.post('/confirm_preview', follow_redirects=True)
    assert res_confirm.status_code == 200
    assert b'Cannot confirm schedule due to conflict with another program' in res_confirm.data


# -----------------------------------------------------------------------------
# 7. Restore Schedule from Archive: Concurrency Conflict Protection
# -----------------------------------------------------------------------------
def test_restore_schedule_cross_program_conflict(test_client, monkeypatch):
    # BSDS has an active schedule in Room 101 on Monday 08:00 - 11:00
    active_other_sched = {
        'schedule_id': 99,
        'room_id': 101,
        'program_id': 2,
        'section': 'DS-1A',
        'day': 'Monday',
        'class_start': '08:00:00',
        'class_end': '11:00:00',
        'semester': '1st Semester',
        'archive': False,
        'professor_load': {'prof_id': 50},
    }
    # BSIT has an archived batch with the same room/time
    archived_bsit_sched = {
        'schedule_id': 100,
        'room_id': 101,
        'program_id': 1,
        'section': 'IT-1A',
        'day': 'Monday',
        'class_start': '09:00:00',
        'class_end': '12:00:00',
        'semester': '1st Semester',
        'archive': True,
        'archive_batch_id': 'batch-conflict-test',
        'professor_load': {'prof_id': 1},
    }

    mock_db = MockSupabaseClient({
        'schedule': [active_other_sched, archived_bsit_sched],
        'program': [{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}],
    })
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, '_get_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}])

    with test_client.session_transaction() as sess:
        sess['user_id'] = 2
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res_restore = test_client.post('/restore_schedule/batch-conflict-test', follow_redirects=True)
    assert res_restore.status_code == 200
    assert b'Conflict with active schedule of another program' in res_restore.data
    # Verify the item remains archived
    assert archived_bsit_sched['archive'] is True


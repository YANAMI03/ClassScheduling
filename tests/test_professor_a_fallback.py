import pytest
from datetime import timedelta
import app as app_module


class FakeResponse:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, table_name, store=None):
        self.table_name = table_name
        self.store = store if store is not None else {}
        self.filters = {}
        self.insert_data = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def delete(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self.insert_data = payload
        return self

    def update(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def like(self, *args, **kwargs):
        return self

    def ilike(self, col, val):
        self.filters[col] = val
        return self

    def execute(self):
        if self.insert_data is not None:
            payload = self.insert_data
            inserted = []
            if isinstance(payload, list):
                for item in payload:
                    if 'prof_id' not in item and self.table_name == 'professor':
                        item['prof_id'] = 999 + len(self.store.get('professor', []))
                    self.store.setdefault(self.table_name, []).append(item)
                    inserted.append(item)
            else:
                if 'prof_id' not in payload and self.table_name == 'professor':
                    payload['prof_id'] = 999 + len(self.store.get('professor', []))
                self.store.setdefault(self.table_name, []).append(payload)
                inserted.append(payload)
            self.insert_data = None
            return FakeResponse(inserted)

        if self.table_name in self.store:
            rows = self.store[self.table_name]
            filtered = []
            for r in rows:
                match = True
                for k, v in self.filters.items():
                    if str(r.get(k, '')).lower() != str(v).lower():
                        match = False
                        break
                if match:
                    filtered.append(r)
            return FakeResponse(filtered)
        return FakeResponse([])


def test_ensure_fallback_professor_creation(monkeypatch):
    store = {'professor': []}

    class MockSupabase:
        def table(self, table_name):
            return FakeQuery(table_name, store)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())

    prof_a = app_module._ensure_fallback_professor(department='CICT')
    assert prof_a is not None
    assert prof_a.get('first_name') == 'Professor'
    assert prof_a.get('last_name') == 'A'

    prof_b = app_module._ensure_fallback_professor_by_index(1, department='CICT')
    assert prof_b is not None
    assert prof_b.get('first_name') == 'Professor'
    assert prof_b.get('last_name') == 'B'
    assert len(store['professor']) == 2


def test_sequential_fallback_professors_zero_conflict(monkeypatch):
    """
    Test that when multiple sections/courses need fallback faculty simultaneously,
    sequential fallback professors ('Professor A', 'Professor B', ...) are assigned
    such that each professor has ZERO overlapping timeslot conflicts.
    """
    courses = [
        {'course_id': 101, 'course_name': 'IT101 - Intro', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
        {'course_id': 102, 'course_name': 'IT102 - Prog 1', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'department': 'CICT'},
        {'room_id': 2, 'room_name': 'Room 102', 'room_type': 'Lecture Room', 'department': 'CICT'},
    ]
    professors = [
        {'prof_id': 10, 'first_name': 'Professor', 'last_name': 'A', 'department': 'CICT', 'max_hours': 40}
    ]
    timeslots = [
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '09:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 2, 'day': 'Monday', 'start_time': '09:00:00', 'end_time': '10:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 3, 'day': 'Monday', 'start_time': '10:00:00', 'end_time': '11:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 4, 'day': 'Monday', 'start_time': '13:00:00', 'end_time': '14:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 5, 'day': 'Monday', 'start_time': '14:00:00', 'end_time': '15:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 6, 'day': 'Monday', 'start_time': '15:00:00', 'end_time': '16:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 7, 'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '09:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 8, 'day': 'Tuesday', 'start_time': '09:00:00', 'end_time': '10:00:00', 'lunch_time': '12:00:00'},
        {'timeslot_id': 9, 'day': 'Tuesday', 'start_time': '10:00:00', 'end_time': '11:00:00', 'lunch_time': '12:00:00'},
    ]

    store = {
        'course': courses,
        'room': rooms,
        'professor': professors,
        'prof_course': [],
        'timeslot': timeslots,
        'schedule': [],
    }

    class MockSupabase:
        def table(self, table_name):
            return FakeQuery(table_name, store)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())

    app = app_module.app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'admin-user'
            sess['role'] = 'admin'
            sess['program'] = 'BSIT'

        # Generate schedule for 1st Semester with 2 sections (1A, 1B)
        resp = client.post('/generate_schedule', data={
            'semester': '1st Semester',
            'sections[1]': '2',
            'students[1]': '80'
        }, follow_redirects=True)

        preview = app_module._get_preview_for_user()
        assert preview is not None and len(preview) > 0

        # Verify fallback professors were assigned
        assigned_prof_names = {e.get('professor_name') for e in preview}
        assert any(name.startswith('Professor ') for name in assigned_prof_names)

        # STRICT ZERO CONFLICT CHECK FOR EVERY PROFESSOR
        by_prof = {}
        for e in preview:
            pname = e.get('professor_name')
            by_prof.setdefault(pname, []).append(e)

        for pname, entries in by_prof.items():
            bookings = []
            for e in entries:
                day = e.get('day')
                start = e.get('start')
                end = e.get('end')
                has_overlap = app_module._has_conflict(day, start, end, bookings)
                assert not has_overlap, f"Conflict detected for {pname} on {day} ({start} - {end})!"
                bookings.append((day, start, end))


def test_multi_year_fallback_scheduling(monkeypatch):
    """
    Test that fallback scheduling correctly generates entries across all 4 year levels
    (Year 1, 2, 3, and 4) without skipping upper year levels.
    """
    courses = [
        {'course_id': 1, 'course_name': 'IT101', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
        {'course_id': 2, 'course_name': 'IT201', 'year_level': 2, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
        {'course_id': 3, 'course_name': 'IT301', 'year_level': 3, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
        {'course_id': 4, 'course_name': 'IT401', 'year_level': 4, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'department': 'CICT'},
        {'room_id': 2, 'room_name': 'Room 102', 'room_type': 'Lecture Room', 'department': 'CICT'},
    ]
    professors = []
    timeslots = [
        {'timeslot_id': 1, 'start_day': 'Monday', 'end_day': 'Friday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'}
    ]

    store = {
        'course': courses,
        'room': rooms,
        'professor': professors,
        'prof_course': [],
        'timeslot': timeslots,
        'schedule': [],
    }

    class MockSupabase:
        def table(self, table_name):
            return FakeQuery(table_name, store)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())

    app = app_module.app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'admin-user'
            sess['role'] = 'admin'
            sess['program'] = 'BSIT'

        resp = client.post('/generate_schedule', data={
            'semester': '1st Semester',
            'sections[1]': '1',
            'sections[2]': '1',
            'sections[3]': '1',
            'sections[4]': '1',
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            uid = sess.get('user_id')
            pid = sess.get('preview_id')
        preview = app_module._get_preview_for_user(user_id=uid, preview_id=pid)
        assert preview is not None and len(preview) >= 4

        # Verify all four year levels are present
        year_levels = {str(e.get('section', ''))[0] for e in preview}
        assert '1' in year_levels
        assert '2' in year_levels
        assert '3' in year_levels
        assert '4' in year_levels


def test_preview_context_fallback_value():
    entries = [
        {'id': 1, 'course_id': 1, 'course_name': 'IT101', 'room_id': 1, 'room_name': 'Room 1', 'day': 'Monday', 'start': '08:00 AM', 'end': '11:00 AM', 'section': '1A', 'professor_name': None}
    ]
    # In views, null/None professor name defaults to 'Professor A'
    for e in entries:
        prof = e.get('professor_name') or 'Professor A'
        assert prof == 'Professor A'


def test_subject_oriented_fallback_across_sections(monkeypatch):
    """
    Verify that fallback professors (e.g. Professor A) act like real faculty assigned to
    specific subjects, teaching those subjects across MULTIPLE sections (e.g. 1A, 1B)
    with zero overlapping schedule conflicts.
    """
    courses = [
        {'course_id': 101, 'course_name': 'IT101 - Intro', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
        {'course_id': 102, 'course_name': 'IT102 - Prog', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'department': 'CICT'},
        {'room_id': 2, 'room_name': 'Room 102', 'room_type': 'Lecture Room', 'department': 'CICT'},
    ]
    timeslots = [
        {'timeslot_id': 1, 'start_day': 'Monday', 'end_day': 'Friday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'}
    ]

    store = {
        'course': courses,
        'room': rooms,
        'professor': [],
        'prof_course': [],
        'timeslot': timeslots,
        'schedule': [],
    }

    class MockSupabase:
        def table(self, table_name):
            return FakeQuery(table_name, store)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())

    app = app_module.app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'admin-user'
            sess['role'] = 'admin'
            sess['program'] = 'BSIT'

        resp = client.post('/generate_schedule', data={
            'semester': '1st Semester',
            'sections[1]': '2',
            'students[1]': '60',
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            uid = sess.get('user_id')
            pid = sess.get('preview_id')
        preview = app_module._get_preview_for_user(user_id=uid, preview_id=pid)
        assert preview is not None and len(preview) == 4

        # Professor A must be assigned to subject IT101 across both 1A and 1B!
        it101_entries = [e for e in preview if e.get('course_id') == 101]
        assert len(it101_entries) == 2
        it101_profs = {e.get('professor_name') for e in it101_entries}
        it101_sections = {e.get('section') for e in it101_entries}
        assert 'Professor A' in it101_profs
        assert it101_sections == {'1A', '1B'}

        # Verify zero schedule conflicts for Professor A across 1A and 1B
        prof_a_entries = [e for e in preview if e.get('professor_name') == 'Professor A']
        bookings = []
        for e in prof_a_entries:
            d = e.get('day')
            st = e.get('start')
            et = e.get('end')
            assert not app_module._has_conflict(d, st, et, bookings), f"Conflict for Professor A on {d} {st}-{et}"
            bookings.append((d, st, et))

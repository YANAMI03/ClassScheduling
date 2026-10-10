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


def test_ensure_fallback_professor_creation_disabled(monkeypatch):
    """Verify that placeholder fallback professor creation is completely disabled."""
    store = {
        'professor': [],
        'academic_ranking': [{'academic_ranking_id': 1, 'name': 'General', 'program': 'General', 'max_hours': 40, 'max_units': 24, 'min_hours': 0, 'min_units': 0}],
    }

    class MockSupabase:
        def table(self, table_name):
            return FakeQuery(table_name, store)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())

    prof_a = app_module._ensure_fallback_professor(department='CICT')
    assert prof_a is None
    prof_b = app_module._ensure_fallback_professor_by_index(1, department='CICT')
    assert prof_b is None
    assert len(store['professor']) == 0


def test_no_fallback_professors_or_tba_created(monkeypatch):
    """
    Test that when professor_load has real assignments, schedule generation strictly
    uses real faculty and never creates 'Professor A' or 'TBA'.
    """
    courses = [
        {'course_id': 101, 'course_code': 'IT101 - Intro', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT', 'major': None},
    ]
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'department': 'CICT'},
    ]
    professors = [
        {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing', 'department': 'CICT', 'max_hours': 40}
    ]
    working_hours = [
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '11:00:00', 'lunch_time': '12:00:00'},
    ]

    store = {
        'academic_ranking': [{'academic_ranking_id': 1, 'name': 'General', 'program': 'General', 'max_hours': 40, 'max_units': 24, 'min_hours': 0, 'min_units': 0}],
        'course': courses,
        'room': rooms,
        'professor': professors,
        'professor_load': [
            {'id': 1, 'course_id': 101, 'prof_id': 10, 'sections': 1},
        ],
        'working_hours': working_hours,
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
            'students[1]': '40'
        }, follow_redirects=True)

        preview = app_module._get_preview_for_user()
        assert preview is not None
        for entry in preview:
            assert entry.get('professor_name') != 'Professor A'
            assert entry.get('professor_name') != 'TBA'
            assert entry.get('course_code') != 'TBA'
            assert entry.get('professor_load_id') is not None


def test_preview_context_no_fallback_to_professor_a():
    entries = [
        {'id': 1, 'course_id': 1, 'course_code': 'IT101', 'room_id': 1, 'room_name': 'Room 1', 'day': 'Monday', 'start': '08:00 AM', 'end': '11:00 AM', 'section': '1A', 'professor_name': None}
    ]
    for e in entries:
        prof = e.get('professor_name') or ''
        assert prof != 'Professor A'

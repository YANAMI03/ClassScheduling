"""Chair click-to-edit: field restrictions, conflicts, working hours, room type."""

from types import SimpleNamespace

import pytest

import app as app_module


def _sched(sid, plid, room, day, start, end, section, session='Lecture',
           course='IT101', prof='Alan Turing', program=1, archive=False):
    return {
        'schedule_id': sid,
        'professor_load_id': plid,
        'room_id': room,
        'day': day,
        'class_start': start,
        'class_end': end,
        'session_type': session,
        'section': section,
        'archive': archive,
        'professor_load': {
            'professor_load_id': plid,
            'professor_id': plid,
            'professor_name': prof,
            'course_id': 1,
            'course': {'course_code': course, 'program_id': program},
        },
    }


class MockQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.filters = []
        self.limit_val = None
        self.action = 'select'
        self.payload = None

    def select(self, *a, **k):
        self.action = 'select'
        return self

    def eq(self, c, v):
        self.filters.append(('eq', c, str(v).lower()))
        return self

    def update(self, p):
        self.action = 'update'
        self.payload = p
        return self

    def limit(self, n):
        self.limit_val = n
        return self

    def in_(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def insert(self, p):
        return self

    def _val(self, r, c):
        if c.startswith('professor_load.course.'):
            return str(((r.get('professor_load') or {}).get('course') or {}).get(c.split('.')[-1], '')).lower()
        return str(r.get(c, '')).lower()

    def execute(self):
        rows = [dict(r) for r in self.store.get(self.name, [])]
        for _, c, v in self.filters:
            rows = [r for r in rows if self._val(r, c) == v]
        if self.action == 'update':
            for r in self.store.get(self.name, []):
                if all(self._val(r, c) == v for _, c, v in self.filters):
                    r.update(self.payload)
            return SimpleNamespace(data=rows)
        if self.limit_val is not None:
            rows = rows[:self.limit_val]
        return SimpleNamespace(data=rows)


class MockSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return MockQuery(self.store, name)


def _install(monkeypatch, store, role='chair', program_id=1):
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(store))
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda *a, **k: program_id)
    monkeypatch.setattr(app_module, 'log_activity', lambda *a, **k: None)


def _client(role='chair'):
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'chair-1'
        sess['role'] = role
        sess['program'] = 'BSIT'
        sess['program_id'] = 1
    return client


def _base_store(schedules=None):
    return {
        'schedule': schedules or [],
        'room': [
            {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'archive': False},
            {'room_id': 2, 'room_name': 'Lab 201', 'room_type': 'Laboratory Room', 'archive': False},
        ],
        'working_hours': [
            {'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'archive': False},
            {'day': 'Tuesday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'archive': False},
        ],
    }


def test_chair_moves_session_to_free_slot(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A')])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 1})
    assert res.status_code == 200
    assert res.get_json().get('success') is True
    row = store['schedule'][0]
    assert row['class_start'] == '14:00:00' and row['class_end'] == '16:00:00'


def test_professor_course_and_session_are_ignored(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A')])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={
        'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 1,
        'professor_load_id': 999, 'course_code': 'HACK', 'session_type': 'Laboratory',
        'section': '9Z', 'semester': '2nd Semester',
    })
    assert res.status_code == 200
    row = store['schedule'][0]
    assert row['professor_load_id'] == 101       # unchanged
    assert row['session_type'] == 'Lecture'      # unchanged
    assert row['section'] == '1A'                # unchanged


def test_professor_conflict_rejected(monkeypatch):
    store = _base_store([
        _sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A', course='IT101', prof='Alan Turing'),
        _sched(11, 101, 2, 'Monday', '14:00:00', '16:00:00', '2B', course='IT102', prof='Alan Turing'),
    ])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 1})
    assert res.status_code == 409
    msg = res.get_json().get('message', '')
    assert 'Alan Turing' in msg and 'already has' in msg


def test_section_conflict_rejected(monkeypatch):
    store = _base_store([
        _sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A'),
        _sched(11, 202, 2, 'Monday', '14:00:00', '16:00:00', '1A', course='IT103'),
    ])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '15:00', 'end': '17:00', 'room_id': 1})
    assert res.status_code == 409
    assert 'Section 1A already has' in res.get_json().get('message', '')


def test_room_conflict_rejected(monkeypatch):
    store = _base_store([
        _sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A'),
        _sched(11, 202, 1, 'Monday', '14:00:00', '16:00:00', '2B', course='IT104'),
    ])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '15:00', 'end': '17:00', 'room_id': 1})
    assert res.status_code == 409
    assert 'already used by' in res.get_json().get('message', '')


def test_back_to_back_is_allowed(monkeypatch):
    # Same professor AND same room; the candidate ends exactly when the other starts.
    store = _base_store([
        _sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A', prof='Alan Turing'),
        _sched(11, 101, 1, 'Monday', '14:00:00', '16:00:00', '2B', course='IT105', prof='Alan Turing'),
    ])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '16:00', 'end': '18:00', 'room_id': 1})
    # start < other_end AND end > other_start is the only overlap rule; 16:00 == 16:00 is touching, not overlapping.
    assert res.status_code == 200
    row = store['schedule'][0]
    assert row['class_start'] == '16:00:00' and row['class_end'] == '18:00:00'


def test_lunch_and_working_hours_rejected(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A')])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '12:00', 'end': '13:00', 'room_id': 1})
    assert res.status_code == 400
    assert 'Lunch break conflict' in res.get_json().get('message', '')

    res2 = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '06:00', 'end': '08:00', 'room_id': 1})
    assert res2.status_code == 400
    assert 'outside the operating hours' in res2.get_json().get('message', '')


def test_room_type_mismatch_rejected(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A', session='Lecture')])
    _install(monkeypatch, store)
    client = _client('chair')

    # Lecture session cannot move into the laboratory room.
    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 2})
    assert res.status_code == 400
    assert 'Room type mismatch' in res.get_json().get('message', '')


def test_archived_session_cannot_be_edited(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A', archive=True)])
    _install(monkeypatch, store)
    client = _client('chair')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 1})
    assert res.status_code == 404


def test_viewer_cannot_edit(monkeypatch):
    store = _base_store([_sched(10, 101, 1, 'Monday', '08:00:00', '10:00:00', '1A')])
    _install(monkeypatch, store)
    client = _client('viewer')

    res = client.post('/edit_schedule_entry/10', json={'day': 'Monday', 'start': '14:00', 'end': '16:00', 'room_id': 1})
    assert res.status_code == 403

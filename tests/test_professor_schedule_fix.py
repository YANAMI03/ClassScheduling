"""Tests for Professor Schedule page bug fix, pagination, filtering, and error handling."""

import pytest
import app as app_module


class MockTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self._data = list(data or [])
        self._filters = {}
        self._offset = 0
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        return self

    def range(self, start, end):
        self._offset = start
        self._limit = end - start + 1
        return self

    def execute(self):
        class Resp:
            def __init__(self, data):
                self.data = data

        current = list(self._data)
        if self._limit is not None:
            sliced = current[self._offset : self._offset + self._limit]
        else:
            sliced = current[self._offset :]
        return Resp(sliced)


def test_professor_schedule_renders_professors(monkeypatch):
    """Test that /professor_schedule displays professor cards when active schedule rows exist."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-scheduler'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    sample_profs = [
        {
            'prof_id': 1,
            'first_name': 'Gloria',
            'last_name': 'Alcantara',
            'specialization': 'Programming',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': {'name': 'Instructor I', 'min_units': 15, 'max_units': 21, 'min_hours': 20, 'max_hours': 30},
        },
        {
            'prof_id': 2,
            'first_name': 'Emilsa',
            'last_name': 'Bantug',
            'specialization': 'Database',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': {'name': 'Instructor I', 'min_units': 15, 'max_units': 21, 'min_hours': 20, 'max_hours': 30},
        }
    ]

    sample_schedules = [
        {
            'schedule_id': 101,
            'professor_load_id': 201,
            'section': 'BSIT 1-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'professor_load': {'prof_id': 1, 'course_id': 10, 'course': {'course_code': 'IT101'}},
            'room': {'room_name': 'Lab 1'},
        },
        {
            'schedule_id': 102,
            'professor_load_id': 202,
            'section': 'BSIT 2-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Tuesday',
            'class_start': '10:00:00',
            'class_end': '13:00:00',
            'professor_load': {'prof_id': 2, 'course_id': 11, 'course': {'course_code': 'IT102'}},
            'room': {'room_name': 'Lab 2'},
        }
    ]

    def mock_table(name):
        if name == 'professor':
            return MockTable('professor', sample_profs)
        if name == 'schedule':
            return MockTable('schedule', sample_schedules)
        return MockTable(name, [])

    monkeypatch.setattr(app_module.supabase, 'table', mock_table)

    resp = client.get('/professor_schedule')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'Gloria Alcantara' in html
    assert 'Emilsa Bantug' in html
    assert 'No professor schedules match the active criteria.' not in html
    assert 'alert-danger' not in html


def test_professor_schedule_filters(monkeypatch):
    """Test filtering by day and professor."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-scheduler'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    sample_profs = [
        {
            'prof_id': 1,
            'first_name': 'Gloria',
            'last_name': 'Alcantara',
            'specialization': 'Programming',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': {'name': 'Instructor I', 'min_units': 15, 'max_units': 21, 'min_hours': 20, 'max_hours': 30},
        },
        {
            'prof_id': 2,
            'first_name': 'Emilsa',
            'last_name': 'Bantug',
            'specialization': 'Database',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': {'name': 'Instructor I', 'min_units': 15, 'max_units': 21, 'min_hours': 20, 'max_hours': 30},
        }
    ]

    sample_schedules = [
        {
            'schedule_id': 101,
            'professor_load_id': 201,
            'section': 'BSIT 1-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'professor_load': {'prof_id': 1, 'course_id': 10, 'course': {'course_code': 'IT101'}},
            'room': {'room_name': 'Lab 1'},
        },
        {
            'schedule_id': 102,
            'professor_load_id': 202,
            'section': 'BSIT 2-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Tuesday',
            'class_start': '10:00:00',
            'class_end': '13:00:00',
            'professor_load': {'prof_id': 2, 'course_id': 11, 'course': {'course_code': 'IT102'}},
            'room': {'room_name': 'Lab 2'},
        }
    ]

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: MockTable(name, sample_profs if name == 'professor' else (sample_schedules if name == 'schedule' else [])))

    # Filter by day=Monday
    resp_monday = client.get('/professor_schedule?day=Monday')
    assert resp_monday.status_code == 200
    html_monday = resp_monday.get_data(as_text=True)
    assert 'View schedule for Professor Gloria Alcantara' in html_monday
    assert 'View schedule for Professor Emilsa Bantug' not in html_monday

    # Filter by prof_id=2
    resp_prof2 = client.get('/professor_schedule?prof_id=2')
    assert resp_prof2.status_code == 200
    html_prof2 = resp_prof2.get_data(as_text=True)
    assert 'View schedule for Professor Emilsa Bantug' in html_prof2
    assert 'View schedule for Professor Gloria Alcantara' not in html_prof2


def test_professor_schedule_empty_state_when_no_match(monkeypatch):
    """Test that empty state message only appears when query succeeded and there are truly no matches."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-scheduler'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    sample_profs = [
        {
            'prof_id': 1,
            'first_name': 'Gloria',
            'last_name': 'Alcantara',
            'specialization': 'Programming',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': None,
        }
    ]

    sample_schedules = [
        {
            'schedule_id': 101,
            'professor_load_id': 201,
            'section': 'BSIT 1-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'professor_load': {'prof_id': 1, 'course_id': 10, 'course': {'course_code': 'IT101'}},
            'room': {'room_name': 'Lab 1'},
        }
    ]

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: MockTable(name, sample_profs if name == 'professor' else (sample_schedules if name == 'schedule' else [])))

    # Filter with non-matching day
    resp = client.get('/professor_schedule?day=Friday')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'No professor schedules match the active criteria.' in html
    assert 'alert-danger' not in html


def test_professor_schedule_error_state_on_query_failure(monkeypatch):
    """Test that when fetching schedules fails, an error banner is shown instead of the empty state."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-scheduler'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    class FailingTable:
        def select(self, *args, **kwargs):
            raise RuntimeError("Database connection timed out")

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FailingTable())

    resp = client.get('/professor_schedule')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Must show error banner
    assert 'alert-danger' in html
    assert 'Failed to load professor schedules' in html
    # Must NOT show the false empty state
    assert 'No professor schedules match the active criteria.' not in html


def test_professor_schedule_pagination_loop(monkeypatch):
    """Test that professor_schedule correctly paginates when schedule rows exceed 1000 items."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-scheduler'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    sample_profs = [
        {
            'prof_id': 1,
            'first_name': 'Gloria',
            'last_name': 'Alcantara',
            'specialization': 'Programming',
            'time_designation': 5,
            'academic_ranking_id': 1,
            'academic_ranking': None,
        }
    ]

    # Create 1500 schedule rows
    large_schedules = []
    for i in range(1500):
        large_schedules.append({
            'schedule_id': 1000 + i,
            'professor_load_id': 201,
            'section': 'BSIT 1-A',
            'semester': '1st Semester',
            'major': 'Web Development',
            'program_id': 1,
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '09:00:00',
            'professor_load': {'prof_id': 1, 'course_id': 10, 'course': {'course_code': 'IT101'}},
            'room': {'room_name': 'Lab 1'},
        })

    mock_sched = MockTable('schedule', large_schedules)
    monkeypatch.setattr(app_module.supabase, 'table', lambda name: MockTable('professor', sample_profs) if name == 'professor' else mock_sched)

    resp = client.get('/professor_schedule')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'Gloria Alcantara' in html
    assert '<strong>1500</strong> classes assigned' in html
    assert 'No professor schedules match the active criteria.' not in html

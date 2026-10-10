import app as app_module
from postgrest.exceptions import APIError
import pytest


class MockTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self.data = list(data or [])
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filters = {}
        self._in_filters = {}
        self._neq_filters = {}
        self._pending_update = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, rows):
        if isinstance(rows, list):
            self.inserted.extend(rows)
            self.data.extend(rows)
        else:
            self.inserted.append(rows)
            self.data.append(rows)
        return self

    def update(self, payload):
        self._pending_update = dict(payload)
        self.updated.append(payload)
        return self

    def delete(self):
        self.deleted.append(dict(self._filters))
        self.data = [
            item for item in self.data
            if not all(str(item.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def neq(self, col, val):
        self._neq_filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def limit(self, count):
        return self

    def order(self, *args, **kwargs):
        return self

    def like(self, col, val):
        return self

    def ilike(self, col, val):
        return self

    def execute(self):
        result = list(self.data)
        if self._pending_update is not None and self._filters:
            for item in result:
                if all(str(item.get(k)) == str(v) for k, v in self._filters.items()):
                    item.update(self._pending_update)
            self._pending_update = None
        for col, val in self._filters.items():
            result = [item for item in result if str(item.get(col)) == str(val)]
        for col, val in self._neq_filters.items():
            result = [item for item in result if str(item.get(col)) != str(val)]
        for col, vals in self._in_filters.items():
            result = [item for item in result if item.get(col) in vals]
        class Response:
            def __init__(self, data):
                self.data = data
        return Response(result)


class MockSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MockTable(name)
        return self._tables[name]


def _build_mock_db():
    courses = [
        {
            'course_id': 101,
            'course_code': 'IT101 - Programming 1',
            'course_code': 'IT101',
            'units': 3.0,
            'lecture_hours': 2.0,
            'lab_hours': 3.0,
            'ilp_hours': 1.0,
            'weekly_hours': 6.0,
            'program': 'BSIT',
            'year_level': 1,
            'semester': '1st Semester',
        },
        {
            'course_id': 102,
            'course_code': 'IT102 - Discrete Math',
            'course_code': 'IT102',
            'units': 3.0,
            'lecture_hours': 3.0,
            'lab_hours': 0.0,
            'ilp_hours': 0.0,
            'weekly_hours': 3.0,
            'program': 'BSIT',
            'year_level': 1,
            'semester': '1st Semester',
        },
        {
            'course_id': 103,
            'course_code': 'IT103 - Heavy Lab Course',
            'course_code': 'IT103',
            'units': 16.0,
            'lecture_hours': 10.0,
            'lab_hours': 15.0,
            'ilp_hours': 0.0,
            'weekly_hours': 25.0,
            'program': 'BSIT',
            'year_level': 2,
            'semester': '1st Semester',
        },
    ]

    professors = [
        {
            'prof_id': 1,
            'first_name': 'Alan',
            'last_name': 'Turing',
            'program': 'BSIT',
            'department': 'CICT',
            'specialization': 'Computer Science',
            'max_hours': 40,
            'max_units': 15,
            'min_units': 6,
        },
        {
            'prof_id': 2,
            'first_name': 'Grace',
            'last_name': 'Hopper',
            'program': 'BSIT',
            'department': 'CICT',
            'specialization': 'Software Engineering',
            'max_hours': 40,
            'max_units': 24,
            'min_units': 12,
        },
    ]

    tables = {
        'course': MockTable('course', courses),
        'professor': MockTable('professor', professors),
        'professor_load': MockTable('professor_load', []),
        'schedule': MockTable('schedule', []),
        'users': MockTable('users', [{
            'id': 'scheduler-1',
            'role': 'scheduler',
            'department': 'CICT',
            'program': 'BSIT',
            'program_id': 1,
        }]),
        'program': MockTable('program', [{
            'id': 1,
            'program_name': 'BSIT',
            'department': 'CICT',
        }]),
        'semester': MockTable('semester', []),
    }
    return MockSupabase(tables)


@pytest.fixture
def test_setup(monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 'scheduler-1'
        session['username'] = 'scheduler_user'
        session['role'] = 'scheduler'
        session['department'] = 'CICT'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    db = _build_mock_db()
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    return client, db


def test_add_professor_load_route_removed(test_setup):
    """Manual add_professor_load was removed per architecture spec; must return 404."""
    client, db = test_setup
    response = client.post('/add_professor_load', data={
        'prof_id': '1',
        'course_ids': ['101', '102'],
        'sections_101': '2',
        'sections_102': '1',
    })
    assert response.status_code == 404
    assert len(db.table('professor_load').inserted) == 0


def test_add_professor_load_allows_high_hours_without_max_hours_cap(test_setup):
    """Manual add route is removed -> returns 404."""
    client, db = test_setup
    response = client.post('/add_professor_load', data={
        'prof_id': '1',
        'course_ids': ['101'],
        'sections_101': '2',
    })
    assert response.status_code == 404


def test_add_professor_load_allows_any_course_units(test_setup):
    """Manual add route is removed -> returns 404."""
    client, db = test_setup
    response = client.post('/add_professor_load', data={
        'prof_id': '1',
        'course_ids': ['103'],
        'sections_103': '1',
    })
    assert response.status_code == 404


def test_add_professor_load_does_not_warn_about_min_units(test_setup):
    """Manual add route is removed -> returns 404."""
    client, db = test_setup
    response = client.post('/add_professor_load', data={
        'prof_id': '1',
        'course_ids': ['102'],
        'sections_102': '1',
    })
    assert response.status_code == 404


def test_update_prof_with_courses_does_not_enforce_unit_limits(test_setup):
    """Manual update route is removed -> returns 404."""
    client, db = test_setup
    response = client.post('/update_prof_with_courses/1', data={
        'first_name': 'Alan',
        'last_name': 'Turing',
        'course_ids': ['103'],
        'edit_sections_103': '1',
    })
    assert response.status_code == 404


def test_example_cc101_multiple_sections(test_setup):
    """Manual add route is removed -> returns 404."""
    client, db = test_setup
    response = client.post('/add_professor_load', data={
        'prof_id': '2',
        'course_ids': ['201'],
        'sections_201': '3',
    })
    assert response.status_code == 404


def test_professor_load_page_renders_load_components(test_setup):
    """Test that professor_load.html renders table, tracker, and has stripped manual add forms."""
    client, db = test_setup

    response = client.get('/professor_load')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Retained components
    assert 'Current Assignments & Load Status' in html
    assert 'load-status-badge' in html
    assert 'Weekly Teaching Hours' in html
    assert 'teaching-load-tracker-card' in html
    assert 'selected-courses-breakdown-card' in html
    assert 'importLoadModal' in html

    # Removed manual write controls
    assert 'assignCourseForm' not in html
    assert 'btn-assign-courses' not in html
    assert 'editModal' not in html


def test_api_professor_load_returns_assignments(test_setup):
    """API endpoint for manual load form preloading was removed -> returns 404."""
    client, db = test_setup
    response = client.get('/api/professor_load/1')
    assert response.status_code == 404


def test_professor_load_template_contains_existing_assignments(test_setup):
    """Verify manual form bindings and editModal preloading scripts were removed."""
    client, db = test_setup
    response = client.get('/professor_load')
    assert response.status_code == 200
    html = response.data.decode('utf-8')
    assert 'existingProfAssignments' not in html
    assert 'editModal' not in html
    assert 'Current Assignments & Load Status' in html


def test_edit_professor_load_success(test_setup):
    """Manual edit route was removed -> returns 404."""
    client, db = test_setup
    response = client.post('/edit_professor_load/10', data={
        'prof_id': '2',
        'course_id': '102',
        'sections': '3',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 404


def test_edit_professor_load_validation_errors(test_setup):
    """Manual edit route was removed -> returns 404."""
    client, db = test_setup
    res = client.post('/edit_professor_load/10', data={
        'sections': '0',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 404


def test_edit_professor_load_duplicate_prevented(test_setup):
    """Manual edit route was removed -> returns 404."""
    client, db = test_setup
    res = client.post('/edit_professor_load/10', data={
        'course_id': '102',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 404


def test_edit_professor_load_cross_program_tampering_403(test_setup):
    """Manual edit route was removed -> returns 404 (never 403)."""
    client, db = test_setup
    res = client.post('/edit_professor_load/10', data={
        'course_id': '999',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 404


def test_edit_professor_load_blocked_by_active_schedule(test_setup):
    """Manual edit route was removed -> returns 404."""
    client, db = test_setup
    res = client.post('/edit_professor_load/10', data={
        'course_id': '101',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 404

import app as app_module
from postgrest.exceptions import APIError
import pytest


class MockTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self.data = data or []
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filters = {}
        self._in_filters = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, rows):
        if isinstance(rows, list):
            self.inserted.extend(rows)
        else:
            self.inserted.append(rows)
        return self

    def update(self, payload):
        self.updated.append(payload)
        return self

    def delete(self):
        self.deleted.append(dict(self._filters))
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        # Filter self.data according to eq and in_
        res = []
        for item in self.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            for col, val_list in self._in_filters.items():
                if item.get(col) not in val_list and int(item.get(col, -999)) not in val_list:
                    match = False
                    break
            if match:
                res.append(item)

        class Response:
            pass

        r = Response()
        r.data = res
        return r


class MockSupabase:
    def __init__(self, professors=None, courses=None, prof_courses=None):
        self.tables = {
            'professor': MockTable('professor', professors or []),
            'course': MockTable('course', courses or []),
            'prof_course': MockTable('prof_course', prof_courses or []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name)
        return self.tables[name]


def _build_mock_db():
    profs = [
        {
            'prof_id': 1,
            'first_name': 'Alan',
            'last_name': 'Turing',
            'department': 'CICT',
            'specialization': 'Computer Science',
            'min_units': 6.0,
            'max_units': 15.0,
        },
        {
            'prof_id': 2,
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'department': 'CICT',
            'specialization': 'Algorithms',
            'min_units': 12.0,
            'max_units': 24.0,
        }
    ]

    courses = [
        {
            'course_id': 101,
            'course_name': 'Intro to Programming',
            'program': 'BSIT',
            'year_level': 1,
            'lecture_hours': 2.0,
            'lab_hours': 3.0,
            'ilp_hours': 1.0,
            'units': 3.0,
        },
        {
            'course_id': 102,
            'course_name': 'Data Structures',
            'program': 'BSIT',
            'year_level': 2,
            'lecture_hours': 3.0,
            'lab_hours': 0.0,
            'ilp_hours': 0.0,
            'units': 3.0,
        },
        {
            'course_id': 103,
            'course_name': 'Capstone Project',
            'program': 'BSIT',
            'year_level': 4,
            'lecture_hours': 5.0,
            'lab_hours': 5.0,
            'ilp_hours': 0.0,
            'units': 20.0,
        }
    ]

    return MockSupabase(profs, courses)


@pytest.fixture
def test_setup(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin_user'
        session['role'] = 'admin'
        session['department'] = 'CICT'

    db = _build_mock_db()
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    return client, db


def test_add_prof_course_with_multiple_sections(test_setup):
    """Test assigning courses to a professor with section counts."""
    client, db = test_setup

    # Course 101: lec=2, lab=3, ilp=1 (sum=6 hrs, 3 units). Sections=2 -> 12 hrs, 6 units.
    # Course 102: lec=3, lab=0, ilp=0 (sum=3 hrs, 3 units). Sections=1 -> 3 hrs, 3 units.
    # Total = 15 hrs, 9 units. Prof 1 max_units=15, min_units=6.
    # This is safe and within limits.
    response = client.post('/add_prof_course', data={
        'prof_id': '1',
        'course_ids': ['101', '102'],
        'sections_101': '2',
        'sections_102': '1',
    }, follow_redirects=True)

    assert response.status_code == 200
    # Check that rows were inserted into prof_course with section counts
    inserted = db.table('prof_course').inserted
    assert len(inserted) == 2
    inserted_by_cid = {row['course_id']: row for row in inserted}
    assert inserted_by_cid[101]['sections'] == 2
    assert inserted_by_cid[102]['sections'] == 1


def test_add_prof_course_allows_high_hours_without_max_hours_cap(test_setup):
    """Test that assignment is NOT blocked by weekly hours since max_hours constraint was removed."""
    client, db = test_setup

    # Course 101: 6 hrs/sec. With 2 sections = 12 hrs, 6 units. Prof 1 max_units = 15.
    response = client.post('/add_prof_course', data={
        'prof_id': '1',
        'course_ids': ['101'],
        'sections_101': '2',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert len(db.table('prof_course').inserted) > 0


def test_add_prof_course_blocks_when_units_exceed_max(test_setup):
    """Test that assignment is blocked if total_units > max_units."""
    client, db = test_setup

    # Course 103: 20 units. Prof 1 max_units = 15.
    response = client.post('/add_prof_course', data={
        'prof_id': '1',
        'course_ids': ['103'],
        'sections_103': '1',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'Cannot assign courses: Total units' in response.data
    assert b'exceed professor maximum limit' in response.data
    assert len(db.table('prof_course').inserted) == 0


def test_add_prof_course_warns_when_units_below_min(test_setup):
    """Test that assignment succeeds with a warning when total_units < min_units."""
    client, db = test_setup

    # Course 102: 3 units, 1 section. Prof 1 has min_units = 6.
    # Total units = 3 < 6. Should allow assignment with warning note.
    response = client.post('/add_prof_course', data={
        'prof_id': '1',
        'course_ids': ['102'],
        'sections_102': '1',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'below minimum target' in response.data
    assert len(db.table('prof_course').inserted) == 1


def test_update_prof_with_courses_enforces_limits(test_setup):
    """Test that update_prof_with_courses enforces max limits."""
    client, db = test_setup

    # Updating prof 1 with course 103 (20 units > 15 max_units)
    response = client.post('/update_prof_with_courses/1', data={
        'first_name': 'Alan',
        'last_name': 'Turing',
        'department': 'CICT',
        'specialization': 'Computer Science',
        'min_units': '6',
        'max_units': '15',
        'course_ids': ['103'],
        'edit_sections_103': '1',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'Total units' in response.data


def test_example_cc101_multiple_sections(test_setup):
    """
    Test user exact example:
    Course: CC-101
    Lecture/Lab/ILP Hours: 5 hours/week (e.g. lec=2, lab=2, ilp=1)
    Units: 3 units
    Assigned Sections: 3
    Expected:
    Total Hours = 5 * 3 = 15 hours/week
    Total Units = 3 * 3 = 9 units
    Prof 2: max_hours=40, max_units=24, min_units=12 (9 units will trigger below min target note).
    """
    client, db = test_setup

    cc101 = {
        'course_id': 201,
        'course_name': 'CC-101',
        'program': 'BSIT',
        'year_level': 1,
        'lecture_hours': 2.0,
        'lab_hours': 2.0,
        'ilp_hours': 1.0,
        'units': 3.0,
    }
    db.table('course').data.append(cc101)

    response = client.post('/add_prof_course', data={
        'prof_id': '2',
        'course_ids': ['201'],
        'sections_201': '3',
    }, follow_redirects=True)

    assert response.status_code == 200
    inserted = db.table('prof_course').inserted
    assert len(inserted) == 1
    assert inserted[0]['prof_id'] == 2
    assert inserted[0]['course_id'] == 201
    assert inserted[0]['sections'] == 3
    # Flash message should confirm 15.0 hrs and 9.0 units
    assert b'15.0 hrs, 9.0 units' in response.data


def test_prof_course_page_renders_load_components(test_setup):
    """Test that prof_course.html renders the Teaching Load Summary Panel, Number of Sections, and live breakdown."""
    client, db = test_setup

    response = client.get('/prof_course')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Check key UI features
    assert 'Teaching Load Summary' in html
    assert 'Weekly Hours' in html
    assert 'Total Units' in html
    assert 'load-status-badge' in html
    assert 'Number of Sections:' in html
    assert 'selected-courses-breakdown-card' in html
    assert 'selected-courses-breakdown-list' in html
    assert 'btn-assign-courses' in html
    assert 'btn-step' in html
    assert 'btn-sec-plus' in html
    assert 'btn-sec-minus' in html
    # Buttons should be clickable, not disabled
    assert 'btn-sec-plus" data-cid="101" disabled' not in html
    assert 'Current Assignments & Load Status' in html

    # Real-time interactive components
    assert 'breakdown-sec-minus' in html
    assert 'breakdown-sec-plus' in html
    assert 'breakdown-sec-input' in html
    assert 'btn-remove-breakdown' in html
    assert 'handleAddSectionChange' in html
    assert 'handleAddRemove' in html
    assert 'Pending Professor' in html


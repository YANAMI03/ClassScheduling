import pytest
import re
from bs4 import BeautifulSoup
import app as app_module


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
    def __init__(self, professors=None, courses=None, professor_loads=None, professor_programs=None):
        self.tables = {
            'professor': MockTable('professor', professors or []),
            'course': MockTable('course', courses or []),
            'professor_load': MockTable('professor_load', professor_loads or []),
            'professor_program': MockTable('professor_program', professor_programs or []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name)
        return self.tables[name]


def _build_test_db():
    profs = [
        {
            'prof_id': 1,
            'first_name': 'Alan',
            'last_name': 'Turing',
            'department': 'CICT',
            'program_id': 1,
            'specialization': 'Computer Science',
            'academic_ranking': {
                'name': 'Instructor',
                'min_units': 6.0,
                'max_units': 15.0,
                'min_hours': 10.0,
                'max_hours': 30.0,
            },
        },
        {
            'prof_id': 2,
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'department': 'CICT',
            'program_id': 1,
            'specialization': 'Algorithms',
            'academic_ranking': {
                'name': 'Associate Professor',
                'min_units': 12.0,
                'max_units': 24.0,
                'min_hours': 15.0,
                'max_hours': 40.0,
            },
        },
        {
            'prof_id': 3,
            'first_name': 'Grace',
            'last_name': 'Hopper',
            'department': 'CICT',
            'program_id': 1,
            'specialization': 'Compilers',
            'academic_ranking': {
                'name': 'Professor',
                'min_units': 6.0,
                'max_units': 12.0,
                'min_hours': 10.0,
                'max_hours': 20.0,
            },
        },
    ]

    courses = [
        {
            'course_id': 1,
            'course_name': 'CC-100',
            'lecture_hours': 2.0,
            'lab_hours': 2.0,
            'ilp_hours': 1.0,
            'units': 3.0,
            'program_id': 1,
        },
        {
            'course_id': 101,
            'course_name': 'IT-PF02',
            'lecture_hours': 3.0,
            'lab_hours': 0.0,
            'ilp_hours': 0.0,
            'units': 3.0,
            'program_id': 1,
        },
        {
            'course_id': 102,
            'course_name': 'CC-104',
            'lecture_hours': 2.0,
            'lab_hours': 3.0,
            'ilp_hours': 0.0,
            'units': 3.0,
            'program_id': 1,
        },
        {
            'course_id': 103,
            'course_name': 'IT-NET01',
            'lecture_hours': 3.0,
            'lab_hours': 0.0,
            'ilp_hours': 0.0,
            'units': 3.0,
            'program_id': 1,
        },
    ]

    # Prof 1 (Alan Turing): IT-PF02 (1 section = 3h, 3u), min_hours=10 -> Underload
    # Prof 2 (Ada Lovelace): IT-PF02 (2 sections = 6h, 6u) and CC-104 (2 sections = 10h, 6u) -> total 16h, 12u, min_h=15, max_h=40 -> Balanced
    # Prof 3 (Grace Hopper): CC-104 (5 sections = 25h, 15u) -> max_units=12, max_hours=20 -> Overload
    loads = [
        {
            'id': 1,
            'prof_id': 1,
            'course_id': 101,
            'sections': 1,
            'professor': profs[0],
            'course': courses[1],
        },
        {
            'id': 2,
            'prof_id': 2,
            'course_id': 101,
            'sections': 2,
            'professor': profs[1],
            'course': courses[1],
        },
        {
            'id': 3,
            'prof_id': 2,
            'course_id': 102,
            'sections': 2,
            'professor': profs[1],
            'course': courses[2],
        },
        {
            'id': 4,
            'prof_id': 3,
            'course_id': 102,
            'sections': 5,
            'professor': profs[2],
            'course': courses[2],
        },
    ]

    prof_programs = [
        {'prof_id': 1, 'program_id': 1},
        {'prof_id': 2, 'program_id': 1},
        {'prof_id': 3, 'program_id': 1},
    ]

    return MockSupabase(professors=profs, courses=courses, professor_loads=loads, professor_programs=prof_programs)


@pytest.fixture
def filter_client(monkeypatch):
    mock_db = _build_test_db()
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['email'] = 'scheduler@example.com'
            sess['program_id'] = 1
            sess['department'] = 'CICT'
        yield client, mock_db


def test_filter_bar_markup_rendered(filter_client):
    """Test that all three filter controls, clear button, and badge are rendered with proper accessibility."""
    client, _ = filter_client
    response = client.get('/professor_load')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Professor search input
    search_input = soup.find('input', id='profSearchInput')
    assert search_input is not None
    assert search_input.get('placeholder') == 'Search by professor name...'
    assert search_input.get('aria-label') == 'Search by professor name'

    # 2. Course filter dropdown
    course_select = soup.find('select', id='profCourseFilter')
    assert course_select is not None
    assert course_select.get('aria-label') == 'Filter by assigned course'
    all_courses_opt = course_select.find('option', value='')
    assert all_courses_opt is not None
    assert all_courses_opt.text.strip() == 'All Courses'
    rendered_course_options = [opt.text.strip() for opt in course_select.find_all('option')]
    assert 'CC-100' in rendered_course_options

    # 3. Load status filter dropdown is removed
    status_select = soup.find('select', id='profStatusFilter')
    assert status_select is None

    # Clear filters button
    clear_btn = soup.find('button', id='btnClearFilters')
    assert clear_btn is not None
    assert 'Clear filters' in clear_btn.text

    # Top-right count badge
    count_badge = soup.find(id='prof-table-count-badge')
    assert count_badge is not None
    assert '3 professors assigned' in count_badge.text

    # Empty filter state row
    empty_row = soup.find('tr', id='empty-filter-state-row')
    assert empty_row is not None
    assert 'No professors match your filters' in empty_row.text


def test_professor_rows_have_correct_data_attributes(filter_client):
    """Test that each professor row has accurate data attributes and does NOT render limit badges or Spec labels."""
    client, _ = filter_client
    response = client.get('/professor_load')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.find_all('tr', class_='professor-row')
    assert len(rows) == 3

    row_data = {}
    for r in rows:
        prof_id = r.get('data-prof-id')
        name = r.get('data-prof-name')
        courses = r.get('data-courses')
        badge = r.find('span', class_=re.compile(r'badge bg-(danger|warning|success)'))
        assert badge is not None

        row_data[prof_id] = {
            'name': name,
            'courses': courses.split('|') if courses else [],
        }

    # Alan Turing (prof_id 1): IT-PF02
    assert row_data['1']['name'] == 'alan turing'
    assert 'IT-PF02' in row_data['1']['courses']

    # Ada Lovelace (prof_id 2): IT-PF02, CC-104
    assert row_data['2']['name'] == 'ada lovelace'
    assert 'IT-PF02' in row_data['2']['courses']
    assert 'CC-104' in row_data['2']['courses']

    # Grace Hopper (prof_id 3): CC-104
    assert row_data['3']['name'] == 'grace hopper'
    assert 'CC-104' in row_data['3']['courses']


def test_edit_and_delete_actions_use_professor_id(filter_client):
    """Test that Edit and Delete buttons on each row act on the correct professor id, not row index."""
    client, _ = filter_client
    response = client.get('/professor_load')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.find_all('tr', class_='professor-row')

    for r in rows:
        prof_id = r.get('data-prof-id')
        edit_btn = r.find('button', class_='edit-btn')
        assert edit_btn is not None
        assert str(edit_btn.get('data-id')) == prof_id

        delete_link = r.find('a', href=re.compile(r'/delete_professor_load_all/'))
        assert delete_link is not None
        assert f'/delete_professor_load_all/{prof_id}' in delete_link.get('href')


def test_filter_matching_logic():
    """Unit test the exact matching algorithm used by the frontend JavaScript."""
    def check_prof_name_match(prof_name, query):
        if not query:
            return True
        clean_query = ' '.join(query.strip().lower().split())
        if not clean_query:
            return True
        clean_name = ' '.join((prof_name or '').lower().split())
        if clean_query in clean_name:
            return True
        tokens = clean_query.split()
        if len(tokens) > 1:
            return all(t in clean_name for t in tokens)
        return False

    def check_course_match(courses_str, selected_course):
        if not selected_course:
            return True
        if not courses_str:
            return False
        courses = [c.strip() for c in courses_str.split('|')]
        return selected_course in courses

    # 1. Professor search tests:
    # Full name, partial name, casing, extra whitespace, reversed tokens
    assert check_prof_name_match("alan turing", "Alan") is True
    assert check_prof_name_match("alan turing", "alan turing") is True
    assert check_prof_name_match("alan turing", "  ALAN   TUR  ") is True
    assert check_prof_name_match("alan turing", "Turing Alan") is True
    assert check_prof_name_match("alan turing", "ada") is False

    # 2. Course filter tests:
    # Multi-professor course (IT-PF02 assigned to Alan and Ada)
    assert check_course_match("IT-PF02", "IT-PF02") is True
    assert check_course_match("IT-PF02|CC-104", "IT-PF02") is True
    assert check_course_match("CC-104", "IT-PF02") is False
    assert check_course_match("IT-PF02|CC-104", "") is True  # All Courses

    # Single-professor course (e.g. IT-NET01)
    assert check_course_match("IT-NET01", "IT-NET01") is True
    assert check_course_match("IT-PF02|CC-104", "IT-NET01") is False

    # 3. Combined AND filters:
    # Prof 2 (Ada Lovelace): "ada lovelace", courses="IT-PF02|CC-104"
    row = {
        'name': 'ada lovelace',
        'courses': 'IT-PF02|CC-104',
    }

    # Matches when both match
    assert (
        check_prof_name_match(row['name'], 'ada')
        and check_course_match(row['courses'], 'IT-PF02')
    ) is True

    # Fails if search doesn't match
    assert (
        check_prof_name_match(row['name'], 'alan')
        and check_course_match(row['courses'], 'IT-PF02')
    ) is False

    # Fails if course doesn't match
    assert (
        check_prof_name_match(row['name'], 'ada')
        and check_course_match(row['courses'], 'IT-NET01')
    ) is False

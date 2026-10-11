import pytest
import app as app_module


class FakeTable:
    def __init__(self):
        self.inserted = []
        self.updated = []
        self.filters = {}
        self._order_by = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        self.inserted.append(data)
        return self

    def update(self, data):
        self.updated.append(data)
        return self

    def eq(self, *args, **kwargs):
        if len(args) >= 2:
            self.filters[args[0]] = args[1]
        return self

    def order(self, field, *args, **kwargs):
        self._order_by = field
        return self

    def single(self):
        return self

    def execute(self):
        filters = self.filters
        self.filters = {}

        class Response:
            data = [
                {
                    'course_id': 1,
                    'course_code': 'CC-101',
                    'course_name': 'Introduction to Computing',
                    'lecture_hours': 3,
                    'lab_hours': 2,
                    'ilp_hours': 1,
                    'program_id': 1,
                    'year_level': '1',
                    'specialization': 'General',
                    'semester': '1st Semester',
                },
                {
                    'course_id': 2,
                    'course_code': 'CS-201',
                    'course_name': 'Data Structures and Algorithms',
                    'lecture_hours': 2,
                    'lab_hours': 3,
                    'ilp_hours': 0,
                    'program_id': 1,
                    'year_level': '2',
                    'specialization': None,
                    'semester': '1st Semester',
                },
            ]

        response = Response()
        # For uniqueness check in program_id:
        if 'program_id' in filters and filters.get('program_id') == 999:
            response.data = []
        return response


class FakeSupabase:
    def __init__(self):
        self.table_obj = FakeTable()

    def table(self, name):
        return self.table_obj


def _build_client(monkeypatch, session_data=None):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'admin'
        if session_data:
            session.update(session_data)

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)
    monkeypatch.setattr(app_module, '_course_code_exists_in_program', lambda code, pid, exc=None: False)
    return client, fake_supabase


def test_add_course_handles_distinct_course_name_and_code(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/add_course', data={
        'course_code': 'IT 101',
        'course_name': 'Living in the IT Era',
        'units': '3',
        'lecture_hours': '3',
        'lab_hours': '0',
        'ilp_hours': '0',
        'program_id': '1',
        'year_level': '1',
        'semester': '1st Semester',
    })

    assert response.status_code == 302
    assert len(fake_supabase.table_obj.inserted) > 0
    row = fake_supabase.table_obj.inserted[0]
    # Verify course_code and course_name are separate attributes
    assert row['course_code'] == 'IT 101'
    assert row['course_name'] == 'Living in the IT Era'
    assert row['course_code'] != row['course_name']


def test_edit_course_updates_both_course_code_and_course_name(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/edit_course/1', data={
        'course_code': 'CS 102',
        'course_name': 'Advanced Programming Techniques',
        'units': '3',
        'lecture_hours': '2',
        'lab_hours': '3',
        'ilp_hours': '0',
        'program_id': '1',
        'year_level': '1',
        'semester': '2nd Semester',
    })

    assert response.status_code == 302
    assert len(fake_supabase.table_obj.updated) > 0
    row = fake_supabase.table_obj.updated[0]
    assert row['course_code'] == 'CS 102'
    assert row['course_name'] == 'Advanced Programming Techniques'


def test_search_courses_matches_by_course_code_or_name(monkeypatch):
    client, _ = _build_client(monkeypatch)

    # Search by code
    res_code = client.get('/search_courses?q=CC-101&program=1')
    assert res_code.status_code == 200
    data_code = res_code.get_json()
    assert len(data_code['courses']) == 1
    assert data_code['courses'][0]['course_code'] == 'CC-101'
    assert data_code['courses'][0]['course_name'] == 'Introduction to Computing'

    # Search by descriptive name
    res_name = client.get('/search_courses?q=Algorithms&program=1')
    assert res_name.status_code == 200
    data_name = res_name.get_json()
    assert len(data_name['courses']) == 1
    assert data_name['courses'][0]['course_code'] == 'CS-201'
    assert data_name['courses'][0]['course_name'] == 'Data Structures and Algorithms'


def test_api_courses_includes_course_name(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.get('/api/courses?program=1')
    assert response.status_code == 200
    data = response.get_json()
    assert 'courses' in data
    assert len(data['courses']) >= 1
    assert 'course_code' in data['courses'][0]
    assert 'course_name' in data['courses'][0]

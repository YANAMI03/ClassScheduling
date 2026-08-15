import app as app_module


class FakeCourseQuery:
    def __init__(self):
        self.filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        courses = [
            {
                'course_id': 1,
                'course_name': 'CC-100',
                'program': 'BSIT',
                'year_level': '1',
                'major': 'General',
                'semester': '1st Semester',
            },
            {
                'course_id': 2,
                'course_name': 'CC-101',
                'program': 'BSIT',
                'year_level': '1',
                'major': 'General',
                'semester': '1st Semester',
            },
        ]
        class Response:
            data = courses
        return Response()


class FakeSupabase:
    def __init__(self):
        self.query = FakeCourseQuery()

    def table(self, name):
        return self.query


def _build_client(monkeypatch, session_data=None):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        if session_data:
            session.update(session_data)

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)
    return client, fake_supabase


def test_api_courses_filters_by_year_level_and_semester(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.get('/api/courses?year_level=1&semester=1st%20Semester')

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload['courses']) == 2
    assert payload['courses'][0]['course_name'] == 'CC-100'
    assert fake_supabase.query.filters.get('year_level') == '1'
    assert fake_supabase.query.filters.get('semester') == '1st Semester'


def test_api_courses_year_level_only_remains_compatible(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.get('/api/courses?year_level=1')

    assert response.status_code == 200
    assert fake_supabase.query.filters.get('year_level') == '1'
    assert 'semester' not in fake_supabase.query.filters


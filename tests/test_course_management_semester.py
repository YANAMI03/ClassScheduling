import app as app_module


class FakeTable:
    def __init__(self):
        self.inserted = []
        self.updated = []

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        self.inserted.append(data)
        return self

    def update(self, data):
        self.updated.append(data)
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        class Response:
            data = [
                {'course_id': 1, 'course_name': 'CC-100', 'lecture_hours': 3, 'lab_hours': 2, 'ilp_hours': 1, 'program': 'BSIT', 'year_level': '1', 'major': 'General', 'semester': '1st Semester'},
            ]
        return Response()


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
        session['role'] = 'Scheduler'
        if session_data:
            session.update(session_data)

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)
    return client, fake_supabase


def test_add_course_inserts_semester(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/add_course', data={
        'course_name': 'CC-100',
        'lecture_hours': '3',
        'lab_hours': '2',
        'ilp_hours': '1',
        'program': 'BSIT',
        'year_level': '1',
        'major': 'General',
        'semester': '1st Semester',
    })

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/courses')
    assert len(fake_supabase.table_obj.inserted) > 0
    assert fake_supabase.table_obj.inserted[0]['semester'] == '1st Semester'


def test_edit_course_updates_semester(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/edit_course/7', data={
        'course_name': 'CC-100',
        'lecture_hours': '3',
        'lab_hours': '2',
        'ilp_hours': '1',
        'program': 'BSIT',
        'year_level': '1',
        'major': 'General',
        'semester': '2nd Semester',
    })

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/courses')
    assert len(fake_supabase.table_obj.updated) > 0
    assert fake_supabase.table_obj.updated[0]['semester'] == '2nd Semester'


def test_add_course_requires_semester(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.post('/add_course', data={
        'course_name': 'CC-100',
        'lecture_hours': '3',
        'lab_hours': '2',
        'ilp_hours': '1',
        'program': 'BSIT',
        'year_level': '1',
        'major': 'General',
    })

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session.get('course_message') == 'Semester is required.'


def test_courses_page_renders_semester_filter_and_row_metadata(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.get('/courses')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="semesterFilter"' in html
    assert 'value="1st Semester"' in html
    assert 'data-semester="1st Semester"' in html

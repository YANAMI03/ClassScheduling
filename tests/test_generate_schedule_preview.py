import app as app_module


class FakeResponse:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, table_name):
        self.table_name = table_name

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        if self.table_name == 'course':
            return FakeResponse([
                {'course_id': 1, 'course_name': 'IT101 - Intro to Computing', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                {'course_id': 2, 'course_name': 'IT201 - Data Structures', 'year_level': 2, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT'},
                {'course_id': 3, 'course_name': 'IT301 - Web Systems', 'year_level': 3, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT'},
                {'course_id': 4, 'course_name': 'IT401 - Capstone Project 1', 'year_level': 4, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
            ])
        elif self.table_name == 'prof_course':
            return FakeResponse([
                {'course_id': 1, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                {'course_id': 2, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                {'course_id': 3, 'prof_id': 3, 'professor': {'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40}},
                {'course_id': 4, 'prof_id': 4, 'professor': {'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40}},
            ])
        elif self.table_name == 'room':
            return FakeResponse([
                {'room_id': 1, 'room_name': 'Lab 101', 'room_type': 'Laboratory', 'department': 'CICT'},
                {'room_id': 2, 'room_name': 'Room 201', 'room_type': 'Lecture', 'department': 'CICT'},
            ])
        elif self.table_name == 'timeslot':
            return FakeResponse([
                {'timeslot_id': 1, 'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'},
            ])
        elif self.table_name == 'schedule':
            return FakeResponse([])
        elif self.table_name == 'program_department':
            return FakeResponse({'department_name': 'CICT'})
        return FakeResponse([])


class FakeSupabase:
    def table(self, table_name):
        return FakeQuery(table_name)


def test_generate_schedule_post_renders_inline_preview_all_years(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # Post only semester (no year_level required)
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'number_of_sections': '1',
    })

    assert response.status_code == 200
    assert b'Generated Schedule Preview' in response.data or b'Generate 4-Year Schedule' in response.data

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0
        
        # Verify courses across multiple year levels are present
        sections = {entry.get('section') for entry in preview}
        # e.g., 1A, 2A, 3A, 4A
        assert any(s.startswith('1') for s in sections)
        assert any(s.startswith('2') for s in sections)
        assert any(s.startswith('3') for s in sections)
        assert any(s.startswith('4') for s in sections)


def test_major_matches_accepts_canonical_aliases():
    assert app_module._major_matches('Database Systems', 'Database')
    assert app_module._major_matches('Web Development', 'Web')
    assert app_module._major_matches('General', 'General')


def test_generate_schedule_clears_previous_preview_before_regenerating(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['schedule_preview'] = [{'id': 1, 'section': 'OLD', 'course_name': 'Old Course'}]
        session['generated_sections'] = [{'section': 'OLD'}]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)
    monkeypatch.setattr(app_module, '_build_candidate_slots', lambda timeslots: (_ for _ in ()).throw(RuntimeError('boom')))

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
    })

    assert response.status_code == 500
    with client.session_transaction() as session:
        assert app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id')) == []
        assert session.get('generated_sections') == []


def test_generate_sections_distributes_majors_round_robin():
    majors = ['Web Development', 'Database Systems', 'Networking']
    sections = app_module._generate_sections(3, 90, 3, majors=majors)
    assert len(sections) == 3
    assert sections[0]['major'] == 'Web Development'
    assert sections[0]['section'] == '3A-WEB'
    assert sections[1]['major'] == 'Database Systems'
    assert sections[1]['section'] == '3B-DB'
    assert sections[2]['major'] == 'Networking'
    assert sections[2]['section'] == '3C-NET'


def test_generate_sections_with_sections_by_major():
    sections_by_major = {
        'Database Systems': 5,
        'Web Development': 4,
        'Networking': 4
    }
    sections = app_module._generate_sections(3, 390, sections_by_major=sections_by_major)
    assert len(sections) == 13

    db_sections = [s for s in sections if s['major'] == 'Database Systems']
    web_sections = [s for s in sections if s['major'] == 'Web Development']
    net_sections = [s for s in sections if s['major'] == 'Networking']

    assert len(db_sections) == 5
    assert len(web_sections) == 4
    assert len(net_sections) == 4

    assert [s['section'] for s in db_sections] == ['3A-DB', '3B-DB', '3C-DB', '3D-DB', '3E-DB']
    assert [s['section'] for s in web_sections] == ['3A-WEB', '3B-WEB', '3C-WEB', '3D-WEB']
    assert [s['section'] for s in net_sections] == ['3A-NET', '3B-NET', '3C-NET', '3D-NET']


def test_generate_schedule_second_semester_with_major_sections(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class FakeQuery2nd(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 10, 'course_name': 'IT102 - OOP', 'year_level': 1, 'semester': '2nd Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': None},
                    {'course_id': 20, 'course_name': 'IT202 - Algorithms', 'year_level': 2, 'semester': '2nd Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': None},
                    {'course_id': 30, 'course_name': 'IT302 - Advanced DB', 'year_level': 3, 'semester': '2nd Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Database Systems'},
                    {'course_id': 31, 'course_name': 'IT303 - Web Frameworks', 'year_level': 3, 'semester': '2nd Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Web Development'},
                    {'course_id': 32, 'course_name': 'IT304 - Network Admin', 'year_level': 3, 'semester': '2nd Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Networking'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    {'course_id': 10, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                    {'course_id': 20, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                    {'course_id': 30, 'prof_id': 3, 'professor': {'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40}},
                    {'course_id': 31, 'prof_id': 4, 'professor': {'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40}},
                    {'course_id': 32, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                ])
            return super().execute()

    class FakeSupabase2nd:
        def table(self, table_name):
            return FakeQuery2nd(table_name)

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase2nd())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '2nd Semester',
        'students[1]': '30',
        'sections[1]': '1',
        'students[2]': '30',
        'sections[2]': '1',
        'students[3]': '90',
        'sections_major[3][database]': '1',
        'sections_major[3][web]': '1',
        'sections_major[3][networking]': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0
        sections = {entry.get('section') for entry in preview}
        # Year 1 & 2 have standard section names
        assert any(s.startswith('1') for s in sections)
        assert any(s.startswith('2') for s in sections)
        # Year 3 sections have distinct major suffixes
        assert '3A-DB' in sections
        assert '3A-WEB' in sections
        assert '3A-NET' in sections
        # Verify entries exist across all three majors
        majors = {entry.get('major') for entry in preview}
        assert 'Database Systems' in majors
        assert 'Web Development' in majors
        assert 'Networking' in majors
        # 4th year is not generated for 2nd semester
        assert not any(s.startswith('4') for s in sections)




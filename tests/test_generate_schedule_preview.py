import app as app_module


class FakeResponse:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, table_name):
        self.table_name = table_name
        self.filters = {}

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

    def insert(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def like(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
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
            data = [
                {'prof_course_id': 101, 'course_id': 1, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}, 'course': {'course_name': 'IT101 - Intro to Computing', 'course_code': 'IT101'}},
                {'prof_course_id': 102, 'course_id': 2, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}, 'course': {'course_name': 'IT201 - Data Structures', 'course_code': 'IT201'}},
                {'prof_course_id': 103, 'course_id': 3, 'prof_id': 3, 'professor': {'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40}, 'course': {'course_name': 'IT301 - Web Systems', 'course_code': 'IT301'}},
                {'prof_course_id': 104, 'course_id': 4, 'prof_id': 4, 'professor': {'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40}, 'course': {'course_name': 'IT401 - Capstone Project 1', 'course_code': 'IT401'}},
            ]
            if 'prof_course_id' in self.filters:
                data = [d for d in data if str(d.get('prof_course_id')) == str(self.filters['prof_course_id'])]
            if 'course_id' in self.filters:
                data = [d for d in data if str(d.get('course_id')) == str(self.filters['course_id'])]
            if 'prof_id' in self.filters:
                data = [d for d in data if str(d.get('prof_id')) == str(self.filters['prof_id'])]
            return FakeResponse(data)
        elif self.table_name == 'professor':
            return FakeResponse([
                {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
                {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40, 'department': 'CICT'},
                {'prof_id': 3, 'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40, 'department': 'CICT'},
                {'prof_id': 4, 'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40, 'department': 'CICT'},
                {'prof_id': 5, 'first_name': 'Margaret', 'last_name': 'Hamilton', 'max_hours': 40, 'department': 'CICT'},
            ])
        elif self.table_name == 'room':
            data = [
                {'room_id': 1, 'room_name': 'Lab 101', 'room_type': 'Laboratory', 'department': 'CICT'},
                {'room_id': 2, 'room_name': 'Room 201', 'room_type': 'Lecture', 'department': 'CICT'},
                {'room_id': 3, 'room_name': 'Room 202', 'room_type': 'Lecture', 'department': 'CICT'},
            ]
            if 'room_id' in self.filters:
                data = [d for d in data if str(d.get('room_id')) == str(self.filters['room_id'])]
            return FakeResponse(data)
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

    def rpc(self, func_name, params=None):
        class FakeRpc:
            def execute(self):
                return FakeResponse({'success': True, 'deleted_count': 5, 'inserted_count': 10})
        return FakeRpc()



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


def test_generate_schedule_first_semester_with_4th_year_major_sections(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class FakeQuery1st(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 10, 'course_name': 'IT101 - Intro to Computing', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': None},
                    {'course_id': 20, 'course_name': 'IT201 - Data Structures', 'year_level': 2, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': None},
                    {'course_id': 30, 'course_name': 'IT301 - Systems Analysis', 'year_level': 3, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': None},
                    {'course_id': 40, 'course_name': 'IT401 - Advanced DB Project', 'year_level': 4, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Database Systems'},
                    {'course_id': 41, 'course_name': 'IT402 - Enterprise Web Apps', 'year_level': 4, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Web Development'},
                    {'course_id': 42, 'course_name': 'IT403 - Enterprise Networking', 'year_level': 4, 'semester': '1st Semester', 'lecture_hours': 2, 'lab_hours': 3, 'program': 'BSIT', 'major': 'Networking'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    {'course_id': 10, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                    {'course_id': 20, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                    {'course_id': 30, 'prof_id': 3, 'professor': {'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40}},
                    {'course_id': 40, 'prof_id': 4, 'professor': {'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40}},
                    {'course_id': 41, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                    {'course_id': 42, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                ])
            return super().execute()

    class FakeSupabase1st:
        def table(self, table_name):
            return FakeQuery1st(table_name)

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase1st())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'students[1]': '30',
        'sections[1]': '1',
        'students[2]': '30',
        'sections[2]': '1',
        'students[3]': '30',
        'sections[3]': '1',
        'students[4]': '90',
        'sections_major[4][database]': '1',
        'sections_major[4][web]': '1',
        'sections_major[4][networking]': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0
        sections = {entry.get('section') for entry in preview}
        # Year 1, 2, 3 have standard section names
        assert any(s.startswith('1') for s in sections)
        assert any(s.startswith('2') for s in sections)
        assert any(s.startswith('3') for s in sections)
        # Year 4 sections have distinct major suffixes
        assert '4A-DB' in sections
        assert '4A-WEB' in sections
        assert '4A-NET' in sections
        # Verify entries exist across all three majors
        majors = {entry.get('major') for entry in preview}
        assert 'Database Systems' in majors
        assert 'Web Development' in majors
        assert 'Networking' in majors


def test_group_preview_sections_collapsible_blocks():
    sections_with_entries = [
        {'section': {'section_name': '1A', 'major': None}, 'entries': []},
        {'section': {'section_name': '1B', 'major': None}, 'entries': []},
        {'section': {'section_name': '2A', 'major': None}, 'entries': []},
        {'section': {'section_name': '3A', 'major': None}, 'entries': []},
        {'section': {'section_name': '4A WST', 'major': 'WST'}, 'entries': []},
        {'section': {'section_name': '4B WST', 'major': 'Web Development'}, 'entries': []},
        {'section': {'section_name': '4A DST', 'major': 'DST'}, 'entries': []},
        {'section': {'section_name': '4A NST', 'major': 'NST'}, 'entries': []},
    ]

    groups = app_module._group_preview_sections(sections_with_entries)
    
    assert len(groups) == 6
    group_dict = {g['id']: g for g in groups}

    assert len(group_dict['1st-year']['sections']) == 2
    assert len(group_dict['2nd-year']['sections']) == 1
    assert len(group_dict['3rd-year']['sections']) == 1
    assert len(group_dict['4th-year-wst']['sections']) == 2
    assert len(group_dict['4th-year-dst']['sections']) == 1
    assert len(group_dict['4th-year-nst']['sections']) == 1

    assert group_dict['1st-year']['title'] == '1st Year Schedules'
    assert group_dict['2nd-year']['title'] == '2nd Year Schedules'
    assert group_dict['3rd-year']['title'] == '3rd Year Schedules'
    assert group_dict['4th-year-wst']['title'] == '4th Year Schedules — WST'
    assert group_dict['4th-year-dst']['title'] == '4th Year Schedules — DST'
    assert group_dict['4th-year-nst']['title'] == '4th Year Schedules — NST'


def test_time_formatting_preserves_pm_and_am():
    assert app_module._to_time_string('01:00 PM') == '13:00:00'
    assert app_module._to_time_string('03:00 PM') == '15:00:00'
    assert app_module._to_time_string('08:00 AM') == '08:00:00'
    assert app_module._to_time_string('12:00 PM') == '12:00:00'
    assert app_module._to_time_string('12:00 AM') == '00:00:00'

    assert app_module._format_time('13:00:00') == '01:00 PM'
    assert app_module._format_time('15:00:00') == '03:00 PM'
    assert app_module._format_time('08:00:00') == '08:00 AM'
    assert app_module._format_time('12:00:00') == '12:00 PM'
    assert app_module._format_time('00:00:00') == '12:00 AM'


def test_group_preview_sections_filter_by_year_and_major():
    sections_with_entries = [
        {'section': {'section_name': '1A', 'major': None}, 'entries': []},
        {'section': {'section_name': '2A', 'major': None}, 'entries': []},
        {'section': {'section_name': '3A', 'major': None}, 'entries': []},
        {'section': {'section_name': '4A WST', 'major': 'Web Development'}, 'entries': []},
        {'section': {'section_name': '4A DST', 'major': 'Database Systems'}, 'entries': []},
        {'section': {'section_name': '4A NST', 'major': 'Network Systems'}, 'entries': []},
    ]

    g_yr1 = app_module._group_preview_sections(sections_with_entries, year_filter='1')
    assert len(g_yr1) == 1
    assert g_yr1[0]['id'] == '1st-year'

    g_yr4 = app_module._group_preview_sections(sections_with_entries, year_filter='4')
    assert len(g_yr4) == 3
    assert {g['id'] for g in g_yr4} == {'4th-year-wst', '4th-year-dst', '4th-year-nst'}

    g_dst = app_module._group_preview_sections(sections_with_entries, year_filter='4', major_filter='Database Systems')
    assert len(g_dst) == 1
    assert g_dst[0]['id'] == '4th-year-dst'


def test_group_preview_sections_excludes_empty_blocks():
    sections_with_entries = [
        {'section': {'section_name': '1A', 'major': None}, 'entries': []},
    ]

    groups = app_module._group_preview_sections(sections_with_entries)
    assert len(groups) == 1
    assert groups[0]['id'] == '1st-year'
    assert groups[0]['title'] == '1st Year Schedules'


def test_confirm_preview_saves_and_clears_preview(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_123'
        session['schedule_preview'] = [
            {
                'course_id': 1,
                'course_name': 'IT101',
                'section': '1A',
                'prof_id': 1,
                'room_id': 2,
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '9:00 AM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    fake_sb = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/confirm_preview', follow_redirects=False)
    assert response.status_code == 302
    assert '/schedules' in response.headers.get('Location', '')

    with client.session_transaction() as session:
        assert session.get('schedule_preview') in ([], None)
        assert len(session.get('generated_sections', [])) == 1
        assert session['generated_sections'][0]['section'] == '1A'


def test_confirm_preview_fallback_deletes_scope_and_inserts(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_456'
        session['schedule_preview'] = [
            {
                'course_id': 10,
                'course_name': 'IT401',
                'section': '4A-DB',
                'prof_id': 2,
                'room_id': 2,
                'day': 'Tuesday',
                'start': '10:00 AM',
                'end': '12:00 PM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'major': 'Database Systems',
                'program': 'BSIT',
            }
        ]

    class FakeSupabaseNoRpc:
        def table(self, table_name):
            return FakeQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseNoRpc())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/confirm_preview', follow_redirects=False)
    assert response.status_code == 302
    assert '/schedules' in response.headers.get('Location', '')

    with client.session_transaction() as session:
        assert session.get('schedule_preview') in ([], None)
        assert len(session.get('generated_sections', [])) == 1
        assert session['generated_sections'][0]['section'] == '4A-DB'
        assert session['generated_sections'][0]['major'] == 'Database Systems'


def test_group_preview_sections_third_year_majors():
    sample_sections = [
        {
            'section': {'section': '1A', 'section_name': '1A', 'major': None},
            'entries': [{'section': '1A', 'course_name': 'IT101', 'year_level': '1'}]
        },
        {
            'section': {'section': '2A', 'section_name': '2A', 'major': None},
            'entries': [{'section': '2A', 'course_name': 'IT201', 'year_level': '2'}]
        },
        {
            'section': {'section': '3A-WEB', 'section_name': '3A-WEB', 'major': 'Web Development'},
            'entries': [{'section': '3A-WEB', 'course_name': 'IT301-WEB', 'year_level': '3', 'major': 'Web Development'}]
        },
        {
            'section': {'section': '3A-DB', 'section_name': '3A-DB', 'major': 'Database Systems'},
            'entries': [{'section': '3A-DB', 'course_name': 'IT302-DB', 'year_level': '3', 'major': 'Database Systems'}]
        },
        {
            'section': {'section': '3A-NET', 'section_name': '3A-NET', 'major': 'Networking'},
            'entries': [{'section': '3A-NET', 'course_name': 'IT303-NET', 'year_level': '3', 'major': 'Networking'}]
        }
    ]

    groups = app_module._group_preview_sections(sample_sections)
    group_titles = [g['title'] for g in groups]

    assert '1st Year Schedules' in group_titles
    assert '2nd Year Schedules' in group_titles
    assert any('3rd Year Schedules' in t and 'WST' in t for t in group_titles)
    assert any('3rd Year Schedules' in t and 'DST' in t for t in group_titles)
    assert any('3rd Year Schedules' in t and 'NST' in t for t in group_titles)


def test_generate_schedule_zero_tba_and_professor_fallback(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'number_of_sections': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0
        # Verify no unnecessary TBA
        for entry in preview:
            assert entry.get('prof_id') is not None, f"Entry {entry} has TBA professor"
            assert entry.get('room_id') is not None, f"Entry {entry} has TBA room"
            assert entry.get('professor_name') not in (None, '', 'TBA')
            assert entry.get('room_name') not in (None, '', 'TBA')


def test_generate_schedule_ignores_other_semester_schedule_bookings(monkeypatch):
    """Test that existing bookings from a different semester do not block room/prof allocation."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class FakeSupabaseWithOtherSemBookings(FakeSupabase):
        def table(self, table_name):
            if table_name == 'schedule':
                class FakeSchedQuery(FakeQuery):
                    def execute(self):
                        return FakeResponse([
                            {
                                'section': '1A', 'room_id': 1, 'day': 'Monday',
                                'class_start': '08:00:00', 'class_end': '12:00:00',
                                'prof_id': 1, 'semester': '2nd Semester', 'program': 'BSIT'
                            }
                        ])
                return FakeSchedQuery(table_name)
            return FakeQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseWithOtherSemBookings())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'number_of_sections': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0
        for entry in preview:
            assert entry.get('prof_id') is not None
            assert entry.get('room_id') is not None


def test_professor_and_room_schedule_non_military_time(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin_tester'
        session['role'] = 'admin'

    class FakeSupabaseForDetail(FakeSupabase):
        def table(self, table_name):
            if table_name == 'schedule':
                class FakeSchedQuery(FakeQuery):
                    def execute(self):
                        return FakeResponse([
                            {
                                'schedule_id': 101,
                                'course_id': 1,
                                'prof_id': 1,
                                'room_id': 1,
                                'day': 'Monday',
                                'class_start': '14:30:00',
                                'class_end': '17:30:00',
                                'section': '1A',
                                'semester': '1st Semester',
                                'major': None,
                                'session_type': 'Lecture',
                                'course': {'course_name': 'IT101'},
                                'room': {'room_name': 'Room 201'},
                                'professor': {'first_name': 'Alan', 'last_name': 'Turing'}
                            }
                        ])
                return FakeSchedQuery(table_name)
            return FakeQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseForDetail())

    # Check Professor Schedule View
    prof_resp = client.get('/professor_schedule/1')
    assert prof_resp.status_code == 200
    assert b'02:30 PM - 05:30 PM' in prof_resp.data
    assert b'14:30:00' not in prof_resp.data

    # Check Room Schedule View
    room_resp = client.get('/room_schedule/1')
    assert room_resp.status_code == 200
    assert b'02:30 PM - 05:30 PM' in room_resp.data
    assert b'14:30:00' not in room_resp.data


def test_generate_schedule_strict_room_type_enforcement(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'number_of_sections': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0

        rooms_res = FakeSupabase().table('room').execute().data
        rooms_by_id = {r['room_id']: r for r in rooms_res}

        for entry in preview:
            rid = entry.get('room_id')
            if rid:
                room = rooms_by_id.get(rid)
                stype = entry.get('session_type')
                assert app_module._room_matches_session(room, stype), (
                    f"Room mismatch: {entry.get('course_name')} ({stype}) assigned to {room.get('room_name')} ({room.get('room_type')})"
                )


def test_edit_preview_entry_blocks_cross_type_room_assignment(monkeypatch):
    client = app_module.app.test_client()

    preview_entry = {
        'id': 1,
        'course_id': 1,
        'course_name': 'IT101',
        'section': '1A',
        'prof_id': 1,
        'room_id': 2,
        'day': 'Monday',
        'start': '8:00 AM',
        'end': '11:00 AM',
        'session_type': 'Lecture',
        'semester': '1st Semester',
        'program': 'BSIT'
    }

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'test_prev'
        session['schedule_preview'] = [preview_entry]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Attempt to assign room_id=1 (Lab 101 - Laboratory) to Lecture class
    resp = client.post('/edit_preview_entry', json={
        'id': 1,
        'prof_course_id': 101,
        'section': '1A',
        'room_id': 1, # Lab 101
        'day': 'Monday',
        'start': '8:00 AM',
        'end': '11:00 AM'
    })

    assert resp.status_code == 400
    json_data = resp.get_json()
    assert 'Room type mismatch' in json_data.get('error', '')


def test_confirm_preview_blocks_on_room_type_mismatch(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_invalid'
        session['schedule_preview'] = [
            {
                'course_id': 1,
                'course_name': 'IT101',
                'section': '1A',
                'prof_id': 1,
                'room_id': 1, # Lab 101 assigned to Lecture
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '11:00 AM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    resp = client.post('/confirm_preview', follow_redirects=False)
    assert resp.status_code == 302
    assert '/generate_schedule' in resp.headers.get('Location', '')


def test_generate_schedule_sets_prof_course_id(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'number_of_sections': '1',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert preview is not None
        assert len(preview) > 0
        # Every generated session with an assigned professor should have prof_course_id populated
        for entry in preview:
            if entry.get('prof_id'):
                assert entry.get('prof_course_id') is not None


def test_edit_preview_entry_with_prof_course_id(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_edit_test'
        session['schedule_preview'] = [
            {
                'id': 1,
                'prof_course_id': 101,
                'course_id': 1,
                'course_name': 'IT101 - Intro to Computing',
                'section': '1A',
                'prof_id': 1,
                'professor_name': 'Alan Turing',
                'room_id': 2,
                'room_name': 'Room 201',
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '11:00 AM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Edit entry to use prof_course_id 102
    resp = client.post('/edit_preview_entry', json={
        'id': 1,
        'prof_course_id': 102,
        'section': '1A',
        'room_id': 2,
        'day': 'Monday',
        'start': '8:00 AM',
        'end': '11:00 AM'
    })
    assert resp.status_code == 200
    json_data = resp.get_json()
    assert json_data.get('success') is True
    assert json_data.get('entry') is not None
    assert json_data['entry']['prof_course_id'] == 102
    assert json_data['entry']['course_name'] == 'IT201 - Data Structures'
    assert json_data['entry']['professor_name'] == 'Grace Hopper'

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        target = preview[0]
        assert target['prof_course_id'] == 102
        assert target['course_name'] == 'IT201 - Data Structures'
        assert target['professor_name'] == 'Grace Hopper'


def test_edit_preview_entry_rejects_missing_prof_course_id(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_no_pcid'
        session['schedule_preview'] = [{
            'id': 1,
            'prof_course_id': 101,
            'section': '1A',
            'day': 'Monday',
            'start': '8:00 AM',
            'end': '11:00 AM',
            'session_type': 'Lecture'
        }]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())

    resp = client.post('/edit_preview_entry', json={
        'id': 1,
        'section': '1A',
        'room_id': 2,
        'day': 'Monday',
        'start': '8:00 AM',
        'end': '11:00 AM'
    })
    assert resp.status_code == 400
    assert 'Professor & Course selection is required' in resp.get_json().get('error', '')


def test_edit_preview_entry_detects_conflicts(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_conflicts'
        session['schedule_preview'] = [
            {
                'id': 1,
                'prof_course_id': 101,
                'prof_id': 1,
                'section': '1A',
                'room_id': 2,
                'room_name': 'Room 201',
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '10:00 AM',
                'session_type': 'Lecture'
            },
            {
                'id': 2,
                'prof_course_id': 102,
                'prof_id': 2,
                'section': '1B',
                'room_id': 3,
                'room_name': 'Room 202',
                'day': 'Monday',
                'start': '9:00 AM',
                'end': '11:00 AM',
                'session_type': 'Lecture'
            }
        ]

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # 1. Section collision: changing entry 2's section to '1A' creates collision with entry 1
    resp_sec = client.post('/edit_preview_entry', json={
        'id': 2,
        'prof_course_id': 102,
        'section': '1A',
        'room_id': 3,
        'day': 'Monday',
        'start': '9:00 AM',
        'end': '11:00 AM'
    })
    assert resp_sec.status_code == 400
    assert 'Section conflict' in resp_sec.get_json().get('error', '')

    # 2. Room collision: changing entry 2's room to room 2 (Room 201) overlaps with entry 1
    resp_room = client.post('/edit_preview_entry', json={
        'id': 2,
        'prof_course_id': 102,
        'section': '1B',
        'room_id': 2,
        'day': 'Monday',
        'start': '9:00 AM',
        'end': '11:00 AM'
    })
    assert resp_room.status_code == 400
    assert 'Room conflict' in resp_room.get_json().get('error', '')

    # 3. Professor collision: changing entry 2's prof_course_id to 101 (Alan Turing, prof_id=1) overlaps with entry 1
    resp_prof = client.post('/edit_preview_entry', json={
        'id': 2,
        'prof_course_id': 101,
        'section': '1B',
        'room_id': 3,
        'day': 'Monday',
        'start': '9:00 AM',
        'end': '11:00 AM'
    })
    assert resp_prof.status_code == 400
    assert 'Professor conflict' in resp_prof.get_json().get('error', '')


def test_api_preview_entries_endpoint(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_api_test'
        session['schedule_preview'] = [
            {'id': 1, 'prof_course_id': 101, 'section': '1A'}
        ]

    resp = client.get('/api/preview_entries')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True
    assert len(data.get('entries', [])) == 1
    assert data['entries'][0]['prof_course_id'] == 101



def test_confirm_preview_passes_prof_course_id_to_rpc(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_rpc_check'
        session['schedule_preview'] = [
            {
                'prof_course_id': 101,
                'course_id': 1,
                'course_name': 'IT101',
                'section': '1A',
                'prof_id': 1,
                'room_id': 2,
                'day': 'Monday',
                'start': '8:00 AM',
                'end': '11:00 AM',
                'session_type': 'Lecture',
                'semester': '1st Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    captured_rpc = {}

    class TrackingSupabase(FakeSupabase):
        def rpc(self, func_name, params=None):
            captured_rpc['func'] = func_name
            captured_rpc['params'] = params
            class FakeRpcExec:
                def execute(self):
                    return FakeResponse({'success': True, 'inserted_count': 1})
            return FakeRpcExec()

    monkeypatch.setattr(app_module, 'supabase', TrackingSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/confirm_preview', follow_redirects=False)
    assert resp.status_code == 302
    assert captured_rpc.get('func') == 'confirm_schedule_transaction'
    rows = captured_rpc.get('params', {}).get('p_rows', [])
    assert len(rows) == 1
    assert rows[0].get('prof_course_id') == 101
    assert 'course_id' not in rows[0]
    assert 'prof_id' not in rows[0]


def test_edit_schedule_entry_with_prof_course_id(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    updated_data = {}

    class EditScheduleSupabase(FakeSupabase):
        def table(self, table_name):
            parent = self
            class LocalQuery(FakeQuery):
                def __init__(self, t_name):
                    super().__init__(t_name)
                def execute(self):
                    if self.table_name == 'schedule':
                        return FakeResponse([{
                            'schedule_id': 10,
                            'prof_course_id': 101,
                            'room_id': 1,
                            'day': 'Monday',
                            'class_start': '08:00:00',
                            'class_end': '11:00:00',
                            'session_type': 'Lecture',
                            'section': '1A',
                            'semester': '1st Semester',
                            'major': None,
                            'program': 'BSIT',
                            'prof_course': {
                                'prof_course_id': 101,
                                'prof_id': 1,
                                'course_id': 1,
                                'course': {'course_name': 'IT101 - Intro to Computing'},
                                'professor': {'first_name': 'Alan', 'last_name': 'Turing'}
                            }
                        }])
                    return super().execute()
                def update(self, payload):
                    updated_data.update(payload)
                    return self
            return LocalQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', EditScheduleSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/edit_schedule_entry/10', json={
        'prof_course_id': 102,
        'room_id': 2,
        'timeslot': 'Tuesday | 09:00 AM - 12:00 PM',
        'session_type': 'Laboratory'
    })

    assert resp.status_code == 200
    res_json = resp.get_json()
    assert res_json.get('success') is True
    assert updated_data.get('prof_course_id') == 102
    assert updated_data.get('room_id') == 2
    assert updated_data.get('day') == 'Tuesday'
    assert updated_data.get('session_type') == 'Laboratory'
    # Ensure obsolete columns are never written
    assert 'course_id' not in updated_data
    assert 'prof_id' not in updated_data


def test_view_schedule_renders_without_foreign_key_errors(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class ViewScheduleSupabase(FakeSupabase):
        def table(self, table_name):
            class LocalQuery(FakeQuery):
                def execute(self):
                    if self.table_name == 'schedule':
                        return FakeResponse([{
                            'schedule_id': 10,
                            'prof_course_id': 101,
                            'room_id': 1,
                            'day': 'Monday',
                            'class_start': '08:00:00',
                            'class_end': '11:00:00',
                            'session_type': 'Lecture',
                            'section': '1A',
                            'semester': '1st Semester',
                            'major': None,
                            'program': 'BSIT',
                            'prof_course': {
                                'prof_course_id': 101,
                                'prof_id': 1,
                                'course_id': 1,
                                'course': {'course_id': 1, 'course_name': 'IT101 - Intro to Computing'},
                                'professor': {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing'}
                            },
                            'room': {'room_name': 'Lab 101'}
                        }])
                    return super().execute()
            return LocalQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', ViewScheduleSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    resp = client.get('/schedule/1A')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'IT101 - Intro to Computing' in html
    assert 'Alan Turing' in html


def test_edit_schedule_section_updates_metadata(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    updated_rows = {}

    class EditSectionSupabase(FakeSupabase):
        def table(self, table_name):
            class LocalQuery(FakeQuery):
                def update(self, payload):
                    updated_rows.update(payload)
                    return self
            return LocalQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', EditSectionSupabase())
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.post('/edit_schedule/1A', json={
        'section': '2B',
        'year_level': '2',
        'semester': '2nd Semester',
        'major': 'Network and Cybersecurity'
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True
    assert data.get('section_name') == '2B'
    assert updated_rows.get('section') == '2B'
    assert updated_rows.get('semester') == '2nd Semester'
    assert updated_rows.get('major') == 'Network and Cybersecurity'


def test_calculate_professor_workload_intervals_merges_overlaps():
    """Test that overlapping intervals on the same day are merged and not double-counted."""
    entries = [
        # Monday: 08:00 AM - 10:00 AM (2h) and 09:00 AM - 11:00 AM (2h, overlapping 09-10). Merged: 08:00 - 11:00 = 3h
        {'day': 'Monday', 'start': '08:00 AM', 'end': '10:00 AM'},
        {'day': 'Monday', 'start': '09:00 AM', 'end': '11:00 AM'},
        # Tuesday: 01:00 PM - 04:00 PM = 3h
        {'day': 'Tuesday', 'start': '01:00 PM', 'end': '04:00 PM'},
        # Invalid / TBA entries should be skipped
        {'day': 'Wednesday', 'start': 'TBA', 'end': 'TBA'},
        {'day': 'TBA', 'start': '08:00 AM', 'end': '10:00 AM'},
        {'day': 'Thursday', 'start': '10:00 AM', 'end': '09:00 AM'},  # end <= start
    ]

    result = app_module._calculate_professor_workload(entries, max_hours=40)
    # Total = 3h (Monday) + 3h (Tuesday) = 6h
    assert result['total_scheduled_hours'] == 6.0
    assert result['total_scheduled_hours_display'] == 6
    assert result['remaining_hours'] == 34.0
    assert result['remaining_hours_display'] == 34
    assert result['max_hours'] == 40.0
    assert result['max_hours_display'] == 40
    assert result['is_overloaded'] is False
    assert result['overload_hours'] == 0.0
    assert result['workload_percentage'] == 15.0
    assert result['assigned_hours_text'] == "6 hours assigned"
    assert result['remaining_hours_text'] == "34 hours remaining"
    assert result['valid_classes_count'] == 3

    # Check that individual entries are annotated with duration
    assert entries[0]['duration_hours'] == 2.0
    assert entries[0]['duration_text'] == "2 hrs"
    assert entries[1]['duration_hours'] == 2.0
    assert entries[2]['duration_hours'] == 3.0
    assert entries[2]['duration_text'] == "3 hrs"
    assert entries[3]['duration_text'] == "TBA"


def test_calculate_professor_workload_overload_cap():
    """Test that workload exceeding professor cap triggers overload flags and calculations."""
    entries = [
        # Monday to Friday: 9 hours each day = 45 hours total
        {'day': 'Monday', 'start': '08:00 AM', 'end': '05:00 PM'},
        {'day': 'Tuesday', 'start': '08:00 AM', 'end': '05:00 PM'},
        {'day': 'Wednesday', 'start': '08:00 AM', 'end': '05:00 PM'},
        {'day': 'Thursday', 'start': '08:00 AM', 'end': '05:00 PM'},
        {'day': 'Friday', 'start': '08:00 AM', 'end': '05:00 PM'},
    ]

    result = app_module._calculate_professor_workload(entries, max_hours=40)
    assert result['total_scheduled_hours'] == 45.0
    assert result['remaining_hours'] == 0.0
    assert result['is_overloaded'] is True
    assert result['overload_hours'] == 5.0
    assert result['workload_percentage'] == 112.5
    assert "45 hours assigned" in result['assigned_hours_text']
    assert "5 hours" in result['remaining_hours_text']


def test_view_professor_schedule_calculates_workload(monkeypatch):
    """Test /professor_schedule/<id> loads confirmed schedule and calculates workload."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_tester'
        session['role'] = 'admin'
        session['program'] = 'BSIT'

    class ConfirmedSchedSupabase(FakeSupabase):
        def table(self, table_name):
            if table_name == 'schedule':
                class SchedQuery(FakeQuery):
                    def execute(self):
                        return FakeResponse([
                            {
                                'schedule_id': 1, 'prof_course_id': 101, 'room_id': 1,
                                'day': 'Monday', 'class_start': '08:00:00', 'class_end': '11:00:00',
                                'section': '1A', 'semester': '1st Semester', 'major': 'General',
                                'session_type': 'Lecture', 'program': 'BSIT',
                                'prof_course': {'prof_course_id': 101, 'prof_id': 1, 'course_id': 1, 'course': {'course_id': 1, 'course_name': 'IT101'}},
                                'room': {'room_name': 'Room 101'}
                            }
                        ])
                return SchedQuery(table_name)
            return FakeQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', ConfirmedSchedSupabase())

    resp = client.get('/professor_schedule/1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Scheduled Workload" in html
    assert "3 hours assigned" in html
    assert "37 hours remaining" in html
    assert "Weekly Workload Cap" in html
    assert "Duration" in html
    assert "3 hrs" in html


def test_view_professor_schedule_preview_mode(monkeypatch):
    """Test /professor_schedule/<id>?mode=preview dynamically computes workload from preview session entries."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_tester'
        session['role'] = 'admin'
        session['program'] = 'BSIT'

    preview_data = [
        {
            'id': 1,
            'prof_course_id': 101,
            'prof_id': 1,
            'professor_name': 'Alan Turing',
            'course_id': 1,
            'course_name': 'IT101',
            'section': '1A',
            'room_id': 1,
            'room_name': 'Room 101',
            'day': 'Monday',
            'start': '08:00 AM',
            'end': '12:00 PM',  # 4 hours
            'time_range': 'Monday | 08:00 AM - 12:00 PM',
            'session_type': 'Lecture',
            'semester': '1st Semester',
            'major': 'General',
            'program': 'BSIT',
        },
        {
            'id': 2,
            'prof_course_id': 101,
            'prof_id': 1,
            'professor_name': 'Alan Turing',
            'course_id': 1,
            'course_name': 'IT101',
            'section': '1B',
            'room_id': 2,
            'room_name': 'Room 201',
            'day': 'Tuesday',
            'start': '01:00 PM',
            'end': '03:00 PM',  # 2 hours
            'time_range': 'Tuesday | 01:00 PM - 03:00 PM',
            'session_type': 'Lecture',
            'semester': '1st Semester',
            'major': 'General',
            'program': 'BSIT',
        }
    ]

    monkeypatch.setattr(app_module, '_get_preview_for_user', lambda *args, **kwargs: preview_data)
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())

    resp = client.get('/professor_schedule/1?mode=preview')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Preview Mode Active" in html
    assert "6 hours assigned" in html
    assert "34 hours remaining" in html


def test_api_professor_workload_endpoint(monkeypatch):
    """Test /api/professor_workload/<id> returns JSON workload metrics."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_tester'
        session['role'] = 'admin'
        session['program'] = 'BSIT'

    preview_data = [
        {
            'id': 1,
            'prof_course_id': 101,
            'prof_id': 1,
            'day': 'Monday',
            'start': '08:00 AM',
            'end': '12:00 PM',
        }
    ]
    monkeypatch.setattr(app_module, '_get_preview_for_user', lambda *args, **kwargs: preview_data)
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())

    # Test preview mode
    resp = client.get('/api/professor_workload/1?mode=preview')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['professor_id'] == 1
    assert data['total_scheduled_hours'] == 4.0
    assert data['remaining_hours'] == 36.0
    assert data['max_hours'] == 40.0
    assert data['is_overloaded'] is False
    assert data['is_preview'] is True


def test_professor_schedule_list_displays_hours(monkeypatch):
    """Test /professor_schedule list page calculates and renders hours on professor cards."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_tester'
        session['role'] = 'admin'
        session['program'] = 'BSIT'

    class SchedListSupabase(FakeSupabase):
        def table(self, table_name):
            if table_name == 'schedule':
                class SchedQuery(FakeQuery):
                    def execute(self):
                        return FakeResponse([
                            {
                                'schedule_id': 1, 'prof_course_id': 101, 'room_id': 1,
                                'day': 'Monday', 'class_start': '08:00:00', 'class_end': '12:00:00',
                                'section': '1A', 'semester': '1st Semester', 'major': 'General',
                                'session_type': 'Lecture', 'program': 'BSIT',
                                'prof_course': {'prof_id': 1}
                            }
                        ])
                return SchedQuery(table_name)
            return FakeQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', SchedListSupabase())

    resp = client.get('/professor_schedule')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "4 hrs" in html
    assert "40 hrs assigned" in html
    assert "36 hrs remaining" in html


def test_generate_schedule_workload_balanced_across_faculty(monkeypatch):
    """Verify teaching loads are distributed evenly across qualified faculty within tolerance."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class BalancedQuery(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 10, 'course_name': 'IT101 - Intro to Computing', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    {'prof_course_id': 201, 'course_id': 10, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                    {'prof_course_id': 202, 'course_id': 10, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                ])
            elif self.table_name == 'professor':
                return FakeResponse([
                    {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
                    {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40, 'department': 'CICT'},
                ])
            elif self.table_name == 'room':
                return FakeResponse([
                    {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 104, 'room_name': 'Room 104', 'room_type': 'Lecture', 'department': 'CICT'},
                ])
            return super().execute()

    class BalancedSupabase(FakeSupabase):
        def table(self, table_name):
            return BalancedQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', BalancedSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # 4 sections generated for Year 1
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '4',
        'students[1]': '120',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) == 4

        prof_hours = {}
        for entry in preview:
            pid = entry.get('prof_id')
            prof_hours[pid] = prof_hours.get(pid, 0) + 3

        # Both professors should each teach 2 sections (6 hours each)
        assert prof_hours.get(1) == 6
        assert prof_hours.get(2) == 6
        assert abs(prof_hours.get(1) - prof_hours.get(2)) <= 2


def test_generate_schedule_rotates_course_sections(monkeypatch):
    """Verify system alternates professors across sections of the same course rather than assigning the same professor repeatedly."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class RotationQuery(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 20, 'course_name': 'IT102 - Computer Programming', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    {'prof_course_id': 211, 'course_id': 20, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                    {'prof_course_id': 212, 'course_id': 20, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                ])
            elif self.table_name == 'professor':
                return FakeResponse([
                    {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
                    {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40, 'department': 'CICT'},
                ])
            elif self.table_name == 'room':
                return FakeResponse([
                    {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
                ])
            return super().execute()

    class RotationSupabase(FakeSupabase):
        def table(self, table_name):
            return RotationQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', RotationSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '2',
        'students[1]': '60',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) == 2
        assigned_prof_ids = {entry.get('prof_id') for entry in preview}
        # Both Prof 1 and Prof 2 must be utilized (rotation across the 2 sections)
        assert assigned_prof_ids == {1, 2}


def test_generate_schedule_respects_max_hours_cap(monkeypatch):
    """Verify professors are not assigned beyond their configured max weekly hours when others have capacity."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class CapQuery(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 30, 'course_name': 'IT103 - Discrete Structures', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    # Prof 1 has max_hours = 3 (can only take 1 section)
                    {'prof_course_id': 221, 'course_id': 30, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 3}},
                    # Prof 2 has max_hours = 40 (can take more)
                    {'prof_course_id': 222, 'course_id': 30, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40}},
                ])
            elif self.table_name == 'professor':
                return FakeResponse([
                    {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 3, 'department': 'CICT'},
                    {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40, 'department': 'CICT'},
                ])
            elif self.table_name == 'room':
                return FakeResponse([
                    {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture', 'department': 'CICT'},
                ])
            return super().execute()

    class CapSupabase(FakeSupabase):
        def table(self, table_name):
            return CapQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', CapSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # 3 sections (9 hours total)
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '3',
        'students[1]': '90',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) == 3
        prof1_entries = [e for e in preview if e.get('prof_id') == 1]
        prof2_entries = [e for e in preview if e.get('prof_id') == 2]

        # Prof 1 strictly does not exceed max_hours (3h = 1 section)
        assert len(prof1_entries) == 1
        # Prof 2 takes the other 2 sections
        assert len(prof2_entries) == 2


def test_generate_schedule_spreads_faculty_days(monkeypatch):
    """Verify single faculty assignments are spread across multiple days instead of clustering on one day."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class DaySpreadQuery(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 40, 'course_name': 'IT104 - Web Development', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    {'prof_course_id': 231, 'course_id': 40, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40}},
                ])
            elif self.table_name == 'professor':
                return FakeResponse([
                    {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
                ])
            elif self.table_name == 'room':
                return FakeResponse([
                    {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
                ])
            return super().execute()

    class DaySpreadSupabase(FakeSupabase):
        def table(self, table_name):
            return DaySpreadQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', DaySpreadSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # 3 sections for Year 1
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '3',
        'students[1]': '90',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) == 3
        assigned_days = [e.get('day') for e in preview]
        # Verify classes are spread across 3 different days
        assert len(set(assigned_days)) == 3


def test_generate_schedule_fallback_when_all_faculty_reach_cap(monkeypatch):
    """Verify fallback (least-overloaded professor or TBA) is activated when all qualified professors reach their limit."""
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class FallbackCapQuery(FakeQuery):
        def execute(self):
            if self.table_name == 'course':
                return FakeResponse([
                    {'course_id': 50, 'course_name': 'IT105 - Systems Analysis', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
                ])
            elif self.table_name == 'prof_course':
                return FakeResponse([
                    # Both professors only have max_hours = 3 (1 section capacity each)
                    {'prof_course_id': 241, 'course_id': 50, 'prof_id': 1, 'professor': {'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 3}},
                    {'prof_course_id': 242, 'course_id': 50, 'prof_id': 2, 'professor': {'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 3}},
                ])
            elif self.table_name == 'professor':
                return FakeResponse([
                    {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 3, 'department': 'CICT'},
                    {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 3, 'department': 'CICT'},
                ])
            elif self.table_name == 'room':
                return FakeResponse([
                    {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
                    {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture', 'department': 'CICT'},
                ])
            return super().execute()

    class FallbackCapSupabase(FakeSupabase):
        def table(self, table_name):
            return FallbackCapQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', FallbackCapSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # 3 sections (9 hours total, but capacity is only 6 hours total)
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '3',
        'students[1]': '90',
    })
    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) == 3
        # First 2 sections take Prof 1 and Prof 2
        # Third section uses fallback (least-overloaded professor or TBA)
        valid_profs = [e for e in preview if e.get('prof_id') in (1, 2) or e.get('prof_id') is None]
        assert len(valid_profs) == 3


def test_calculate_room_availability_logic():
    """Verify room availability calculations excluding lunch break, free interval derivation, and day classifications."""
    entries = [
        # Monday: 08:00 to 11:00 (3h), 13:00 to 15:00 (2h) -> 5h occupied, 7h free (lunch 12-1 is excluded)
        {'day': 'Monday', 'start_time': '08:00:00', 'end_time': '11:00:00', 'course_name': 'IT101', 'section': '1A', 'room_type': 'Lecture'},
        {'day': 'Monday', 'start_time': '13:00:00', 'end_time': '15:00:00', 'course_name': 'IT102', 'section': '1B', 'room_type': 'Lecture'},
        # Tuesday: 07:00 to 12:00 (5h) and 13:00 to 20:00 (7h) -> 12h occupied (all usable hours booked, lunch 12-1 excluded) -> Fully Booked
        {'day': 'Tuesday', 'start_time': '07:00:00', 'end_time': '12:00:00', 'course_name': 'IT103', 'section': '1C', 'room_type': 'Lecture'},
        {'day': 'Tuesday', 'start_time': '13:00:00', 'end_time': '20:00:00', 'course_name': 'IT104', 'section': '1D', 'room_type': 'Lecture'},
    ]
    room = {'room_id': 1, 'room_name': 'Lecture Hall 101', 'room_type': 'Lecture'}

    avail = app_module._calculate_room_availability(entries, timeslots=None, room=room)
    metrics = avail['metrics']

    # 6 operating days (Mon-Sat), 07:00 to 20:00 = 13 hrs window - 1 hr lunch = 12 usable hrs/day => 72 operating hours total
    assert metrics['total_operating_hours'] == 72.0
    assert metrics['raw_operating_hours'] == 78.0
    assert metrics['total_lunch_hours'] == 6.0
    assert metrics['total_occupied_hours'] == 17.0
    assert metrics['total_available_hours'] == 55.0
    assert metrics['lunch_break_info']['is_active'] is True
    assert metrics['free_days_count'] == 4
    assert metrics['partial_days_count'] == 1
    assert metrics['booked_days_count'] == 1

    monday = avail['day_status_map']['Monday']
    assert monday['status'] == 'partial'
    assert monday['label'] == 'Partially Used'
    assert monday['occupied_hours'] == 5.0
    assert monday['free_hours'] == 7.0
    # Monday free intervals: 07:00-08:00 (1h), 11:00-12:00 (1h), 15:00-20:00 (5h). Total = 7h. Lunch 12-1 is NOT included!
    monday_intervals = [(p['start_time'], p['end_time']) for p in avail['free_periods_by_day']['Monday']]
    assert len(monday_intervals) == 3
    assert ('12:00 PM', '01:00 PM') not in monday_intervals

    tuesday = avail['day_status_map']['Tuesday']
    assert tuesday['status'] == 'booked'
    assert tuesday['label'] == 'Fully Booked'
    assert tuesday['occupied_hours'] == 12.0
    assert tuesday['free_hours'] == 0.0
    assert avail['free_periods_by_day']['Tuesday'] == []

    # Wednesday has 0 classes -> 2 separate free periods (morning 07:00-12:00, afternoon 01:00-08:00), lunch strictly excluded
    wednesday = avail['day_status_map']['Wednesday']
    assert wednesday['status'] == 'free'
    assert wednesday['label'] == 'Fully Free'
    assert wednesday['occupied_hours'] == 0.0
    assert wednesday['free_hours'] == 12.0
    wed_intervals = [(p['start_time'], p['end_time']) for p in avail['free_periods_by_day']['Wednesday']]
    assert len(wed_intervals) == 2
    assert wed_intervals[0] == ('07:00 AM', '12:00 PM')
    assert wed_intervals[1] == ('01:00 PM', '08:00 PM')


def test_calculate_room_availability_lunch_slot_in_grid():
    """Verify that timetable grid explicitly marks lunch slots with type='lunch' and not 'available'."""
    room = {'room_id': 1, 'room_name': 'Lecture Hall 101', 'room_type': 'Lecture'}
    avail = app_module._calculate_room_availability([], timeslots=None, room=room)
    grid = avail['timetable_grid']

    # Find the 12:00 PM - 01:00 PM slot row
    lunch_row = next(r for r in grid if '12:00' in r['slot_label'] and '01:00' in r['slot_label'])
    assert lunch_row['is_lunch_slot'] is True

    # Across all days, lunch cell type must be 'lunch' with is_schedulable False, NOT 'available'
    for day in avail['operating_days']:
        cell = lunch_row['days'][day]
        assert cell['type'] == 'lunch'
        assert cell['label'] == 'Lunch Break (Unavailable)'
        assert cell.get('is_schedulable') is False


def test_calculate_room_availability_partial_occupancy_protection():
    """Verify that partially occupied slots are never marked as fully available."""
    entries = [
        # Monday: 08:30 to 10:30 (starts halfway in 08:00-09:00, ends halfway in 10:00-11:00)
        {'day': 'Monday', 'start_time': '08:30:00', 'end_time': '10:30:00', 'course_name': 'IT101', 'section': '1A', 'room_type': 'Lecture'},
    ]
    room = {'room_id': 2, 'room_name': 'Room 202', 'room_type': 'Lecture'}

    avail = app_module._calculate_room_availability(entries, timeslots=None, room=room)
    grid = avail['timetable_grid']

    # 07:00 - 08:00 Monday should be fully available
    row_7_8 = next(r for r in grid if '07:00' in r['slot_label'] and '08:00' in r['slot_label'])
    cell_7_8 = row_7_8['days']['Monday']
    assert cell_7_8['type'] == 'available'
    assert cell_7_8['is_partial'] is False

    # 08:00 - 09:00 Monday has 08:30-09:00 occupied => partial occupied, MUST NOT be available
    row_8_9 = next(r for r in grid if '08:00' in r['slot_label'] and '09:00' in r['slot_label'])
    cell_8_9 = row_8_9['days']['Monday']
    assert cell_8_9['type'] == 'occupied'
    assert cell_8_9['is_partial'] is True

    # 09:00 - 10:00 Monday is fully within 08:30-10:30 => occupied
    row_9_10 = next(r for r in grid if '09:00' in r['slot_label'] and '10:00' in r['slot_label'])
    cell_9_10 = row_9_10['days']['Monday']
    assert cell_9_10['type'] == 'occupied'
    assert cell_9_10['is_partial'] is False

    # 10:00 - 11:00 Monday has 10:00-10:30 occupied => partial occupied, MUST NOT be available
    row_10_11 = next(r for r in grid if '10:00' in r['slot_label'] and '11:00' in r['slot_label'])
    cell_10_11 = row_10_11['days']['Monday']
    assert cell_10_11['type'] == 'occupied'
    assert cell_10_11['is_partial'] is True


def test_view_room_schedule_preview_mode(monkeypatch):
    """Verify viewing room schedule in preview mode reflects session draft data and availability."""
    client = app_module.app.test_client()

    preview_entries = [
        {
            'id': 'draft_1',
            'room_id': 2,
            'room_name': 'Room 201',
            'room_type': 'Lecture',
            'day': 'Monday',
            'start': '08:00:00',
            'end': '11:00:00',
            'course_name': 'IT101 - Intro to Computing',
            'course_code': 'IT101',
            'professor_name': 'Alan Turing',
            'prof_course_id': 101,
            'section': '1A',
            'semester': '1st Semester',
            'session_type': 'Lecture',
        }
    ]

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'preview_room_test'
        session['schedule_preview'] = preview_entries
        session['has_schedule_preview'] = True
        app_module._SERVER_PREVIEW_STORE['preview_room_test'] = preview_entries

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Query with ?mode=preview
    response = client.get('/room_schedule/2?mode=preview')
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Check for preview banner, lunch break elements, and availability elements
    assert 'Preview Mode' in html
    assert 'Available Capacity' in html
    assert 'Lunch Break' in html
    assert 'Partially Used' in html
    assert 'Room 201' in html


def test_api_room_availability_endpoint(monkeypatch):
    """Verify /api/room_availability/<room_id> returns proper JSON schema with availability and metrics."""
    client = app_module.app.test_client()

    preview_entries = [
        {
            'id': 'draft_2',
            'room_id': 2,
            'room_name': 'Room 201',
            'room_type': 'Lecture',
            'day': 'Wednesday',
            'start': '09:00:00',
            'end': '12:00:00',
            'course_name': 'IT201 - Data Structures',
            'section': '2A',
            'session_type': 'Lecture',
        }
    ]

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'preview_room_api_test'
        session['schedule_preview'] = preview_entries
        session['has_schedule_preview'] = True
        app_module._SERVER_PREVIEW_STORE['preview_room_api_test'] = preview_entries

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Test API with preview mode
    response = client.get('/api/room_availability/2?mode=preview')
    assert response.status_code == 200
    data = response.get_json()

    assert data['success'] is True
    assert data['is_preview'] is True
    assert data['room_name'] == 'Room 201'
    assert 'availability' in data
    # FakeSupabase timeslot has 07:00:00 to 19:00:00 (12h window - 1h lunch = 11h usable/day * 6 days = 66 usable hours)
    assert data['availability']['metrics']['total_operating_hours'] == 66.0
    assert data['availability']['metrics']['total_occupied_hours'] == 3.0
    assert data['availability']['metrics']['total_available_hours'] == 63.0
    assert data['availability']['metrics']['total_lunch_hours'] == 6.0
    assert 'Wednesday' in data['availability']['day_status_map']
    assert data['availability']['day_status_map']['Wednesday']['label'] == 'Partially Used'


def test_edit_preview_entry_blocks_lunch_time_overlap(monkeypatch):
    """Verify that editing an entry to overlap lunch time is strictly rejected with 400 error."""
    client = app_module.app.test_client()

    preview_entries = [
        {
            'id': 1,
            'prof_course_id': 101,
            'course_id': 1,
            'course_name': 'IT101 - Intro to Computing',
            'prof_id': 1,
            'professor_name': 'Alan Turing',
            'section': '1A',
            'room_id': 2,
            'room_name': 'Room 201',
            'day': 'Monday',
            'start': '08:00 AM',
            'end': '11:00 AM',
            'time_range': 'Monday | 08:00 AM - 11:00 AM',
            'session_type': 'Lecture',
        }
    ]

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_lunch_test'
        session['schedule_preview'] = preview_entries
        session['has_schedule_preview'] = True
        app_module._SERVER_PREVIEW_STORE['prev_lunch_test'] = preview_entries

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Attempt to move class into 12:00 PM - 01:00 PM (lunch break)
    resp = client.post('/edit_preview_entry', json={
        'id': 1,
        'prof_course_id': 101,
        'room_id': 2,
        'day': 'Monday',
        'timeslot': 'Monday | 12:00 PM - 01:00 PM',
        'section': '1A',
    })

    assert resp.status_code == 400
    err_json = resp.get_json()
    assert 'Lunch break conflict' in err_json.get('error', '')


def test_edit_schedule_entry_blocks_lunch_time_overlap(monkeypatch):
    """Verify that editing an existing confirmed schedule entry into lunch time is strictly rejected."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class EditLunchSupabase(FakeSupabase):
        def table(self, table_name):
            class LocalQuery(FakeQuery):
                def execute(self):
                    if self.table_name == 'schedule':
                        return FakeResponse([{
                            'schedule_id': 10,
                            'prof_course_id': 101,
                            'room_id': 1,
                            'day': 'Monday',
                            'class_start': '08:00:00',
                            'class_end': '11:00:00',
                            'session_type': 'Lecture',
                            'section': '1A',
                            'semester': '1st Semester',
                            'major': None,
                            'program': 'BSIT',
                            'prof_course': {
                                'prof_course_id': 101,
                                'prof_id': 1,
                                'course_id': 1,
                                'course': {'course_name': 'IT101 - Intro to Computing'},
                                'professor': {'first_name': 'Alan', 'last_name': 'Turing'}
                            }
                        }])
                    elif self.table_name == 'timeslot':
                        return FakeResponse([{
                            'start_time': '07:00:00',
                            'end_time': '19:00:00',
                            'lunch_time': '12:00:00',
                            'day': 'Monday,Tuesday,Wednesday,Thursday,Friday,Saturday'
                        }])
                    return super().execute()
            return LocalQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', EditLunchSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    # Attempt to edit entry into 12:00 PM - 01:00 PM
    resp = client.post('/edit_schedule_entry/10', json={
        'prof_course_id': 101,
        'room_id': 1,
        'timeslot': 'Monday | 12:00 PM - 01:00 PM',
        'session_type': 'Lecture'
    })

    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json.get('success') is False
    assert 'Lunch break conflict' in res_json.get('message', '')


def test_calculate_room_availability_calendar_blocks_single_render():
    """Verify that multi-hour classes render as a single contiguous block with dynamic offsets and no duplicates."""
    entries = [
        # Monday: 01:00 PM to 03:00 PM (2 hours contiguous)
        {
            'day': 'Monday',
            'start_time': '13:00:00',
            'end_time': '15:00:00',
            'course_name': 'IT201 - Data Structures',
            'section': '2A',
            'room_type': 'Lecture',
            'professor': 'Grace Hopper',
            'session_type': 'Lecture',
        }
    ]
    room = {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture'}
    avail = app_module._calculate_room_availability(entries, timeslots=None, room=room)

    # 1. Verify single hour markers on time axis (no 'to ' range strings)
    assert 'time_markers' in avail
    markers = avail['time_markers']
    assert len(markers) > 0
    marker_labels = [m['label'] for m in markers]
    assert '07:00 AM' in marker_labels
    assert '01:00 PM' in marker_labels
    assert '02:00 PM' in marker_labels
    assert '03:00 PM' in marker_labels
    for label in marker_labels:
        assert 'to ' not in label.lower(), f"Marker label '{label}' should be a single hour marker, not a range"

    # 2. Verify Monday calendar blocks
    mon_blocks = avail['day_calendar_blocks']['Monday']

    # Must contain exactly ONE block for IT201 - Data Structures (not two individual hourly slots!)
    it201_blocks = [b for b in mon_blocks if b.get('course_name') == 'IT201 - Data Structures']
    assert len(it201_blocks) == 1, f"Expected exactly 1 contiguous block for 2-hour class, found {len(it201_blocks)}"

    it201 = it201_blocks[0]
    assert it201['type'] == 'occupied'
    assert it201['duration_hours'] == 2.0
    assert it201['duration_minutes'] == 120.0
    # Operating window starts at 07:00 AM; 01:00 PM is 6 hours = 360 minutes offset
    assert it201['top_offset_minutes'] == 360.0
    assert it201['start_time'] == '01:00 PM'
    assert it201['end_time'] == '03:00 PM'

    # 3. Verify Lunch Break is a single contiguous block
    lunch_blocks = [b for b in mon_blocks if b['type'] == 'lunch']
    assert len(lunch_blocks) == 1
    lunch = lunch_blocks[0]
    assert lunch['label'] == 'Lunch Break (Unavailable)'
    assert lunch['duration_hours'] == 1.0
    assert lunch['duration_minutes'] == 60.0
    assert lunch['top_offset_minutes'] == 300.0  # 12:00 PM is 5 hours = 300 minutes from 07:00 AM
    assert lunch['is_schedulable'] is False

    # 4. Verify Available periods are contiguous blocks (07:00-12:00 before lunch, 03:00-08:00 after class)
    avail_blocks = [b for b in mon_blocks if b['type'] == 'available']
    assert len(avail_blocks) == 2
    # 07:00 AM - 12:00 PM (5 hrs)
    assert avail_blocks[0]['start_time'] == '07:00 AM'
    assert avail_blocks[0]['end_time'] == '12:00 PM'
    assert avail_blocks[0]['duration_hours'] == 5.0
    # 03:00 PM - 08:00 PM (5 hrs)
    assert avail_blocks[1]['start_time'] == '03:00 PM'
    assert avail_blocks[1]['end_time'] == '08:00 PM'
    assert avail_blocks[1]['duration_hours'] == 5.0


def test_view_room_schedule_renders_true_calendar_view(monkeypatch):
    """Verify that room schedule view template renders true calendar layout with single hour markers and contiguous cards."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class CalendarRoomSupabase(FakeSupabase):
        def table(self, table_name):
            class LocalQuery(FakeQuery):
                def execute(self):
                    if self.table_name == 'room':
                        return FakeResponse([{
                            'room_id': 1,
                            'room_name': 'Room 101',
                            'room_type': 'Lecture',
                            'capacity': 40,
                            'department': 'CICT'
                        }])
                    elif self.table_name == 'schedule':
                        return FakeResponse([{
                            'schedule_id': 10,
                            'prof_course_id': 101,
                            'room_id': 1,
                            'day': 'Monday',
                            'class_start': '13:00:00',
                            'class_end': '15:00:00',
                            'session_type': 'Lecture',
                            'section': '1A',
                            'semester': '1st Semester',
                            'major': None,
                            'program': 'BSIT',
                            'prof_course': {
                                'prof_course_id': 101,
                                'prof_id': 1,
                                'course_id': 1,
                                'course': {'course_name': 'IT101 - Intro to Computing'},
                                'professor': {'first_name': 'Alan', 'last_name': 'Turing'}
                            }
                        }])
                    elif self.table_name == 'timeslot':
                        return FakeResponse([{
                            'start_time': '07:00:00',
                            'end_time': '20:00:00',
                            'lunch_time': '12:00:00',
                            'day': 'Monday,Tuesday,Wednesday,Thursday,Friday,Saturday'
                        }])
                    return super().execute()
            return LocalQuery(table_name)

    monkeypatch.setattr(app_module, 'supabase', CalendarRoomSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    resp = client.get('/room_schedule/1')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')

    # Verify calendar view elements
    assert 'true-calendar-wrapper' in html
    assert 'calendar-time-axis' in html
    assert 'calendar-hour-marker' in html
    assert 'calendar-event-block' in html
    assert 'calendar-block-occupied' in html

    # Verify single hour markers exist
    assert '01:00 PM' in html
    assert '02:00 PM' in html

    # Verify contiguous subject card renders once
    assert 'IT101 - Intro to Computing' in html
    # Count occurrences of the course name inside calendar event blocks
    import re
    block_matches = re.findall(r'calendar-block-occupied.*?IT101 - Intro to Computing', html, re.DOTALL)
    assert len(block_matches) == 1, f"Expected class to render in exactly 1 calendar block, found {len(block_matches)}"




















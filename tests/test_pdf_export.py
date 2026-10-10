import io
import pytest
from datetime import timedelta
import app as app_module
import pdf_export


def test_parse_time_preserves_pm_and_am():
    """Verify that PM and AM times are parsed correctly with zero AM/PM confusion."""
    # 1:00 PM -> 13:00 (46800 sec)
    sec_1pm = pdf_export._parse_time_to_seconds('01:00 PM')
    assert sec_1pm == 13 * 3600

    # 1:00 AM -> 01:00 (3600 sec)
    sec_1am = pdf_export._parse_time_to_seconds('01:00 AM')
    assert sec_1am == 1 * 3600
    assert sec_1pm != sec_1am

    # 12:00 PM -> 12:00 (43200 sec)
    sec_12pm = pdf_export._parse_time_to_seconds('12:00 PM')
    assert sec_12pm == 12 * 3600

    # 12:00 AM -> 00:00 (0 sec)
    sec_12am = pdf_export._parse_time_to_seconds('12:00 AM')
    assert sec_12am == 0

    # 7:30 AM -> 7 * 3600 + 1800
    sec_730am = pdf_export._parse_time_to_seconds('07:30 AM')
    assert sec_730am == 7 * 3600 + 1800

    # Test reverse display formatting
    assert pdf_export._seconds_to_display_time(sec_1pm) == '01:00 PM'
    assert pdf_export._seconds_to_display_time(sec_1am) == '01:00 AM'
    assert pdf_export._seconds_to_display_time(sec_12pm) == '12:00 PM'


def test_generate_pdf_room_schedule():
    """Verify room timetable PDF generation with lecture and lab classes."""
    room = {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture'}
    entries = [
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '10:00:00',
            'course_code': 'IT101 - Intro to Computing',
            'section': '1A',
            'professor': 'Dr. Alan Turing',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('room', room, entries, filter_metadata={'semester': '1st Semester'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_section_schedule():
    """Verify section timetable PDF generation."""
    section = {'section_name': 'BSIT 2A', 'year_level': '2', 'semester': '2nd Semester', 'major': 'Network'}
    entries = [
        {
            'day': 'Tuesday',
            'start_time_raw': '09:00:00',
            'end_time_raw': '12:00:00',
            'course_code': 'NET201 - Advanced Networking',
            'professor': 'Grace Hopper',
            'room': 'Cisco Lab',
            'session_type': 'Laboratory',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('section', section, entries, filter_metadata={'semester': '2nd Semester', 'year': '2', 'major': 'Network'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_professor_schedule():
    """Verify professor timetable PDF generation."""
    professor = {'prof_id': 1, 'first_name': 'Ada', 'last_name': 'Lovelace', 'department': 'Computer Science'}
    entries = [
        {
            'day': 'Wednesday',
            'start_time_raw': '13:00:00',
            'end_time_raw': '15:00:00',
            'course_code': 'CS102 - Data Structures',
            'section': '1B',
            'room': 'Room 305',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('professor', professor, entries, filter_metadata={'semester': '1st Semester'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_multiple_classes_same_slot():
    """Requirement 13: If multiple classes occur on the same day/time, display them properly without losing information."""
    section = {'section_name': 'Irregular-Combo'}
    entries = [
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '09:00:00',
            'course_code': 'MATH101 - Calculus 1',
            'professor': 'Prof Gauss',
            'room': 'Room 101',
            'session_type': 'Lecture',
        },
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '09:00:00',
            'course_code': 'HIST101 - Philippine History',
            'professor': 'Prof Rizal',
            'room': 'Room 102',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('section', section, entries)
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_routes_export_professor_pdf(monkeypatch):
    """Test GET /professor_schedule/<professor_id>/export_pdf returns valid PDF file."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 5, 'first_name': 'Alan', 'last_name': 'Turing', 'department': 'CS', 'max_hours': 30}]})()
            elif self.table_name == 'professor_load':
                return type('Resp', (), {'data': [{'professor_load_id': 10, 'course_id': 1, 'course': {'course_code': 'IT101 - Intro to Computing'}}]})()
            elif self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 3,
                        'professor_load_id': 10,
                        'room_id': 1,
                        'day': 'Friday',
                        'class_start': '14:00:00',
                        'class_end': '17:00:00',
                        'section': '2B',
                        'semester': '1st Semester',
                        'major': None,
                        'session_type': 'Lecture',
                        'professor_load': {'course': {'course_code': 'IT101 - Intro to Computing'}},
                        'room': {'room_name': 'Room 105'}
                    }
                ]})()
            elif self.table_name == 'working_hours':
                return type('Resp', (), {'data': []})()
            elif self.table_name == 'semester':
                return type('Resp', (), {'data': [{'school_year': '2026-2027', 'term': '1st Semester'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/professor_schedule/5/export_pdf')
    assert resp.status_code == 200
    assert 'application/pdf' in resp.content_type
    assert 'attachment;' in resp.headers.get('Content-Disposition', '')
    assert 'Teacher_Schedule_Alan_Turing.pdf' in resp.headers.get('Content-Disposition', '')
    assert resp.data.startswith(b'%PDF-')


def test_routes_export_professor_pdf_empty_filters(monkeypatch):
    """Test GET /professor_schedule/<professor_id>/export_pdf?year=&semester=&major= does not 404 and treats empty filters as no filter."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 12, 'first_name': 'Grace', 'last_name': 'Hopper', 'department': 'IT', 'max_hours': 30}]})()
            elif self.table_name == 'professor_load':
                return type('Resp', (), {'data': [{'professor_load_id': 20, 'course_id': 2, 'course': {'course_code': 'CS102 - Data Structures'}}]})()
            elif self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 7,
                        'professor_load_id': 20,
                        'room_id': 2,
                        'day': 'Monday',
                        'class_start': '09:00:00',
                        'class_end': '12:00:00',
                        'section': '3A',
                        'semester': '1st Semester',
                        'major': None,
                        'session_type': 'Lecture',
                        'professor_load': {'course': {'course_code': 'CS102 - Data Structures'}},
                        'room': {'room_name': 'Lab 201'}
                    }
                ]})()
            elif self.table_name == 'working_hours':
                return type('Resp', (), {'data': []})()
            elif self.table_name == 'semester':
                return type('Resp', (), {'data': [{'school_year': '2026-2027', 'term': '1st Semester'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/professor_schedule/12/export_pdf?year=&semester=&major=')
    assert resp.status_code == 200
    assert 'application/pdf' in resp.content_type
    assert 'attachment;' in resp.headers.get('Content-Disposition', '')
    assert 'Teacher_Schedule_Grace_Hopper.pdf' in resp.headers.get('Content-Disposition', '')
    assert resp.data.startswith(b'%PDF-')


def test_routes_export_professor_pdf_empty_redirects(monkeypatch):
    """Test that when a professor has no active entries, it gracefully redirects with flash instead of 404/500."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 99, 'first_name': 'Empty', 'last_name': 'Prof'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/professor_schedule/99/export_pdf')
    # Should redirect (302) to view_professor_schedule, NOT 404 or 500
    assert resp.status_code == 302
    assert '/professor_schedule/99' in resp.headers.get('Location', '')


def test_routes_export_section_pdf(monkeypatch):
    """Test GET /schedule/<section_name>/export_pdf returns valid PDF file."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 101,
                        'day': 'Monday',
                        'class_start': '08:00:00',
                        'class_end': '11:00:00',
                        'session_type': 'Lecture',
                        'semester': '1st Semester',
                        'major': None,
                        'professor_load_id': 1,
                        'room_id': 1,
                        'professor_load': {
                            'professor_load_id': 1,
                            'prof_id': 5,
                            'course_id': 1,
                            'course': {'course_id': 1, 'course_code': 'IT101 - Intro to Computing'},
                            'professor': {'prof_id': 5, 'first_name': 'Alan', 'last_name': 'Turing'}
                        },
                        'room': {'room_name': 'Room 101'}
                    }
                ]})()
            elif self.table_name == 'working_hours':
                return type('Resp', (), {'data': []})()
            elif self.table_name == 'semester':
                return type('Resp', (), {'data': [{'school_year': '2026-2027', 'term': '1st Semester'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/schedule/2B/export_pdf')
    assert resp.status_code == 200
    assert 'application/pdf' in resp.content_type
    assert 'attachment;' in resp.headers.get('Content-Disposition', '')
    assert 'Section_Schedule_2B' in resp.headers.get('Content-Disposition', '')
    assert resp.data.startswith(b'%PDF-')


def test_routes_export_section_pdf_empty_filters(monkeypatch):
    """Test GET /schedule/<section_name>/export_pdf?year=&semester=&major= properly handles empty filters."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 102,
                        'day': 'Wednesday',
                        'class_start': '13:00:00',
                        'class_end': '16:00:00',
                        'session_type': 'Laboratory',
                        'semester': '1st Semester',
                        'major': None,
                        'professor_load_id': 2,
                        'room_id': 2,
                        'professor_load': {
                            'professor_load_id': 2,
                            'prof_id': 6,
                            'course_id': 2,
                            'course': {'course_id': 2, 'course_code': 'IT102 - Web Dev'},
                            'professor': {'prof_id': 6, 'first_name': 'Tim', 'last_name': 'Berners-Lee'}
                        },
                        'room': {'room_name': 'Lab 102'}
                    }
                ]})()
            elif self.table_name == 'working_hours':
                return type('Resp', (), {'data': []})()
            elif self.table_name == 'semester':
                return type('Resp', (), {'data': [{'school_year': '2026-2027', 'term': '1st Semester'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/schedule/2B/export_pdf?year=&semester=&major=')
    assert resp.status_code == 200
    assert 'application/pdf' in resp.content_type
    assert 'attachment;' in resp.headers.get('Content-Disposition', '')
    assert 'Section_Schedule_2B' in resp.headers.get('Content-Disposition', '')
    assert resp.data.startswith(b'%PDF-')


def test_routes_export_section_pdf_empty_redirects(monkeypatch):
    """Test that when a section has no active entries, it gracefully redirects with flash instead of 404/500."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeQuery:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def like(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeQuery(name))

    resp = client.get('/schedule/EmptySec/export_pdf')
    # Should redirect (302) to view_schedule, NOT 404 or 500
    assert resp.status_code == 302
    assert '/schedule/EmptySec' in resp.headers.get('Location', '')


def test_section_schedule_template_has_pdf_export_button():
    """Verify that templates/schedules.html and templates/generated_schedule.html contain PDF export buttons."""
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schedules_tmpl = os.path.join(base_dir, 'templates', 'schedules.html')
    gen_sched_tmpl = os.path.join(base_dir, 'templates', 'generated_schedule.html')
    gen_prof_tmpl = os.path.join(base_dir, 'templates', 'generated_professor_schedule.html')

    with open(schedules_tmpl, 'r', encoding='utf-8') as f:
        sched_content = f.read()
    assert '/export_pdf' in sched_content
    assert 'Export to PDF' in sched_content

    with open(gen_sched_tmpl, 'r', encoding='utf-8') as f:
        gen_content = f.read()
    assert '/export_pdf' in gen_content
    assert 'Export to PDF' in gen_content

    with open(gen_prof_tmpl, 'r', encoding='utf-8') as f:
        prof_content = f.read()
    assert '/export_pdf' in prof_content
    assert 'Export to PDF' in prof_content


def test_official_neust_pdf_format_and_single_page():
    """Verify that generated PDFs conform to the official NEUST single-page Folio format."""
    import re
    prof = {'prof_id': 10, 'first_name': 'Alan', 'last_name': 'Turing'}
    entries = [
        {'day': 'Monday', 'start_time_raw': '08:00:00', 'end_time_raw': '11:00:00', 'course_code': 'IT101 - Intro to Computing', 'section': 'BSIT 1A', 'room': 'Rm 301', 'session_type': 'Lecture'},
        {'day': 'Wednesday', 'start_time_raw': '13:00:00', 'end_time_raw': '16:00:00', 'course_code': 'IT102 - Programming Lab', 'section': 'BSIT 1B', 'room': 'Lab 1', 'session_type': 'Laboratory'},
    ]

    # Test Professor schedule
    buf_prof = pdf_export.generate_timetable_pdf('professor', prof, entries, filter_metadata={'semester': '1st Semester', 'school_year': '2026-2027'})
    data_prof = buf_prof.read()
    assert data_prof.startswith(b'%PDF-')
    # Must be exactly 1 page
    page_matches = re.findall(rb'/Type\s*/Page\b', data_prof)
    assert len(page_matches) == 1

    # Test Section schedule
    sec = {'section_name': 'BSIT 3M', 'year_level': '3', 'semester': '2nd Semester'}
    sec_entries = [
        {'day': 'Tuesday', 'start_time_raw': '09:00:00', 'end_time_raw': '12:00:00', 'course_code': 'IT-WS07 - Web Systems', 'professor': 'Grace Hopper', 'room': 'Lab 4', 'session_type': 'Laboratory'},
    ]
    buf_sec = pdf_export.generate_timetable_pdf('section', sec, sec_entries, filter_metadata={'semester': '2nd Semester', 'school_year': '2026-2027'})
    data_sec = buf_sec.read()
    assert data_sec.startswith(b'%PDF-')
    page_matches_sec = re.findall(rb'/Type\s*/Page\b', data_sec)
    assert len(page_matches_sec) == 1

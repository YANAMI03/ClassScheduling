import pytest
from unittest.mock import MagicMock, patch
from app import app, _set_preview_for_user


@pytest.fixture
def client():
    app.config['TESTING'] = True
    return app.test_client()


def test_generation_inserts_is_used_false(client):
    """
    Ensure that confirm_preview constructs rows with 'is_used': False
    and passes them to the database, ensuring newly generated schedules start as not used.
    """
    preview = [{
        'course_id': 1,
        'course_code': 'IT101',
        'section': 'BSIT-1A',
        'semester': '1st Semester',
        'program': 'BSIT',
        'day': 'Monday',
        'start': '08:00:00',
        'end': '10:00:00',
        'room_id': 101,
        'session_type': 'Lecture',
        'school_year': '2026-2027'
    }]
    _set_preview_for_user(preview, user_id=10)

    with client.session_transaction() as sess:
        sess['user_id'] = 10
        sess['username'] = 'testchair'
        sess['role'] = 'chair'
        sess['program_id'] = 1
        sess['program'] = 'BSIT'

    with patch('app.supabase') as mock_sb:
        # Mock room validation
        room_mock = MagicMock()
        room_mock.execute.return_value.data = [{'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'}]
        # Mock course lookup
        course_mock = MagicMock()
        course_mock.execute.return_value.data = [{'course_id': 1, 'course_code': 'IT101', 'semester': '1st Semester'}]
        # Mock professor_load lookup
        pl_mock = MagicMock()
        pl_mock.execute.return_value.data = [{'id': 50, 'prof_id': 1, 'course_id': 1}]

        def table_side_effect(name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.in_.return_value = m
            m.is_.return_value = m
            if name == 'room':
                return room_mock
            elif name == 'course':
                return course_mock
            elif name == 'professor_load':
                return pl_mock
            elif name == 'schedule_with_semester':
                m.execute.return_value.data = []
                return m
            elif name == 'schedule':
                m.execute.return_value.data = []
                return m
            return m

        mock_sb.table.side_effect = table_side_effect

        captured_rpc = {}
        def mock_rpc_call(fn, params):
            captured_rpc['func'] = fn
            captured_rpc['params'] = params
            res = MagicMock()
            res.execute.return_value.data = {'success': True, 'batch_id': 'new-batch-uuid', 'inserted_count': 1}
            return res

        mock_sb.rpc.side_effect = mock_rpc_call

        resp = client.post('/confirm_preview', follow_redirects=False)
        assert resp.status_code == 302
        assert captured_rpc.get('func') == 'confirm_schedule_transaction'
        rows = captured_rpc['params']['p_rows']
        assert len(rows) == 1
        assert rows[0]['is_used'] is False


def test_schedules_view_not_used_badge_and_button(client):
    """
    When active schedule has is_used = False:
    - active-used-badge must be hidden (display: none)
    - btn-toggle-active-used must display 'Mark as Used'
    """
    with client.session_transaction() as sess:
        sess['user_id'] = 10
        sess['username'] = 'testchair'
        sess['role'] = 'chair'
        sess['program_id'] = 1
        sess['program'] = 'BSIT'

    sched_row = {
        'schedule_id': 101,
        'professor_load_id': 50,
        'section': 'BSIT-1A',
        'day': 'Monday',
        'class_start': '08:00:00',
        'class_end': '10:00:00',
        'session_type': 'Lecture',
        'room_id': 101,
        'archive_batch_id': 'batch-test-1',
        'school_year': '2026-2027',
        'is_used': False,
        'room': {'room_name': 'Room 101'},
        'professor_load': {
            'id': 50,
            'course_id': 1,
            'professor_name': 'Prof John',
            'course': {
                'course_id': 1,
                'course_code': 'IT101',
                'program_id': 1,
                'semester': '1st Semester',
                'specialization': None,
                'program': {'program_name': 'BSIT'}
            }
        }
    }

    with patch('app.supabase') as mock_sb, \
         patch('app._get_programs', return_value=[{'id': 1, 'program_name': 'BSIT'}]):

        def fake_table(name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.in_.return_value = m
            m.range.return_value = m
            m.limit.return_value = m
            if name == 'schedule':
                m.execute.return_value.data = [sched_row]
            elif name == 'schedule_with_semester':
                m.execute.return_value.data = [{'semester': '1st Semester'}]
            else:
                m.execute.return_value.data = []
            return m

        mock_sb.table.side_effect = fake_table

        resp = client.get('/schedules')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        # Badge should be hidden
        assert 'id="active-used-badge" style="display: none;"' in html or 'id="active-used-badge"  style="display: none;"' in html
        # Button should show Mark as Used
        assert 'Mark as Used' in html
        assert 'btn-success' in html


def test_filters_no_all_options_in_schedules(client):
    """
    On /schedules:
    - 'All Programs' must NOT exist in the program dropdown
    - 'All Semesters' must NOT exist in the semester dropdown
    - Options for semester are only '1st Semester' and '2nd Semester'
    """
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'
        sess['program'] = 'BSIT'

    with patch('app.supabase') as mock_sb, \
         patch('app._get_programs', return_value=[{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}]):

        def fake_table(name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.in_.return_value = m
            m.range.return_value = m
            m.limit.return_value = m
            m.execute.return_value.data = []
            return m

        mock_sb.table.side_effect = fake_table

        resp = client.get('/schedules')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        # Confirm No 'All Programs' or 'All Semesters' options
        assert 'All Programs</option>' not in html
        assert 'All Semesters</option>' not in html
        assert '<option value="1st Semester"' in html
        assert '<option value="2nd Semester"' in html

        # Empty state should be clear
        assert 'No schedule for this program and semester' in html


def test_filters_fallback_on_all_or_missing_params(client):
    """
    If program='all' or semester='all' is passed in query string:
    - Backend does NOT accept 'all'
    - Backend falls back to the default program (e.g. BSIT) and active semester (or 1st Semester)
    """
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'
        sess['program'] = 'BSIT'

    captured_queries = []
    with patch('app.supabase') as mock_sb, \
         patch('app._get_programs', return_value=[{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}]):

        def fake_table(name):
            m = MagicMock()
            m.select.return_value = m
            m.range.return_value = m
            m.limit.return_value = m
            def record_eq(col, val):
                captured_queries.append((col, val))
                return m
            m.eq.side_effect = record_eq
            m.execute.return_value.data = []
            return m

        mock_sb.table.side_effect = fake_table

        resp = client.get('/schedules?program=all&semester=all')
        assert resp.status_code == 200

        # Program query should NOT be unconstrained; it should filter by BSIT's program_id (1)
        assert ('professor_load.course.program_id', 1) in captured_queries
        # Semester query should NOT be 'all'; it should filter by '1st Semester'
        assert ('professor_load.course.semester', '1st Semester') in captured_queries


def test_filters_no_all_in_archive_room_and_professor(client):
    """
    Verify room_schedule, professor_schedule, and schedule_archive templates do NOT have
    'All Programs' or 'All Semesters' in their program/semester dropdowns.
    """
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'
        sess['program'] = 'BSIT'

    with patch('app.supabase') as mock_sb, \
         patch('app._get_programs', return_value=[{'id': 1, 'program_name': 'BSIT'}, {'id': 2, 'program_name': 'BSDS'}]):

        def fake_table(name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.range.return_value = m
            m.limit.return_value = m
            m.execute.return_value.data = []
            return m

        mock_sb.table.side_effect = fake_table

        # 1. room_schedule
        r_resp = client.get('/room_schedule')
        assert r_resp.status_code == 200
        r_html = r_resp.data.decode('utf-8')
        assert 'All Programs</option>' not in r_html

        # 2. professor_schedule
        p_resp = client.get('/professor_schedule')
        assert p_resp.status_code == 200
        p_html = p_resp.data.decode('utf-8')
        assert 'All Programs</option>' not in p_html

        # 3. schedule_archive
        a_resp = client.get('/schedule_archive')
        assert a_resp.status_code == 200
        a_html = a_resp.data.decode('utf-8')
        assert 'All Programs</option>' not in a_html
        assert 'All Semesters</option>' not in a_html
        # School Year filter should STILL have All School Years
        assert 'All School Years</option>' in a_html

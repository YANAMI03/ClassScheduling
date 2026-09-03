import app as app_module
from unittest.mock import MagicMock


class FakeResponse:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class FakeArchiveQuery:
    def __init__(self, table_name):
        self.table_name = table_name
        self._filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
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

    def execute(self):
        if self.table_name == 'schedule_archive':
            if self._filters.get('batch_id') == 'batch-1':
                return FakeResponse([
                    {
                        'archive_id': 1,
                        'batch_id': 'batch-1',
                        'course_id': 101,
                        'prof_id': 1,
                        'room_id': 1,
                        'day': 'Monday',
                        'class_start': '08:00:00',
                        'class_end': '09:00:00',
                        'session_type': 'Lecture',
                        'section': '1A',
                        'semester': '1st Semester',
                        'major': None,
                        'program': 'BSIT',
                        'archived_at': '2026-09-01T12:00:00Z',
                        'archived_by': 'Scheduler',
                        'archive_reason': 'Replaced on confirmation',
                        'course': {'course_name': 'IT101 - Intro to Computing'},
                        'professor': {'first_name': 'Alan', 'last_name': 'Turing'},
                        'room': {'room_name': 'Room 101'},
                    }
                ])
            return FakeResponse([
                {
                    'archive_id': 1,
                    'batch_id': 'batch-1',
                    'course_id': 101,
                    'section': '1A',
                    'semester': '1st Semester',
                    'major': None,
                    'program': 'BSIT',
                    'archived_at': '2026-09-01T12:00:00Z',
                    'archived_by': 'Scheduler',
                    'archive_reason': 'Replaced on confirmation',
                }
            ])
        elif self.table_name == 'schedule':
            return FakeResponse([])
        elif self.table_name == 'program_department':
            return FakeResponse({'department_name': 'CICT'})
        return FakeResponse([])


class FakeArchiveSupabase:
    def table(self, table_name):
        return FakeArchiveQuery(table_name)

    def rpc(self, func_name, params=None):
        class FakeRpc:
            def execute(self):
                if func_name == 'restore_archived_schedule_batch':
                    return FakeResponse({
                        'success': True,
                        'restored_batch_id': params.get('p_batch_id'),
                        'archived_current_count': 1,
                        'restored_count': 1,
                        'semester': '1st Semester',
                        'program': 'BSIT'
                    })
                elif func_name == 'confirm_schedule_transaction':
                    return FakeResponse({
                        'success': True,
                        'batch_id': 'batch-new',
                        'archived_count': 1,
                        'deleted_count': 1,
                        'inserted_count': 1,
                        'semester': '1st Semester',
                        'program': 'BSIT'
                    })
                elif func_name == 'get_schedule_archive_batches':
                    return FakeResponse([
                        {
                            'batch_id': 'batch-1',
                            'semester': '1st Semester',
                            'program': 'BSIT',
                            'archived_at': '2026-09-01T10:00:00Z',
                            'archived_by': 'Admin',
                            'archive_reason': 'Replaced on confirmation',
                            'entry_count': 1,
                            'section_count': 1,
                            'sections': ['1A']
                        }
                    ])
                return FakeResponse({'success': True})
        return FakeRpc()


def test_schedule_archive_list_page(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    assert b'Schedule Archive' in response.data
    assert b'batch-1' in response.data or b'batch-1'[:8] in response.data.decode('utf-8')


def test_view_schedule_archive_batch_detail(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive/batch-1')
    assert response.status_code == 200
    assert b'Archived Schedule Details' in response.data
    assert b'1A' in response.data
    assert b'IT101' in response.data


def test_restore_schedule_archive_rpc_success(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/restore_schedule_archive/batch-1', follow_redirects=False)
    assert response.status_code == 302
    assert '/schedules' in response.headers.get('Location', '')


def test_restore_schedule_archive_viewer_forbidden(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 2
        session['program'] = 'BSIT'
        session['username'] = 'viewer'
        session['role'] = 'Viewer'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.post('/restore_schedule_archive/batch-1', follow_redirects=False)
    assert response.status_code == 302
    assert '/schedule_archive' in response.headers.get('Location', '')


def test_delete_schedule_archive_admin_only(monkeypatch):
    client = app_module.app.test_client()

    # Scheduler cannot delete
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'scheduler'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    res_sched = client.post('/delete_schedule_archive/batch-1', follow_redirects=False)
    assert res_sched.status_code == 302

    # Admin can delete
    with client.session_transaction() as session:
        session['role'] = 'Admin'

    res_admin = client.post('/delete_schedule_archive/batch-1', follow_redirects=False)
    assert res_admin.status_code == 302
    assert '/schedule_archive' in res_admin.headers.get('Location', '')


def test_confirm_preview_second_semester_sets_active_semester_and_redirects(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_2nd_sem'
        session['schedule_preview'] = [
            {
                'course_id': 21,
                'course_name': 'IT202 - Object Oriented Programming',
                'section': '2A',
                'prof_id': 54,
                'room_id': 21,
                'day': 'Wednesday',
                'start': '10:00 AM',
                'end': '12:00 PM',
                'session_type': 'Lecture',
                'semester': '2nd Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/confirm_preview', follow_redirects=False)
    assert response.status_code == 302
    assert 'semester=2nd+Semester' in response.headers.get('Location', '') or 'semester=2nd%20Semester' in response.headers.get('Location', '')

    with client.session_transaction() as session:
        assert session.get('active_semester') == '2nd Semester'
        assert len(session.get('generated_sections', [])) == 1
        assert session['generated_sections'][0]['semester'] == '2nd Semester'


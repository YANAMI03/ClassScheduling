import io
import json
import pytest
import app as app_module


class FakeSupabaseTableQuery:
    def __init__(self, data=None):
        self._data = data or []

    def select(self, *args, **kwargs):
        return self

    def range(self, start, end):
        return self

    def delete(self):
        return self

    def neq(self, *args, **kwargs):
        return self

    def upsert(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def execute(self):
        class Response:
            def __init__(self, data):
                self.data = data
        return Response(self._data)


class FakeSupabaseClient:
    def __init__(self, tables=None, rpc_result=None):
        self._tables = tables or {}
        self._rpc_result = rpc_result

    def table(self, name):
        data = self._tables.get(name, [])
        return FakeSupabaseTableQuery(data)

    def rpc(self, fn_name, params=None):
        class RPCQuery:
            def __init__(self, result):
                self.result = result
            def execute(self):
                class Response:
                    def __init__(self, data):
                        self.data = data
                return Response(self.result)
        if self._rpc_result is not None:
            return RPCQuery(self._rpc_result)
        raise RuntimeError("RPC error")


def test_backup_access_control(monkeypatch):
    """Test that unauthorized users cannot trigger backup."""
    client = app_module.app.test_client()

    # 1. Unauthenticated -> redirect to login
    res = client.get('/backup')
    assert res.status_code == 302
    assert '/login' in res.location

    # 2. Viewer role -> 403 Forbidden
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-viewer-123'
        sess['role'] = 'Viewer'
        sess['username'] = 'viewer'

    res = client.get('/backup')
    assert res.status_code == 403


def test_backup_database_full_export(monkeypatch):
    """Test full backup export with metadata and proper table structure."""
    fake_tables = {
        'program_department': [{'program_name': 'BSIT', 'department_name': 'CICT'}],
        'room': [{'room_id': 1, 'room_name': 'Room 101'}],
        'course': [{'course_id': 101, 'course_name': 'Data Structures', 'program': 'BSIT'}],
    }
    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseClient(fake_tables))

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin_user'

    res = client.get('/backup')
    assert res.status_code == 200
    assert res.headers['Content-Type'] == 'application/json'
    assert 'attachment;' in res.headers['Content-Disposition']

    backup_data = json.loads(res.data.decode('utf-8'))
    assert '_metadata' in backup_data
    assert backup_data['_metadata']['version'] == '2.0'
    assert backup_data['_metadata']['is_partial'] is False
    assert 'program_department' in backup_data['data']
    assert backup_data['data']['program_department'] == [{'program_name': 'BSIT', 'department_name': 'CICT'}]
    assert backup_data['data']['room'] == [{'room_id': 1, 'room_name': 'Room 101'}]


def test_backup_database_partial_export(monkeypatch):
    """Test partial backup export via query parameter."""
    fake_tables = {
        'room': [{'room_id': 1, 'room_name': 'Room 101'}],
        'course': [{'course_id': 101, 'course_name': 'Data Structures', 'program': 'BSIT'}],
    }
    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseClient(fake_tables))

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin_user'

    res = client.get('/backup?tables=course,room')
    assert res.status_code == 200

    backup_data = json.loads(res.data.decode('utf-8'))
    assert backup_data['_metadata']['is_partial'] is True
    assert set(backup_data['_metadata']['tables_included']) == {'course', 'room'}
    assert 'course' in backup_data['data']
    assert 'room' in backup_data['data']
    assert 'timeslot' not in backup_data['data']


def test_restore_database_validation(monkeypatch):
    """Test validation errors for invalid file uploads."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'

    # 1. No file selected
    res = client.post('/restore', data={})
    assert res.status_code == 302

    # 2. Non-JSON file
    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(b'DROP TABLE course;'), 'backup.sql')
    })
    assert res.status_code == 302

    # 3. Invalid JSON syntax
    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(b'invalid json content'), 'backup.json')
    })
    assert res.status_code == 302


def test_restore_database_rpc_success(monkeypatch):
    """Test successful restore through the Supabase RPC method."""
    rpc_return = {
        'success': True,
        'total_restored': 3,
        'details': {'course': 1, 'professor': 1, 'program_department': 1}
    }
    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseClient(rpc_result=rpc_return))

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin'

    sample_backup = {
        "_metadata": {"version": "2.0"},
        "data": {
            "program_department": [{"program_name": "BSIT", "department_name": "CICT"}],
            "professor": [{"prof_id": 1, "first_name": "Alice"}],
            "course": [{"course_id": 10, "course_name": "Algorithms"}]
        }
    }

    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(json.dumps(sample_backup).encode('utf-8')), 'backup.json'),
        'clear_existing': 'true'
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b'Database restored successfully' in res.data


def test_restore_database_client_side_fallback(monkeypatch):
    """Test successful restore when falling back to client-side batching if RPC is unavailable."""
    # FakeSupabaseClient without rpc_result triggers fallback to _execute_client_side_restore
    monkeypatch.setattr(app_module, 'supabase', FakeSupabaseClient())

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin'

    sample_backup = {
        "program_department": [{"program_name": "BSIT", "department_name": "CICT"}],
        "professor": [{"prof_id": 1, "first_name": "Alice"}]
    }

    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(json.dumps(sample_backup).encode('utf-8')), 'backup.json'),
        'clear_existing': 'false'
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b'Database restored successfully' in res.data


def test_restore_database_legacy_format_mapping(monkeypatch):
    """Test that legacy backup with professor table and prof_course derives professor_name."""
    fake_client = FakeSupabaseClient()
    monkeypatch.setattr(app_module, 'supabase', fake_client)

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin'

    legacy_backup = {
        "_metadata": {"version": "1.0"},
        "data": {
            "program": [{"id": 1, "program_name": "BSIT"}],
            "professor": [{"prof_id": 5, "first_name": "Maria", "last_name": "Santos"}],
            "prof_course": [{"id": 20, "prof_id": 5, "course_id": 101, "sections": 2}],
            "schedule": [{"schedule_id": 50, "course_id": 101, "prof_course_id": 20, "day": "M"}]
        }
    }

    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(json.dumps(legacy_backup).encode('utf-8')), 'legacy_backup.json'),
        'clear_existing': 'false'
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b'Database restored successfully' in res.data


def test_restore_database_empty_data_rejection(monkeypatch):
    """Test that backup with empty data is rejected and no tables are wiped."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'user-admin-123'
        sess['role'] = 'admin'
        sess['username'] = 'admin'

    empty_backup = {
        "_metadata": {"version": "2.0"},
        "data": {}
    }

    res = client.post('/restore', data={
        'backup_file': (io.BytesIO(json.dumps(empty_backup).encode('utf-8')), 'empty_backup.json')
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b'No valid table data found' in res.data

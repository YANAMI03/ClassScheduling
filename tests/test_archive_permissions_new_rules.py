"""Test suite verifying the new permissions for Schedule Archive.

Covers all rules specified:
1. BSIT scheduler archives their own active schedule directly (no deletion request, no approval).
2. Scheduler cannot see or call permanent delete (HTTP 403 on direct call, RLS blocks in DB).
3. Scheduler restores their own archive; blocked on cross-program conflicts and when an active schedule already exists.
4. Scheduler cannot archive or restore BSDS data (HTTP 403).
5. Admin permanently deletes an archived batch with direct action.
6. Activity log records each action with program, semester, and batch details.
"""

import uuid
import pytest
from bs4 import BeautifulSoup
import app as app_module


class MockQueryBuilder:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self._filters = {}
        self._is_delete = False
        self._is_update = False
        self._update_data = None
        self._insert_data = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def neq(self, col, val):
        self._filters[f"{col}__neq"] = val
        return self

    def in_(self, col, vals):
        self._filters[f"{col}__in"] = list(vals)
        return self

    def is_(self, col, val):
        self._filters[f"{col}__is"] = val
        return self

    def or_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, start, end):
        return self

    def limit(self, count):
        return self

    def update(self, data):
        self._is_update = True
        self._update_data = data
        return self

    def delete(self):
        self._is_delete = True
        return self

    def insert(self, data):
        self._insert_data = data
        return self

    def execute(self):
        table_rows = self.store.setdefault(self.table_name, [])

        if self._is_delete:
            remaining = []
            deleted = []
            for row in table_rows:
                match = True
                for col, val in self._filters.items():
                    if col.endswith('__is') and val == 'null':
                        base_col = col[:-6]
                        if row.get(base_col) is not None:
                            match = False
                    elif col.endswith('__in'):
                        base_col = col[:-4]
                        if row.get(base_col) not in val:
                            match = False
                    elif row.get(col) != val:
                        match = False
                if match:
                    deleted.append(row)
                else:
                    remaining.append(row)
            self.store[self.table_name] = remaining
            return type('Response', (), {'data': deleted})()

        if self._is_update:
            updated = []
            for row in table_rows:
                match = True
                for col, val in self._filters.items():
                    if col.endswith('__is') and val == 'null':
                        base_col = col[:-6]
                        if row.get(base_col) is not None:
                            match = False
                    elif col.endswith('__in'):
                        base_col = col[:-4]
                        if row.get(base_col) not in val:
                            match = False
                    elif row.get(col) != val:
                        match = False
                if match:
                    row.update(self._update_data)
                    updated.append(row)
            return type('Response', (), {'data': updated})()

        # SELECT
        matching = []
        for row in table_rows:
            match = True
            for col, val in self._filters.items():
                if col.endswith('__is') and val == 'null':
                    base_col = col[:-6]
                    if row.get(base_col) is not None:
                        match = False
                elif col.endswith('__in'):
                    base_col = col[:-4]
                    if row.get(base_col) not in val:
                        match = False
                elif row.get(col) != val:
                    match = False
            if match:
                matching.append(row)
        return type('Response', (), {'data': matching})()


class MockSupabase:
    def __init__(self, initial_data=None):
        self.store = initial_data or {}
        self.rpc_calls = []

    def table(self, table_name):
        return MockQueryBuilder(table_name, self.store)

    def rpc(self, func_name, params=None):
        self.rpc_calls.append((func_name, params))
        class RpcResult:
            def execute(self_rpc):
                return type('Response', (), {'data': None})()
        return RpcResult()


@pytest.fixture
def test_setup(monkeypatch):
    logged_activities = []
    def fake_log_activity(action, target_type, target_detail=None):
        logged_activities.append({
            'action': action,
            'target_type': target_type,
            'target_detail': target_detail
        })
    monkeypatch.setattr(app_module, 'log_activity', fake_log_activity)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    return logged_activities


def test_bsit_scheduler_archives_without_a_request(monkeypatch, test_setup):
    """1. BSIT scheduler archives directly: archive=True, archived_at, archive_batch_id, no deletion request."""
    client = app_module.app.test_client()

    store = {
        'schedule': [
            {
                'schedule_id': 10,
                'program_id': 1,
                'archive': False,
                'archived_at': None,
                'archive_batch_id': None,
                'prepared_by_user_id': 'user-111',
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 10,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': False,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.post('/archive_schedule', data={
        'program_id': '1',
        'semester': '1st Semester'
    }, follow_redirects=False)

    assert res.status_code == 302
    assert '/schedules' in res.headers.get('Location', '')

    # Verify schedule table rows were archived with fresh archive_batch_id and timestamp
    row = store['schedule'][0]
    assert row['archive'] is True
    assert row['archived_at'] is not None
    assert row['archive_batch_id'] is not None
    # The preparer user link must remain unchanged
    assert row['prepared_by_user_id'] == 'user-111'

    # Activity log recorded
    assert any(
        log['action'] == 'archive' and log['target_type'] == 'schedule' and 'BSIT' in log['target_detail']
        for log in test_setup
    )


def test_scheduler_cannot_see_or_call_permanent_delete(monkeypatch):
    """2. Scheduler cannot see delete button/modal in UI, and calling delete route returns 403."""
    client = app_module.app.test_client()

    store = {
        'schedule_with_semester': [
            {
                'schedule_id': 10,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': True,
                'archive_batch_id': 'batch-bsit-1',
                'archived_at': '2026-10-01T00:00:00Z',
                'section': '1A',
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # UI check: Delete button and delete modal are absent
    res_ui = client.get('/schedule_archive')
    assert res_ui.status_code == 200
    soup = BeautifulSoup(res_ui.data.decode('utf-8'), 'html.parser')
    assert soup.find('button', class_='delete-batch-btn') is None
    assert soup.find(id='deleteArchiveModal') is None

    # Direct route call check: 403 Forbidden
    res_call = client.post('/delete_schedule_archive/batch-bsit-1', follow_redirects=False)
    assert res_call.status_code == 403


def test_scheduler_restores_own_archive_success(monkeypatch, test_setup):
    """3. Scheduler restores their own archive successfully when no active schedule exists and no conflict."""
    client = app_module.app.test_client()

    batch_uuid = str(uuid.uuid4())
    store = {
        'schedule': [
            {
                'schedule_id': 20,
                'program_id': 1,
                'archive': True,
                'archived_at': '2026-10-01T00:00:00Z',
                'archive_batch_id': batch_uuid,
                'room_id': 1,
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '10:00:00',
                'professor_load_id': 1,
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 20,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': True,
                'archive_batch_id': batch_uuid,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.post(f'/restore_schedule_archive/{batch_uuid}', follow_redirects=False)
    assert res.status_code == 302
    assert '/schedules' in res.headers.get('Location', '')

    # Restored row should have archive=False, archived_at=None, archive_batch_id=None
    row = store['schedule'][0]
    assert row['archive'] is False
    assert row['archived_at'] is None
    assert row['archive_batch_id'] is None

    # Activity log recorded
    assert any(
        log['action'] == 'restore' and log['target_type'] == 'schedule' and batch_uuid in log['target_detail']
        for log in test_setup
    )


def test_scheduler_restore_blocked_when_active_schedule_already_exists(monkeypatch, test_setup):
    """4. Restore is blocked if an active schedule already exists for the same program & semester."""
    client = app_module.app.test_client()

    batch_uuid = str(uuid.uuid4())
    store = {
        'schedule': [
            # Archived row to restore
            {
                'schedule_id': 20,
                'program_id': 1,
                'archive': True,
                'archived_at': '2026-10-01T00:00:00Z',
                'archive_batch_id': batch_uuid,
                'room_id': 1,
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '10:00:00',
                'professor_load_id': 1,
            },
            # Active row currently in place
            {
                'schedule_id': 30,
                'program_id': 1,
                'archive': False,
                'archived_at': None,
                'archive_batch_id': None,
                'room_id': 2,
                'day': 'Tuesday',
                'class_start': '08:00:00',
                'class_end': '10:00:00',
                'professor_load_id': 2,
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 20,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': True,
                'archive_batch_id': batch_uuid,
            },
            {
                'schedule_id': 30,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': False,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.post(f'/restore_schedule_archive/{batch_uuid}', follow_redirects=True)
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Cannot restore schedule: An active schedule already exists' in html

    # Confirm rows were NOT updated or overwritten
    assert store['schedule'][0]['archive'] is True
    assert store['schedule'][1]['archive'] is False


def test_scheduler_restore_blocked_on_cross_program_conflict(monkeypatch):
    """5. Restore is blocked on conflict with active schedule of another program (room or normalized professor)."""
    client = app_module.app.test_client()

    batch_uuid = str(uuid.uuid4())
    store = {
        'schedule': [
            # BSIT archived row to restore: Monday 08:00 - 10:00, room 1
            {
                'schedule_id': 20,
                'program_id': 1,
                'archive': True,
                'archived_at': '2026-10-01T00:00:00Z',
                'archive_batch_id': batch_uuid,
                'room_id': 1,
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '10:00:00',
                'section': 'BSIT-1A',
                'professor_load': {'id': 1, 'professor_name': 'Alan Turing'},
            },
            # BSDS active row: Monday 09:00 - 11:00, room 1 (Room Conflict!)
            {
                'schedule_id': 99,
                'program_id': 9,
                'archive': False,
                'room_id': 1,
                'day': 'Monday',
                'class_start': '09:00:00',
                'class_end': '11:00:00',
                'section': 'BSDS-1A',
                'professor_load': {'id': 9, 'professor_name': 'John von Neumann'},
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 20,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': True,
                'archive_batch_id': batch_uuid,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.post(f'/restore_schedule_archive/{batch_uuid}', follow_redirects=True)
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Conflict with active schedule of another program' in html
    assert 'Room booking conflict' in html

    # Confirm batch was NOT restored
    assert store['schedule'][0]['archive'] is True


def test_scheduler_cannot_archive_or_restore_bsds_data(monkeypatch):
    """6. BSIT Scheduler cannot archive or restore BSDS data (returns HTTP 403)."""
    client = app_module.app.test_client()

    bsds_batch_uuid = str(uuid.uuid4())
    store = {
        'schedule': [
            {
                'schedule_id': 50,
                'program_id': 9,  # BSDS
                'archive': True,
                'archive_batch_id': bsds_batch_uuid,
                'room_id': 5,
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '10:00:00',
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 50,
                'program_id': 9,
                'semester': '1st Semester',
                'program_name': 'BSDS',
                'archive': True,
                'archive_batch_id': bsds_batch_uuid,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'user-1'
        sess['username'] = 'bsit_scheduler'
        sess['role'] = 'Scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # Tampering: BSIT scheduler tries to archive BSDS program
    res_arch = client.post('/archive_schedule', data={
        'program_id': '9',
        'semester': '1st Semester'
    }, follow_redirects=False)
    assert res_arch.status_code == 403

    # Tampering: BSIT scheduler tries to restore BSDS batch
    res_rest = client.post(f'/restore_schedule_archive/{bsds_batch_uuid}', follow_redirects=False)
    assert res_rest.status_code == 403


def test_admin_permanently_deletes_archived_batch(monkeypatch, test_setup):
    """7. Academic Admin / Super Admin can permanently delete an archived batch directly."""
    client = app_module.app.test_client()

    batch_uuid = str(uuid.uuid4())
    store = {
        'schedule': [
            {
                'schedule_id': 88,
                'program_id': 1,
                'archive': True,
                'archive_batch_id': batch_uuid,
            }
        ],
        'schedule_with_semester': [
            {
                'schedule_id': 88,
                'program_id': 1,
                'semester': '1st Semester',
                'program_name': 'BSIT',
                'archive': True,
                'archive_batch_id': batch_uuid,
            }
        ]
    }
    mock_sb = MockSupabase(store)
    monkeypatch.setattr(app_module, 'supabase', mock_sb)

    with client.session_transaction() as sess:
        sess['user_id'] = 'admin-user'
        sess['username'] = 'acad_admin'
        sess['role'] = 'Admin'

    res = client.post(f'/delete_schedule_archive/{batch_uuid}', follow_redirects=False)
    assert res.status_code == 302
    assert '/schedule_archive' in res.headers.get('Location', '')

    # Row is deleted from schedule
    assert len(store['schedule']) == 0

    # Activity log recorded
    assert any(
        log['action'] == 'delete' and log['target_type'] == 'schedule' and batch_uuid in log['target_detail']
        for log in test_setup
    )

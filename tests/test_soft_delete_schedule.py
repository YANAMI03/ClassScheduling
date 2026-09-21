import app as app_module
from unittest.mock import MagicMock


class CallTracker:
    def __init__(self):
        self.updates = []
        self.deletes = []
        self.filters = []


class FakeTrackingQuery:
    def __init__(self, table_name, tracker):
        self.table_name = table_name
        self.tracker = tracker
        self._filters = {}
        self._operation = None
        self._payload = None

    def select(self, *args, **kwargs):
        self._operation = 'select'
        return self

    def eq(self, col, val):
        self._filters[col] = val
        self.tracker.filters.append((self.table_name, col, val))
        return self

    def neq(self, col, val):
        self._filters[f"{col}__neq"] = val
        return self

    def in_(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def update(self, payload):
        self._operation = 'update'
        self._payload = payload
        return self

    def delete(self):
        self._operation = 'delete'
        return self

    def insert(self, *args, **kwargs):
        return self

    def execute(self):
        if self._operation == 'update':
            self.tracker.updates.append({
                'table': self.table_name,
                'payload': self._payload,
                'filters': dict(self._filters)
            })
        elif self._operation == 'delete':
            self.tracker.deletes.append({
                'table': self.table_name,
                'filters': dict(self._filters)
            })

        class Resp:
            def __init__(self, data, count=0):
                self.data = data
                self.count = count

        if self.table_name == 'schedule':
            if self._filters.get('schedule_id') == 42:
                return Resp([{'schedule_id': 42, 'section': 'BSIT-1A', 'semester': '1st Semester', 'archive': False}])
            if self._filters.get('archive') is True:
                return Resp([
                    {'schedule_id': 42, 'section': 'BSIT-1A', 'semester': '1st Semester', 'program': 'BSIT', 'major': None, 'archive': True}
                ])
            return Resp([
                {'schedule_id': 10, 'section': 'BSIT-1A', 'semester': '1st Semester', 'program': 'BSIT', 'major': None, 'archive': False}
            ])
        elif self.table_name == 'delete_requests':
            if self._filters.get('id') == 99:
                return Resp([
                    {
                        'id': 99,
                        'item_type': 'schedule',
                        'item_id': '42',
                        'item_name': 'IT101 - BSIT-1A',
                        'item_details': 'IT101 Schedule Entry',
                        'requester_name': 'Scheduler',
                        'status': 'pending'
                    }
                ], count=0)
            return Resp([], count=0)
        elif self.table_name == 'program_department':
            return Resp({'department_name': 'CICT'})
        return Resp([])


class FakeTrackingSupabase:
    def __init__(self, tracker):
        self.tracker = tracker

    def table(self, table_name):
        return FakeTrackingQuery(table_name, self.tracker)


def test_delete_single_schedule_soft_archives(monkeypatch):
    client = app_module.app.test_client()
    tracker = CallTracker()
    fake_sb = FakeTrackingSupabase(tracker)

    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'Admin'
        sess['program'] = 'BSIT'

    res = client.get('/delete_schedule/42', follow_redirects=False)
    assert res.status_code == 302

    # Verify no DELETE on schedule table
    sched_deletes = [d for d in tracker.deletes if d['table'] == 'schedule']
    assert len(sched_deletes) == 0, f"Expected 0 DELETE operations on schedule table, got: {sched_deletes}"

    # Verify UPDATE archive = True was performed
    sched_updates = [u for u in tracker.updates if u['table'] == 'schedule']
    assert len(sched_updates) > 0
    assert sched_updates[0]['payload'] == {'archive': True}
    assert sched_updates[0]['filters'].get('schedule_id') == 42


def test_delete_section_schedule_soft_archives(monkeypatch):
    client = app_module.app.test_client()
    tracker = CallTracker()
    fake_sb = FakeTrackingSupabase(tracker)

    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'Admin'
        sess['program'] = 'BSIT'

    res = client.post('/delete_section_schedule/BSIT-1A')
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    sched_deletes = [d for d in tracker.deletes if d['table'] == 'schedule']
    assert len(sched_deletes) == 0

    sched_updates = [u for u in tracker.updates if u['table'] == 'schedule']
    assert len(sched_updates) > 0
    assert sched_updates[0]['payload'] == {'archive': True}
    assert sched_updates[0]['filters'].get('section') == 'BSIT-1A'


def test_delete_all_schedules_soft_archives(monkeypatch):
    client = app_module.app.test_client()
    tracker = CallTracker()
    fake_sb = FakeTrackingSupabase(tracker)

    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    # Mock supabase.auth.sign_in_with_password
    fake_auth = MagicMock()
    fake_auth.sign_in_with_password.return_value = MagicMock(session=True)
    fake_sb.auth = fake_auth

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['email'] = 'admin@example.com'
        sess['role'] = 'Admin'
        sess['program'] = 'BSIT'

    res = client.post('/delete_all_schedules', json={'password': 'secret'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    sched_deletes = [d for d in tracker.deletes if d['table'] == 'schedule']
    assert len(sched_deletes) == 0

    sched_updates = [u for u in tracker.updates if u['table'] == 'schedule']
    assert len(sched_updates) > 0
    assert sched_updates[0]['payload'] == {'archive': True}


def test_approve_delete_request_soft_archives_schedule(monkeypatch):
    client = app_module.app.test_client()
    tracker = CallTracker()
    fake_sb = FakeTrackingSupabase(tracker)

    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    res = client.post('/admin/delete_requests/99/approve', follow_redirects=False)
    assert res.status_code == 302

    sched_deletes = [d for d in tracker.deletes if d['table'] == 'schedule']
    assert len(sched_deletes) == 0

    sched_updates = [u for u in tracker.updates if u['table'] == 'schedule']
    assert any(u['payload'] == {'archive': True} for u in sched_updates)

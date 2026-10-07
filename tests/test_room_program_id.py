import pytest
import re
import app as app_module

class MockTable:
    def __init__(self, name, data):
        self.name = name
        self.data = list(data)
        self._filters = {}
        self._order_col = None
        self._order_desc = False
        self._limit_val = None
        self._update_payload = None
        self._is_delete = False
        self.inserted = []
        self.deleted = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def ilike(self, col, val):
        val_clean = val.replace('%', '').lower()
        self._filters[col] = val_clean
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit_val = n
        return self

    def insert(self, payload):
        if isinstance(payload, dict):
            row = dict(payload)
            if 'room_id' not in row:
                row['room_id'] = len(self.data) + 101
            self.inserted.append(row)
            self.data.append(row)
            class InsResp:
                data = [row]
            return InsResp()
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def delete(self):
        self._is_delete = True
        return self

    def execute(self):
        if self._update_payload is not None:
            payload = self._update_payload
            self._update_payload = None
            affected = []
            for row in self.data:
                match = True
                for k, v in self._filters.items():
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if match:
                    row.update(payload)
                    affected.append(dict(row))
            self._filters = {}
            class UpdResp:
                def __init__(self, d):
                    self.data = d
            return UpdResp(affected)

        if self._is_delete:
            self._is_delete = False
            for row in list(self.data):
                match = True
                for k, v in self._filters.items():
                    if str(row.get(k)) != str(v):
                        match = False
                        break
                if match:
                    self.deleted.append(row)
                    self.data.remove(row)
            self._filters = {}
            class DelResp:
                data = []
            return DelResp()

        filtered = list(self.data)
        for k, v in self._filters.items():
            filtered = [r for r in filtered if str(r.get(k, '')).lower() == str(v).lower() or str(v).lower() in str(r.get(k, '')).lower()]
        self._filters = {}

        class Resp:
            def __init__(self, d):
                self.data = d
        return Resp(filtered)


class MockSupabase:
    def __init__(self):
        self.tables = {
            'program': MockTable('program', [
                {'id': 1, 'program_name': 'BSIT'},
                {'id': 2, 'program_name': 'BSBA'},
            ]),
            'room': MockTable('room', [
                {'room_id': 1, 'room_name': 'Lab 101', 'room_type': 'Laboratory Room', 'program_id': 1},
                {'room_id': 2, 'room_name': 'Room 201', 'room_type': 'Lecture Room', 'program_id': 2},
            ]),
            'users': MockTable('users', [
                {'id': 1, 'email': 'dean_it@example.com', 'username': 'dean_it', 'role': 'Dean', 'program_id': 1},
                {'id': 2, 'email': 'admin@example.com', 'username': 'admin', 'role': 'super_admin', 'program_id': None},
            ]),
            'delete_requests': MockTable('delete_requests', []),
            'activity_log': MockTable('activity_log', []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name, [])
        return self.tables[name]


@pytest.fixture
def mock_db(monkeypatch):
    db = MockSupabase()
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)
    return db


def test_rooms_page_shows_no_program_header_common_pool(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_it'
        session['role'] = 'Admin'

    resp = client.get('/rooms')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Must NOT have Program header or Department header in table (rooms are shared common pool)
    assert '<th>Program</th>' not in html
    assert '<th>Department</th>' not in html
    # Must NOT have Program dropdown in Add Room form
    assert 'name="program_id"' not in html
    assert 'name="department"' not in html
    # Table must display room headers
    assert '<th>Room Name</th>' in html
    assert '<th>Type</th>' in html


def test_add_room_saves_room_in_common_pool(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin_it'
        session['role'] = 'Admin'

    resp = client.post('/add_room', data={
        'room_name': 'Room 303',
        'room_type': 'Lecture Room',
    }, follow_redirects=True)

    assert resp.status_code == 200
    inserted = mock_db.tables['room'].inserted
    assert len(inserted) == 1
    new_room = inserted[0]
    assert new_room['room_name'] == 'Room 303'
    assert new_room['room_type'] == 'Lecture Room'
    assert 'department' not in new_room


def test_edit_room_updates_room(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['username'] = 'admin'
        session['role'] = 'super_admin'

    resp = client.post('/edit_room/1', data={
        'room_name': 'Lab 101 Renamed',
        'room_type': 'Laboratory Room',
    }, follow_redirects=True)

    assert resp.status_code == 200
    updated_room = next(r for r in mock_db.tables['room'].data if r['room_id'] == 1)
    assert updated_room['room_name'] == 'Lab 101 Renamed'
    assert updated_room['room_type'] == 'Laboratory Room'


def test_search_rooms_returns_rooms_without_department(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['username'] = 'admin'
        session['role'] = 'super_admin'

    resp = client.get('/search_rooms?q=Lab')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'rooms' in data
    assert len(data['rooms']) >= 1
    room_item = data['rooms'][0]
    assert 'room_name' in room_item
    assert 'department' not in room_item

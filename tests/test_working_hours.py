"""Tests for the renamed working_hours table, endpoints, RBAC, and compatibility."""

import pytest
from bs4 import BeautifulSoup
import app as app_module
from tests.test_rbac_permissions import FakeTable, FakeSupabase, _login_as


class WorkingHoursFakeTable(FakeTable):
    def __init__(self, table_name, data=None):
        super().__init__(table_name, data)
        self._filters = {}

    def select(self, *args, **kwargs):
        clone = WorkingHoursFakeTable(self.table_name, self.data)
        clone._filters = dict(self._filters)
        return clone

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def insert(self, payload):
        if isinstance(payload, list):
            self.data.extend(payload)
        else:
            self.data.append(payload)
        return self

    def update(self, payload):
        for r in self.data:
            match = all(r.get(k) == v for k, v in self._filters.items())
            if match:
                r.update(payload)
        return self

    def delete(self):
        self.data = [r for r in self.data if not all(r.get(k) == v for k, v in self._filters.items())]
        return self

    def execute(self):
        class Resp:
            def __init__(self, d):
                self.data = d
        filtered = list(self.data)
        for k, v in self._filters.items():
            filtered = [r for r in filtered if r.get(k) == v]
        return Resp(filtered)


@pytest.fixture
def wh_client(monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False

    fake_db = FakeSupabase()
    initial_rows = [
        {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'semester_id': 1},
        {'timeslot_id': 2, 'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'semester_id': 1},
        {'timeslot_id': 3, 'day': 'Wednesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'semester_id': 1},
    ]
    wh_table = WorkingHoursFakeTable('working_hours', list(initial_rows))
    fake_db.tables['working_hours'] = wh_table
    fake_db.tables['timeslot'] = wh_table
    fake_db.tables['schedule'] = WorkingHoursFakeTable('schedule', [])

    monkeypatch.setattr(app_module, 'supabase', fake_db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    with app_module.app.test_client() as client:
        yield client, fake_db


def test_admin_working_hours_view(wh_client):
    client, _ = wh_client
    _login_as(client, 'admin')

    res = client.get('/working_hours')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Working Hours' in html
    assert 'Daily Operating Windows & Working Hours' in html
    assert 'Monday' in html
    assert 'Tuesday' in html
    assert 'Wednesday' in html


def test_legacy_timeslot_endpoint_alias(wh_client):
    client, _ = wh_client
    _login_as(client, 'admin')

    # Legacy endpoint should resolve to the same view
    res = client.get('/timeslot')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Working Hours' in html


def test_admin_add_working_hours(wh_client):
    client, fake_db = wh_client
    _login_as(client, 'admin')

    res = client.post('/add_working_hours', data={
        'day': 'Thursday',
        'start_time': '08:00',
        'end_time': '18:00',
        'lunch_time': '12:00',
    }, follow_redirects=True)
    assert res.status_code == 200

    wh_rows = fake_db.tables['working_hours'].data
    assert any(r.get('day') == 'Thursday' for r in wh_rows)


def test_admin_add_timeslot_alias(wh_client):
    client, fake_db = wh_client
    _login_as(client, 'admin')

    res = client.post('/add_timeslot', data={
        'day': 'Friday',
        'start_time': '08:00',
        'end_time': '18:00',
        'lunch_time': '12:00',
    }, follow_redirects=True)
    assert res.status_code == 200

    wh_rows = fake_db.tables['working_hours'].data
    assert any(r.get('day') == 'Friday' for r in wh_rows)


def test_admin_edit_working_hours(wh_client):
    client, fake_db = wh_client
    _login_as(client, 'admin')

    res = client.post('/edit_working_hours/1', data={
        'start_time': '09:00',
        'end_time': '17:00',
        'lunch_time': '13:00',
    }, follow_redirects=True)
    assert res.status_code == 200

    mon_row = next(r for r in fake_db.tables['working_hours'].data if r.get('timeslot_id') == 1)
    assert mon_row['start_time'] == '09:00:00'
    assert mon_row['end_time'] == '17:00:00'


def test_admin_delete_working_hours(wh_client):
    client, fake_db = wh_client
    _login_as(client, 'admin')

    res = client.post('/delete_working_hours/3', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res.status_code == 200

    wh_rows = fake_db.tables['working_hours'].data
    assert not any(r.get('timeslot_id') == 3 for r in wh_rows)


def test_scheduler_and_viewer_blocked(wh_client):
    client, _ = wh_client

    _login_as(client, 'scheduler')
    assert client.get('/working_hours').status_code == 403
    assert client.post('/add_working_hours', data={'day': 'Saturday'}).status_code == 403
    assert client.post('/edit_working_hours/1', data={}).status_code == 403
    assert client.get('/delete_working_hours/1').status_code == 403

    _login_as(client, 'viewer')
    assert client.get('/working_hours').status_code == 403


def test_backup_insert_order_and_pk_configuration():
    assert 'working_hours' in app_module.BACKUP_TABLES_INSERT_ORDER
    assert app_module.BACKUP_PKS.get('working_hours') == 'timeslot_id'

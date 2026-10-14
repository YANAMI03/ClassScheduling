"""Archive guards must consider ACTIVE schedules only.

Covers:
  * a room used only by archived schedules can be archived
  * a room used by the active schedule is blocked, with an "active schedule" message
  * the shared _has_active_schedule helper ignores archive = true rows
  * _archived_room_names drives the restore guard
"""

import app as app_module
from app import archive_room, _has_active_schedule, _archived_room_names


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.filters = []
        self._limit = None
        self._mode = 'select'
        self._payload = None

    def select(self, *a, **k):
        self._mode = 'select'
        return self

    def update(self, payload):
        self._mode = 'update'
        self._payload = payload
        return self

    def insert(self, payload):
        self._mode = 'insert'
        self._payload = payload
        return self

    def eq(self, col, val):
        self.filters.append(('eq', col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(('in', col, list(vals)))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def _apply(self, rows):
        out = list(rows)
        for op, col, val in self.filters:
            if op == 'eq':
                out = [r for r in out if str(r.get(col)) == str(val)]
            else:
                out = [r for r in out if r.get(col) in val]
        return out

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        matching = self._apply(rows)
        if self._mode == 'select':
            if self._limit is not None:
                matching = matching[:self._limit]
            return FakeResponse(matching)
        if self._mode == 'update':
            for r in matching:
                r.update(self._payload)
            return FakeResponse(matching)
        if self._mode == 'insert':
            rows.append(dict(self._payload))
            return FakeResponse([dict(self._payload)])
        return FakeResponse([])


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(self.store, name)


def _install(monkeypatch, store):
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(store))
    monkeypatch.setattr(app_module, '_set_delete_request_status', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'log_activity', lambda *a, **k: None)
    captured = []
    monkeypatch.setattr(app_module, 'flash', lambda msg, cat: captured.append((msg, cat)))
    return captured


def test_has_active_schedule_ignores_archived(monkeypatch):
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase({
        'schedule': [{'schedule_id': 1, 'room_id': 5, 'archive': True}],
    }))
    assert _has_active_schedule(room_id=5) is False

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase({
        'schedule': [{'schedule_id': 2, 'room_id': 5, 'archive': False}],
    }))
    assert _has_active_schedule(room_id=5) is True


def test_archive_room_allowed_with_only_archived_schedules(monkeypatch):
    store = {
        'schedule': [{'schedule_id': 1, 'room_id': 5, 'archive': True}],
        'room': [{'room_id': 5, 'room_name': 'Lab 1', 'archive': False}],
    }
    captured = _install(monkeypatch, store)

    with app_module.app.test_request_context('/archive_room/5', method='POST'):
        archive_room.__wrapped__(5)

    assert store['room'][0]['archive'] is True
    assert not any('Cannot archive room' in msg for msg, _ in captured)


def test_archive_room_blocked_by_active_schedule(monkeypatch):
    store = {
        'schedule': [{'schedule_id': 1, 'room_id': 5, 'archive': False}],
        'room': [{'room_id': 5, 'room_name': 'Lab 1', 'archive': False}],
    }
    captured = _install(monkeypatch, store)

    with app_module.app.test_request_context('/archive_room/5', method='POST'):
        archive_room.__wrapped__(5)

    assert store['room'][0]['archive'] is False
    assert any(
        'Cannot archive room' in msg and 'currently active schedule' in msg
        for msg, _ in captured
    )


def test_archived_room_names_helper(monkeypatch):
    monkeypatch.setattr(app_module, 'supabase', FakeSupabase({
        'room': [
            {'room_id': 5, 'room_name': 'Lab 1', 'archive': True},
            {'room_id': 6, 'room_name': 'Lab 2', 'archive': False},
            {'room_id': 7, 'room_name': 'Lab 3', 'archive': True},
        ],
    }))
    assert _archived_room_names([5, 6, 7]) == ['Lab 1', 'Lab 3']
    assert _archived_room_names([]) == []
    assert _archived_room_names([None, '', 0]) == []

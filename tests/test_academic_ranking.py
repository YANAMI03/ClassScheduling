import pytest
import app as app_module


class MockTable:
    def __init__(self, name, data=None):
        self.name = name
        self.data = data if data is not None else []
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filter_col = None
        self._filter_val = None
        self._filters = {}

    def select(self, *args, **kwargs):
        self._filters = {}
        return self

    def insert(self, payload):
        if isinstance(payload, dict):
            row = payload.copy()
            if 'academic_ranking_id' not in row:
                row['academic_ranking_id'] = len(self.data) + len(self.inserted) + 1
            if 'prof_id' not in row and self.name == 'professor':
                row['prof_id'] = len(self.data) + len(self.inserted) + 1
            self.inserted.append(row)
            self.data.append(row)
            self._last_op = [row]
            self._is_insert = True
        return self

    def update(self, payload):
        self.updated.append((dict(self._filters), payload.copy()))
        for r in self.data:
            match = True
            for k, v in self._filters.items():
                if str(r.get(k)) != str(v):
                    match = False
                    break
            if match:
                r.update(payload)
        self._last_op = [payload]
        return self

    def delete(self):
        to_del = []
        for r in self.data:
            match = True
            for k, v in self._filters.items():
                if str(r.get(k)) != str(v):
                    match = False
                    break
            if match:
                to_del.append(r)
        for r in to_del:
            self.deleted.append(r)
            self.data.remove(r)
        self._last_op = to_del
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def ilike(self, column, value):
        self._filters[column] = value.replace('%', '')
        return self

    def execute(self):
        if getattr(self, '_is_insert', False):
            self._is_insert = False
            class Response:
                def __init__(self, d):
                    self.data = d
            return Response(self._last_op)
        res_data = []
        for r in self.data:
            match = True
            for k, v in self._filters.items():
                rv = str(r.get(k, ''))
                if str(v).lower() not in rv.lower():
                    match = False
                    break
            if match:
                res_data.append(r)

        class Response:
            def __init__(self, d):
                self.data = d

        # Reset filters after query
        self._filters = {}
        return Response(res_data)


class MockSupabase:
    def __init__(self):
        self.tables = {
            'academic_ranking': MockTable('academic_ranking', [
                {'academic_ranking_id': 1, 'name': 'Instructor I', 'units_required': 18, 'program': 'BSIT'},
                {'academic_ranking_id': 2, 'name': 'Assistant Professor', 'units_required': 15, 'program': 'BSIT'},
                {'academic_ranking_id': 3, 'name': 'Instructor I', 'units_required': 21, 'program': 'BSCS'},
            ]),
            'professor': MockTable('professor', [
                {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'department': 'CICT', 'specialization': 'Math', 'academic_ranking_id': 1, 'min_units': 12, 'max_units': 24},
            ]),
            'delete_requests': MockTable('delete_requests', []),
            'program_department': MockTable('program_department', [
                {'program_name': 'BSIT', 'department_name': 'CICT'},
                {'program_name': 'BSCS', 'department_name': 'CICT'},
            ]),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name, [])
        return self.tables[name]


@pytest.fixture
def mock_db(monkeypatch):
    db = MockSupabase()
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_department', lambda *args, **kwargs: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)
    return db


def test_academic_ranking_page_renders_for_scheduler(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    resp = client.get('/academic_ranking')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Academic Ranking' in html
    assert 'Instructor I' in html
    assert 'Assistant Professor' in html
    # BSCS rank should NOT be visible to BSIT scheduler
    assert 'BSCS' not in html or 'BSCS' not in [r['program'] for r in [r for r in mock_db.table('academic_ranking').data if r['program'] == 'BSIT']]


def test_add_academic_ranking_auto_assigns_program(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    # Attempt to pass a different program 'BSCS' from form (should be ignored and forced to BSIT)
    resp = client.post('/add_academic_ranking', data={
        'name': 'Associate Professor 1',
        'units_required': '12',
        'program': 'BSCS',
    }, follow_redirects=True)

    assert resp.status_code == 200
    inserted = mock_db.table('academic_ranking').inserted
    assert len(inserted) > 0
    new_rank = [r for r in inserted if r['name'] == 'Associate Professor 1'][0]
    assert new_rank['program'] == 'BSIT'
    assert new_rank['units_required'] == 12


def test_add_academic_ranking_ajax(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    resp = client.post('/add_academic_ranking', data={
        'name': 'Professor 1',
        'units_required': '9',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['message'] == 'Academic ranking added successfully.'
    assert data['ranking']['program'] == 'BSIT'
    assert data['ranking']['units_required'] == 9


def test_edit_academic_ranking_preserves_program(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    # Rank 1 is BSIT
    resp = client.post('/edit_academic_ranking/1', data={
        'name': 'Instructor I - Updated',
        'units_required': '20',
        'program': 'HACK_PROGRAM',
    }, follow_redirects=True)

    assert resp.status_code == 200
    rank1 = [r for r in mock_db.table('academic_ranking').data if r['academic_ranking_id'] == 1][0]
    assert rank1['name'] == 'Instructor I - Updated'
    assert rank1['units_required'] == 20
    assert rank1['program'] == 'BSIT'


def test_edit_academic_ranking_blocks_other_program(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    # Rank 3 is BSCS, scheduler is BSIT
    resp = client.post('/edit_academic_ranking/3', data={
        'name': 'Tampered',
        'units_required': '15',
    }, follow_redirects=True)

    assert resp.status_code == 200
    rank3 = [r for r in mock_db.table('academic_ranking').data if r['academic_ranking_id'] == 3][0]
    assert rank3['name'] == 'Instructor I'


def test_delete_academic_ranking_scheduler_request(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    resp = client.get('/delete_academic_ranking/1', follow_redirects=True)
    assert resp.status_code == 200
    # Schedulers create a delete request
    del_reqs = mock_db.table('delete_requests').inserted
    assert len(del_reqs) > 0
    assert del_reqs[0]['item_type'] == 'academic_ranking'


def test_delete_academic_ranking_admin_direct(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'admin'
        session['role'] = 'admin'
        session['program'] = 'BSIT'

    resp = client.get('/delete_academic_ranking/1', follow_redirects=True)
    assert resp.status_code == 200
    # Admin directly deletes
    deleted = mock_db.table('academic_ranking').deleted
    assert any(r['academic_ranking_id'] == 1 for r in deleted)


def test_add_professor_with_academic_ranking(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    # Rank 1 is BSIT (allowed)
    resp = client.post('/add_professor', data={
        'first_name': 'Grace',
        'last_name': 'Hopper',
        'department': 'CICT',
        'specialization': 'Compilers',
        'academic_ranking_id': '1',
        'min_units': '12',
        'max_units': '24',
    }, follow_redirects=True)

    assert resp.status_code == 200
    inserted = mock_db.table('professor').inserted
    assert len(inserted) > 0
    new_p = [p for p in inserted if p['first_name'] == 'Grace'][0]
    assert new_p['academic_ranking_id'] == 1


def test_add_professor_rejects_mismatched_program_ranking(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    # Rank 3 is BSCS, scheduler is BSIT (mismatch)
    resp = client.post('/add_professor', data={
        'first_name': 'Charles',
        'last_name': 'Babbage',
        'department': 'CICT',
        'specialization': 'Hardware',
        'academic_ranking_id': '3',
        'min_units': '12',
        'max_units': '24',
    }, follow_redirects=True)

    assert resp.status_code == 200
    inserted = [p for p in mock_db.table('professor').inserted if p.get('first_name') == 'Charles']
    assert len(inserted) == 0


def test_api_academic_rankings_endpoint(mock_db):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 10
        session['username'] = 'sched_it'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'

    resp = client.get('/api/academic_rankings')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'academic_rankings' in data
    # Should only return BSIT rankings
    names = [r['name'] for r in data['academic_rankings']]
    assert 'Assistant Professor' in names

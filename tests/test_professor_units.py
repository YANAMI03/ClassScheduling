import app as app_module
from postgrest.exceptions import APIError


class FakeTable:
    def __init__(self, simulate_missing_column=False):
        self.inserted = []
        self.updated = []
        self.simulate_missing_column = simulate_missing_column
        self._is_filter = False

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        if self.simulate_missing_column and ('min_units' in data or 'max_units' in data):
            raise APIError({'message': 'column professor.min_units does not exist', 'code': '42703'})
        self.inserted.append(data.copy())
        return self

    def update(self, data):
        if self.simulate_missing_column and ('min_units' in data or 'max_units' in data):
            raise APIError({'message': 'column professor.min_units does not exist', 'code': '42703'})
        self.updated.append(data.copy())
        return self

    def eq(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
        self._is_filter = True
        return self

    def execute(self):
        if self._is_filter:
            self._is_filter = False
            class EmptyResponse:
                data = []
            return EmptyResponse()

        class Response:
            data = [
                {
                    'prof_id': 1,
                    'first_name': 'Alan',
                    'last_name': 'Turing',
                    'department': 'CICT',
                    'specialization': 'Computer Science',
                    'min_units': 12,
                    'max_units': 24,
                }
            ]
        return Response()


class FakeSupabase:
    def __init__(self, simulate_missing_column=False):
        self.table_obj = FakeTable(simulate_missing_column=simulate_missing_column)

    def table(self, name):
        return self.table_obj


def _build_client(monkeypatch, simulate_missing_column=False):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin_user'
        session['role'] = 'admin'
        session['department'] = 'CICT'

    fake_supabase = FakeSupabase(simulate_missing_column=simulate_missing_column)
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    return client, fake_supabase


def test_add_professor_stores_min_and_max_units(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)


    response = client.post('/add_professor', data={
        'first_name': 'Grace',
        'last_name': 'Hopper',
        'department': 'CICT',
        'specialization': 'Programming',
        'min_units': '15',
        'max_units': '21',
    })

    assert response.status_code == 302
    assert len(fake_supabase.table_obj.inserted) > 0
    ins = fake_supabase.table_obj.inserted[0]
    assert ins['min_units'] == 15
    assert ins['max_units'] == 21
    assert ins['specialization'] == 'Programming'


def test_add_professor_ajax_returns_units(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)


    response = client.post(
        '/add_professor',
        data={
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'department': 'CICT',
            'specialization': 'Algorithms',
            'min_units': '18',
            'max_units': '27',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'}
    )

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['message'] == 'Professor added successfully.'
    prof = json_data['professor']
    assert prof['min_units'] == 18
    assert prof['max_units'] == 27
    assert prof['specialization'] == 'Algorithms'


def test_edit_professor_updates_min_and_max_units(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/edit_professor/1', data={
        'first_name': 'Alan',
        'last_name': 'Turing',
        'department': 'CICT',
        'specialization': 'Cryptography',
        'min_units': '9',
        'max_units': '18',
    })

    assert response.status_code == 302
    assert len(fake_supabase.table_obj.updated) > 0
    upd = fake_supabase.table_obj.updated[0]
    assert upd['min_units'] == 9
    assert upd['max_units'] == 18
    assert upd['specialization'] == 'Cryptography'


def test_professors_page_renders_unit_inputs_and_columns(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.get('/professors')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="min_units"' in html
    assert 'name="max_units"' in html
    assert '<th>Minimum Units</th>' in html
    assert '<th>Maximum Units</th>' in html
    assert 'data-minunits="12"' in html
    assert 'data-maxunits="24"' in html


def test_add_and_edit_fallback_when_column_missing(monkeypatch):
    # Simulates DB where migration hasn't been run yet (42703 column does not exist)
    client, fake_supabase = _build_client(monkeypatch, simulate_missing_column=True)

    # 1. Add professor fallback
    response = client.post('/add_professor', data={
        'first_name': 'Linus',
        'last_name': 'Torvalds',
        'department': 'CICT',
        'specialization': 'Operating Systems',
        'min_units': '12',
        'max_units': '24',
    })
    assert response.status_code == 302
    assert len(fake_supabase.table_obj.inserted) > 0
    assert 'min_units' not in fake_supabase.table_obj.inserted[0]

    # 2. Edit professor fallback
    response = client.post('/edit_professor/1', data={
        'first_name': 'Linus',
        'last_name': 'Torvalds',
        'department': 'CICT',
        'specialization': 'Operating Systems',
        'min_units': '12',
        'max_units': '24',
    })
    assert response.status_code == 302
    assert len(fake_supabase.table_obj.updated) > 0
    assert 'min_units' not in fake_supabase.table_obj.updated[0]

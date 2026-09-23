import app as app_module


class FakeTable:
    def __init__(self):
        self.inserted = []
        self.updated = []
        self._is_filter = False

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        self.inserted.append(data.copy())
        return self

    def update(self, data):
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
            data = [{
                'prof_id': 1,
                'first_name': 'Alan',
                'last_name': 'Turing',
                'department': 'CICT',
                'specialization': 'Computer Science',
            }]
        return Response()


class FakeSupabase:
    def __init__(self):
        self.table_obj = FakeTable()

    def table(self, name):
        return self.table_obj


def _build_client(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin_user'
        session['role'] = 'admin'
        session['department'] = 'CICT'

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_supabase)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    return client, fake_supabase


def test_add_professor_does_not_store_unit_limits(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/add_professor', data={
        'first_name': 'Grace',
        'last_name': 'Hopper',
        'department': 'CICT',
        'specialization': 'Programming',
        'academic_ranking_id': '1',
        'min_units': '15',
        'max_units': '21',
    })

    assert response.status_code == 302
    inserted = fake_supabase.table_obj.inserted[0]
    assert 'min_units' not in inserted
    assert 'max_units' not in inserted
    assert inserted['specialization'] == 'Programming'


def test_edit_professor_does_not_update_unit_limits(monkeypatch):
    client, fake_supabase = _build_client(monkeypatch)

    response = client.post('/edit_professor/1', data={
        'first_name': 'Alan',
        'last_name': 'Turing',
        'department': 'CICT',
        'specialization': 'Cryptography',
        'academic_ranking_id': '1',
        'min_units': '9',
        'max_units': '18',
    })

    assert response.status_code == 302
    updated = fake_supabase.table_obj.updated[0]
    assert 'min_units' not in updated
    assert 'max_units' not in updated
    assert updated['specialization'] == 'Cryptography'


def test_professors_page_has_ranking_but_no_unit_limit_inputs(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.get('/professors')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Academic Ranking' in html
    assert 'name="min_units"' not in html
    assert 'name="max_units"' not in html
    assert '<th>Minimum Units</th>' not in html
    assert '<th>Maximum Units</th>' not in html
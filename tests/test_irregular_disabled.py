import app as app_module


def _build_client(monkeypatch, session_data=None):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        if session_data:
            session.update(session_data)

    return client


def test_irregular_students_route_disabled(monkeypatch):
    client = _build_client(monkeypatch)

    # Direct GET to /irregular_students should return 404
    resp = client.get('/irregular_students')
    assert resp.status_code == 404

    # Direct POST to /irregular_students should return 404
    resp = client.post('/irregular_students', data={
        'student_id_number': '12345',
        'first_name': 'John',
        'last_name': 'Doe',
        'program': 'BSIT'
    })
    assert resp.status_code == 404


def test_irregular_subroutes_disabled(monkeypatch):
    client = _build_client(monkeypatch)

    assert client.get('/delete_irregular_student/1').status_code == 404
    assert client.get('/irregular_students/1/schedule').status_code == 404
    assert client.get('/irregular_students/1/view_schedule').status_code == 404
    assert client.post('/irregular_students/1/assign_section', json={'course_id': 1, 'section': 'A'}).status_code == 404
    assert client.post('/irregular_students/1/unassign_section/1').status_code == 404


def test_unauthenticated_access_returns_404():
    client = app_module.app.test_client()

    assert client.get('/irregular_students').status_code == 404
    assert client.get('/irregular_students/1/schedule').status_code == 404
    assert client.get('/irregular_students/1/view_schedule').status_code == 404
    assert client.get('/delete_irregular_student/1').status_code == 404


def test_sidebar_does_not_contain_irregular_link():
    with open('templates/base.html', 'r', encoding='utf-8') as f:
        content = f.read()

    assert '/irregular_students' not in content
    assert 'Irregular Students' not in content

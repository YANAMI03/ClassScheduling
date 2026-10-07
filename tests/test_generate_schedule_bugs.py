import pytest
import os
import re
from unittest.mock import patch, MagicMock
import app as app_module
from app import app, _check_has_professor_loads


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    with app.test_client() as c:
        yield c


def test_check_has_professor_loads_returns_true_when_count_positive():
    mock_res = MagicMock()
    mock_res.count = 42
    mock_res.data = [{'id': 1}]

    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = mock_res

    with patch.object(app_module.supabase, 'table', return_value=mock_query):
        result = _check_has_professor_loads()
        assert result is True


def test_check_has_professor_loads_returns_false_when_zero():
    mock_res = MagicMock()
    mock_res.count = 0
    mock_res.data = []

    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = mock_res

    with patch.object(app_module.supabase, 'table', return_value=mock_query):
        result = _check_has_professor_loads()
        assert result is False


def test_check_has_professor_loads_failsafe_on_exception():
    mock_query = MagicMock()
    mock_query.select.side_effect = Exception("Supabase connection error")

    with patch.object(app_module.supabase, 'table', return_value=mock_query):
        result = _check_has_professor_loads()
        assert result is True  # Failsafe avoids false-positive warning banner


def test_api_has_professor_loads_permissions(client):
    # 1. Unauthenticated -> 401 Unauthorized for API route
    res = client.get('/api/has_professor_loads')
    assert res.status_code == 401

    # 2. Scheduler role -> 200 OK
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'sched_user'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1

    with patch('app._check_has_professor_loads', return_value=True):
        res = client.get('/api/has_professor_loads')
        assert res.status_code == 200
        data = res.get_json()
        assert data == {'ok': True, 'has_loads': True}

    with patch('app._check_has_professor_loads', return_value=False):
        res = client.get('/api/has_professor_loads')
        assert res.status_code == 200
        data = res.get_json()
        assert data == {'ok': True, 'has_loads': False}


def test_home_page_banner_hidden_when_loads_exist(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'sched_user'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1

    with patch('app._check_has_professor_loads', return_value=True):
        res = client.get('/')
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        # When loads exist, banner element must have style="display: none;"
        assert 'id="no-loads-banner"' in html
        assert bool(re.search(r'id="no-loads-banner"[^>]*style="display:\s*none;"', html)) is True


def test_home_page_banner_visible_when_no_loads(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'sched_user'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1

    with patch('app._check_has_professor_loads', return_value=False):
        res = client.get('/')
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert 'id="no-loads-banner"' in html
        # When no loads exist, banner element must NOT be hidden
        assert bool(re.search(r'id="no-loads-banner"[^>]*style="display:\s*none;"', html)) is False
        assert 'No professor loads yet.' in html


def test_generate_schedule_get_passes_has_professor_loads(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'sched_user'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1
        sess['schedule_preview'] = []

    with patch('app._check_has_professor_loads', return_value=True), \
         patch('app._build_preview_context', return_value={}):
        res = client.get('/generate_schedule')
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert bool(re.search(r'id="no-loads-banner"[^>]*style="display:\s*none;"', html)) is True


def test_preview_schedule_omits_no_loads_banner(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'sched_user'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1
        sess['schedule_preview'] = [{'section': 'BSIT 1-A', 'course_id': 1}]

    with patch('app._check_has_professor_loads', return_value=True), \
         patch('app._build_preview_context', return_value={}):
        res = client.get('/generate_schedule')
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        # In preview mode, no-loads-banner is completely omitted from the DOM
        assert 'id="no-loads-banner"' not in html
        assert 'No professor loads yet.' not in html


def test_base_html_does_not_attach_beforeunload():
    base_html_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'base.html')
    with open(base_html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Verify beforeunload listener is NOT added
    assert "addEventListener('beforeunload'" not in content, (
        "beforeunload event listener must not be added in base.html"
    )
    assert 'addEventListener("beforeunload"' not in content

    # Verify hideGenOverlay restores buttons
    assert 'hideGenOverlay:' in content
    assert "setButtonLoading(btn, false)" in content

    # Verify pageshow restores buttons and hides overlay
    assert "window.addEventListener('pageshow'" in content
    assert "AppLoader.hideGenOverlay()" in content


def test_index_html_has_dynamic_banner_refresh():
    index_html_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'index.html')
    with open(index_html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Verify banner ID
    assert 'id="no-loads-banner"' in content
    # Verify refreshProfessorLoadsBanner function and listeners
    assert 'refreshProfessorLoadsBanner' in content
    assert '/api/has_professor_loads' in content
    assert "window.addEventListener('pageshow'" in content

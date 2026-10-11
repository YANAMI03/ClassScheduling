import pytest
from bs4 import BeautifulSoup
import app as app_module


@pytest.fixture
def client():
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key-landing'
    return app.test_client()


def test_anonymous_get_landing_page_returns_200_and_content(client, monkeypatch):
    """
    Anonymous visit to '/' returns 200, contains H1 text, all sections,
    and makes ZERO database calls.
    """
    db_calls = []

    def mock_table(name):
        db_calls.append(f"table:{name}")
        raise AssertionError(f"Landing page should NOT call database table '{name}'!")

    def mock_rpc(name, *args, **kwargs):
        db_calls.append(f"rpc:{name}")
        raise AssertionError(f"Landing page should NOT call RPC '{name}'!")

    # Guard against any unexpected database interaction
    monkeypatch.setattr(app_module.supabase, 'table', mock_table)
    if hasattr(app_module.supabase, 'rpc'):
        monkeypatch.setattr(app_module.supabase, 'rpc', mock_rpc)

    res = client.get('/', follow_redirects=False)
    assert res.status_code == 200
    assert len(db_calls) == 0, "Expected 0 database calls on public landing page"

    html = res.get_data(as_text=True)
    soup = BeautifulSoup(html, 'html.parser')

    # 1. H1 Heading and Title
    h1 = soup.find('h1')
    assert h1 is not None
    h1_text = h1.get_text()
    assert 'Conflict-free class schedules' in h1_text
    assert 'generated in seconds.' in h1_text
    assert 'ClassScheduling | NEUST CICT' in soup.title.string

    # 2. Hero copy and elements
    page_text = soup.get_text()
    assert 'NEUST' in page_text and 'College of ICT' in page_text
    assert 'Import professor loads and let the system place every section, room, and time slot' in page_text
    assert 'Sign in to get started' in page_text
    assert 'See how it works' in page_text

    # 3. Static Timetable Illustration Card
    assert 'BSIT' in page_text and 'Section 3A' in page_text
    assert '2nd Semester' in page_text
    assert 'IT-WS03' in page_text
    assert 'CC-104' in page_text
    assert 'IT-NET03' in page_text
    assert 'IT-IM02' in page_text
    assert 'IT-WS04' in page_text
    assert 'IT-IAS02' in page_text
    assert 'IT-NET04' in page_text
    assert 'IT-CAP01' in page_text
    assert 'IT-WS05' in page_text
    assert 'Laboratory' in page_text
    assert 'Lecture' in page_text
    assert 'No conflicts detected' in page_text

    # 4. Features Section (#features)
    features_sec = soup.find('section', id='features')
    assert features_sec is not None
    assert 'Everything a department needs to schedule' in features_sec.get_text()
    assert 'From faculty loads to a signed, printable timetable.' in features_sec.get_text()
    assert 'Automated generation' in features_sec.get_text()
    assert 'Conflict detection' in features_sec.get_text()
    assert 'Multi-program ready' in features_sec.get_text()
    assert 'Excel and CSV import' in features_sec.get_text()
    assert 'Official PDF export' in features_sec.get_text()
    assert 'Archive and restore' in features_sec.get_text()

    # 5. How It Works Section (#workflow)
    workflow_sec = soup.find('section', id='workflow')
    assert workflow_sec is not None
    assert 'Three steps to a finished schedule' in workflow_sec.get_text()
    assert 'Set up' in workflow_sec.get_text()
    assert 'Import loads' in workflow_sec.get_text()
    assert 'Generate and confirm' in workflow_sec.get_text()

    # 6. Closing CTA & Footer
    assert "Ready to build this semester's schedule?" in page_text
    assert 'Sign in with your account to get started.' in page_text
    assert 'NEUST CICT' in page_text and 'Class Scheduling and Room Allocation System' in page_text

    # 7. Navigation and Sign In links point to /login
    login_links = soup.find_all('a', href='/login')
    assert len(login_links) >= 2, "Expected at least 2 Sign in links leading to /login"


def test_authenticated_users_redirected_to_role_home(client):
    """
    Authenticated Admin, Viewer, and Scheduler visiting '/' are redirected
    to their respective role home pages.
    """
    # 1. Admin -> Section Schedule (/schedules)
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 'admin-uuid-1'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    res_admin = client.get('/', follow_redirects=False)
    assert res_admin.status_code == 302
    assert res_admin.location == '/schedules'

    # 2. Super Admin -> Section Schedule (/schedules)
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 'superadmin-uuid-1'
        sess['role'] = 'super_admin'
        sess['email'] = 'superadmin@example.com'

    res_sa = client.get('/', follow_redirects=False)
    assert res_sa.status_code == 302
    assert res_sa.location == '/schedules'

    # 3. Viewer -> Section Schedule (/schedules)
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 'viewer-uuid-1'
        sess['role'] = 'viewer'
        sess['email'] = 'viewer@example.com'

    res_viewer = client.get('/', follow_redirects=False)
    assert res_viewer.status_code == 302
    assert res_viewer.location == '/schedules'

    # 4. Scheduler -> Generate Schedule (/home)
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 'scheduler-uuid-1'
        sess['role'] = 'scheduler'
        sess['email'] = 'scheduler@example.com'
        sess['program_id'] = 1

    res_sched = client.get('/', follow_redirects=False)
    assert res_sched.status_code == 302
    assert res_sched.location == '/home'


def test_anonymous_visit_to_home_redirects_to_login(client):
    """
    Anonymous visitor trying to access '/home' (Generate Schedule)
    is redirected to '/login' by RBAC.
    """
    with client.session_transaction() as sess:
        sess.clear()

    res = client.get('/home', follow_redirects=False)
    assert res.status_code == 302
    assert res.location == '/login'

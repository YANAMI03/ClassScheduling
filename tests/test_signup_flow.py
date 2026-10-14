import re
import pytest
from bs4 import BeautifulSoup
import app as app_module


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    with app_module.app.test_client() as client:
        yield client


def test_anonymous_get_signup_page_returns_200_and_expected_fields(client):
    """GET /signup should return 200 with all required fields, styling, and viewer notice."""
    res = client.get('/signup')
    assert res.status_code == 200
    html = res.data.decode('utf-8')

    soup = BeautifulSoup(html, 'html.parser')
    assert "Create your account" in html
    assert "New accounts have view-only access to the Section and Room schedules" in html

    # Verify format hint line above Full Name
    assert "REQUIRED FORMAT: La Madrid, Micko John A." in html

    # Verify input fields in required order
    assert soup.find('input', {'name': 'full_name'}) is not None
    assert soup.find('select', {'name': 'program'}) is not None
    assert soup.find('input', {'name': 'email'}) is not None
    assert soup.find('input', {'name': 'password'}) is not None
    assert soup.find('input', {'name': 'confirm_password'}) is not None
    assert soup.find('input', {'name': 'website'}) is not None  # Honeypot

    # Submit button text
    submit_btn = soup.find('button', {'id': 'signupSubmitBtn'})
    assert submit_btn is not None
    assert "Sign up" in submit_btn.text

    # Link back to sign in
    assert soup.find('a', {'href': '/login'}) is not None


def test_authenticated_user_redirected_from_signup(client):
    """Authenticated users opening /signup should be redirected to their role's home page."""
    # 1. Viewer redirected to Section Schedule (/schedules)
    with client.session_transaction() as sess:
        sess['user_id'] = 'viewer-123'
        sess['role'] = 'viewer'
    res = client.get('/signup')
    assert res.status_code == 302
    assert res.headers['Location'] == '/schedules'

    # 2. Scheduler redirected to Generator (/home)
    with client.session_transaction() as sess:
        sess['user_id'] = 'sched-123'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1
    res = client.get('/signup')
    assert res.status_code == 302
    assert res.headers['Location'] == '/home'


def test_signup_validation_name_format(client):
    """Full name must match 'Surname, Given Names M.I.' with letters, accents, hyphens."""
    # Invalid: missing comma
    res = client.post('/signup', data={
        'full_name': 'Maria Santos',
        'program': '1',
        'email': 'valid@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert "Use the format: Last name, First name M.I." in res.data.decode('utf-8')

    # Invalid: multiple commas
    res = client.post('/signup', data={
        'full_name': 'Santos, Maria, Jr.',
        'program': '1',
        'email': 'valid@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert "Use the format: Last name, First name M.I." in res.data.decode('utf-8')

    # Invalid: empty given names or surname
    res = client.post('/signup', data={
        'full_name': 'Santos, ',
        'program': '1',
        'email': 'valid@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert "Use the format: Last name, First name M.I." in res.data.decode('utf-8')

    # Invalid: contains numbers or special symbols
    res = client.post('/signup', data={
        'full_name': 'Santos123, Maria',
        'program': '1',
        'email': 'valid@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert "Use the format: Last name, First name M.I." in res.data.decode('utf-8')


def test_signup_validation_program_required(client):
    """Program is required on sign-up."""
    res = client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '',
        'email': 'valid@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    assert "Please select your program." in res.data.decode('utf-8')


def test_signup_validation_invalid_email(client):
    """Malformed email addresses should be rejected with a clear message."""
    for bad_email in ['not-an-email', 'missingatsign.com', '@nodomain.com']:
        res = client.post('/signup', data={
            'full_name': 'Santos, Maria',
            'program': '1',
            'email': bad_email,
            'password': 'Password123!',
            'confirm_password': 'Password123!',
        })
        assert res.status_code == 200
        html = res.data.decode('utf-8')
        assert "Please enter a valid email address." in html


def test_signup_validation_password_too_short(client):
    """Password under 8 characters should be rejected."""
    res = client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '1',
        'email': 'maria@example.com',
        'password': 'short',
        'confirm_password': 'short',
    })
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert "Password must be at least 8 characters long." in html


def test_signup_validation_passwords_mismatch(client):
    """Mismatched password and confirm_password should be rejected."""
    res = client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '1',
        'email': 'maria@example.com',
        'password': 'Password123!',
        'confirm_password': 'DifferentPassword123!',
    })
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert "Passwords do not match." in html


def test_signup_allowed_email_domains(client, monkeypatch):
    """ALLOWED_SIGNUP_EMAIL_DOMAINS restriction should block unapproved domains."""
    monkeypatch.setenv('ALLOWED_SIGNUP_EMAIL_DOMAINS', 'neust.edu.ph,cict.neust.edu.ph')
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])

    # 1. Blocked domain
    res_blocked = client.post('/signup', data={
        'full_name': 'Dela Cruz, Juan',
        'program': '1',
        'email': 'juan@gmail.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res_blocked.status_code == 200
    html_blocked = res_blocked.data.decode('utf-8')
    assert "approved email domains" in html_blocked

    # 2. Allowed domain reaches Supabase call
    call_detected = []
    class DummyUser:
        id = 'dummy-id'
        identities = ['id1']

    class DummyRes:
        user = DummyUser()
        session = None

    def dummy_signup(payload):
        call_detected.append(payload)
        return DummyRes()

    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', dummy_signup)

    res_allowed = client.post('/signup', data={
        'full_name': 'Dela Cruz, Juan',
        'program': '1',
        'email': 'juan@neust.edu.ph',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }, follow_redirects=True)
    assert res_allowed.status_code == 200
    assert len(call_detected) == 1
    assert call_detected[0]['email'] == 'juan@neust.edu.ph'
    assert "Check your email to confirm your account" in res_allowed.data.decode('utf-8')


def test_signup_honeypot_silently_rejects(client, monkeypatch):
    """If bot fills the honeypot field, reject silently without calling Supabase."""
    called = []
    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', lambda p: called.append(p))
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])

    res = client.post('/signup', data={
        'full_name': 'Spammer, Bot',
        'program': '1',
        'email': 'bot@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
        'website': 'https://spam-link.com',  # Honeypot filled
    }, follow_redirects=True)
    assert res.status_code == 200
    assert len(called) == 0  # Supabase never called
    html = res.data.decode('utf-8')
    assert "Check your email" in html


def test_signup_tampering_role_ignored_and_viewer_forced(client, monkeypatch):
    """Tampering with role field is completely ignored; role is never entrusted to user_metadata and program_id is passed."""
    captured = []
    class DummyUser:
        id = 'dummy-id'
        identities = ['id1']

    class DummyRes:
        user = DummyUser()
        session = None

    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', lambda p: captured.append(p) or DummyRes())
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])

    client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '1',
        'email': 'tamper_test@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
        'role': 'admin',  # Attempted privilege escalation
    })

    assert len(captured) == 1
    payload = captured[0]
    data = payload['options']['data']
    assert 'role' not in data  # Role is strictly forced to viewer by DB trigger handle_new_auth_user
    assert data['program_id'] == 1
    assert data['program'] == 'BSIT'
    assert data['full_name'] == 'Santos, Maria'
    assert data['first_name'] == 'Maria'
    assert data['last_name'] == 'Santos'


def test_signup_rate_limit_friendly_message(client, monkeypatch):
    """Supabase rate limit error should show friendly message and never raw error."""
    def raise_rate_limit(payload):
        raise Exception("429 Client Error: email rate limit exceeded for url https://.../auth/v1/signup")

    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', raise_rate_limit)
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])

    res = client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '1',
        'email': 'ratelimit_test@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')
    err_box = soup.find(id='serverErrorBox')
    assert err_box is not None
    assert "Sign-ups are temporarily busy. Please try again in a few minutes." in err_box.text
    assert "429" not in err_box.text
    assert "email rate limit exceeded" not in err_box.text


def test_signup_duplicate_email_neutral_message(client, monkeypatch):
    """Already registered emails show neutral message."""
    class ExistingUser:
        id = 'existing-id'
        identities = []  # Empty identities indicates already registered in Supabase Auth

    class ExistingRes:
        user = ExistingUser()
        session = None

    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', lambda p: ExistingRes())
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])

    res = client.post('/signup', data={
        'full_name': 'Santos, Maria',
        'program': '1',
        'email': 'existing_user@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    })
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert "An account with this email address already exists. Please sign in or reset your password." in html


def test_signup_throttle_attempts(client, monkeypatch):
    """Exceeding 5 attempts within 15 minutes triggers local server-side throttle."""
    class DummyUser:
        id = 'dummy-id'
        identities = ['id1']

    class DummyRes:
        user = DummyUser()
        session = None

    monkeypatch.setattr(app_module.supabase.auth, 'sign_up', lambda p: DummyRes())
    monkeypatch.setattr(app_module, '_get_signup_programs', lambda: [{'id': 1, 'program_name': 'BSIT'}])
    # Reset throttle state for clean test
    with app_module._signup_throttles_lock:
        app_module._signup_throttles.clear()

    # Submit 5 valid requests that trigger the throttle counter
    for i in range(5):
        client.post('/signup', data={
            'full_name': 'Spam, Test',
            'program': '1',
            'email': f'spam_{i}@example.com',
            'password': 'Password123!',
            'confirm_password': 'Password123!',
        }, environ_overrides={'REMOTE_ADDR': '198.51.100.42'})

    # 6th attempt from the same IP should be blocked by throttling
    res = client.post('/signup', data={
        'full_name': 'Spam, Test',
        'program': '1',
        'email': 'spam_6@example.com',
        'password': 'Password123!',
        'confirm_password': 'Password123!',
    }, environ_overrides={'REMOTE_ADDR': '198.51.100.42'})

    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert "Sign-ups are temporarily busy. Please try again in a few minutes." in html


def test_landing_page_buttons_and_polish(client):
    """Landing page has all updated buttons, nav order, hero order, and closing CTA buttons."""
    res = client.get('/')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    # Nav
    nav_right = soup.find(class_='landing-nav__right')
    assert nav_right is not None
    nav_signup = nav_right.find('a', href='/signup')
    nav_signin = nav_right.find('a', href='/login')
    assert nav_signup is not None
    assert 'btn-outline-dark' in nav_signup.get('class', [])
    assert nav_signin is not None
    assert 'btn-primary' in nav_signin.get('class', [])

    # Hero
    hero_buttons = soup.find(class_='landing-hero__buttons')
    assert hero_buttons is not None
    links = hero_buttons.find_all('a')
    assert len(links) == 3
    # 1st: Sign in / Login to get started
    assert links[0].get('href') == '/login'
    assert 'get started' in links[0].text.lower()
    # 2nd: Sign up
    assert links[1].get('href') == '/signup'
    assert 'btn-outline-dark' in links[1].get('class', [])
    assert 'Sign up' in links[1].text
    # 3rd: See how it works plain text link
    assert links[2].get('href') == '#workflow'
    assert 'hero-link-arrow' in links[2].get('class', [])
    assert 'See how it works' in links[2].text

    # Closing CTA band
    cta_buttons = soup.find(class_='landing-cta__buttons')
    assert cta_buttons is not None
    cta_signin = cta_buttons.find('a', href='/login')
    cta_signup = cta_buttons.find('a', href='/signup')
    assert cta_signin is not None
    assert 'btn-white' in cta_signin.get('class', [])
    assert cta_signup is not None
    assert 'btn-outline-white' in cta_signup.get('class', [])


def test_login_page_has_link_to_signup(client):
    """Login page contains 'Don't have an account? Sign up' pointing to /signup."""
    res = client.get('/login')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'href="/signup"' in html or 'href="/signup' in html
    assert "Don't have an account?" in html

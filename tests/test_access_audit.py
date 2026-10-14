"""Automated ACCESS AUDIT.

It walks the whole Flask ``url_map`` for every role in the matrix (Admin, Chair,
Viewer, anonymous, and a user with an unknown/legacy role) and asserts that
*every* permission failure uses one of the two approved responses and that the
bare Werkzeug "Forbidden" page never appears anywhere:

    page navigation -> the styled 403 page (sidebar visible, "Access Denied")
    AJAX/API/export/import/write -> JSON {"ok": false, "error": "Access denied"}

It also spot-checks the role matrix so a regression that widens or narrows the
Chair's access (relative to the former Scheduler) fails loudly.

The Supabase client is replaced with a permissive in-memory fake so no real
database is ever touched.
"""
import re
import time

import pytest

import app as app_module
from roles import ROLE_ADMIN, ROLE_CHAIR, ROLE_VIEWER, normalize_role

WERKZEUG_FORBIDDEN_TEXT = "You don't have the permission to access the requested resource"

# The exact two approved denial shapes.
JSON_DENIAL = {'ok': False, 'error': 'Access denied'}


# ── Fake Supabase (never touches a real database) ──────────────────────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class _FakeQuery:
    def __init__(self, table):
        self.table = table

    def __getattr__(self, name):
        # Any fluent PostgREST builder method is a no-op returning self.
        def _chain(*args, **kwargs):
            return self
        return _chain

    def select(self, *args, **kwargs):
        return self

    def execute(self):
        return _Resp([])

    def __iter__(self):
        return iter([])


class _FakeAuth:
    def auth(self, *args, **kwargs):
        return None

    def get_user(self, *args, **kwargs):
        return None

    def sign_up(self, *args, **kwargs):
        return None

    def sign_in_with_password(self, *args, **kwargs):
        return None


class _FakeSupabase:
    def __init__(self):
        self.postgrest = _FakeAuth()
        self.auth = _FakeAuth()

    def table(self, name):
        return _FakeQuery(name)

    def rpc(self, name, params=None):
        return _FakeQuery('rpc:' + str(name))


@pytest.fixture
def audit_client(monkeypatch):
    app_module.app.config['TESTING'] = True
    monkeypatch.setattr(app_module, 'supabase', _FakeSupabase())
    monkeypatch.setattr(app_module, 'log_activity', lambda *a, **k: None)
    # Never let background magic or real helpers reach out.
    monkeypatch.setattr(app_module, '_get_department', lambda *a, **k: 'CICT')
    with app_module.app.test_client() as client:
        yield client


def _pin_role(client, role, program_id=1, program='BSIT', anonymous=False):
    """Set the session and pin the live role so the middleware cannot override it."""
    with client.session_transaction() as sess:
        sess.clear()
        if anonymous:
            return
        sess['user_id'] = f'audit-{role or "unknown"}'
        sess['username'] = f'audit-{role or "unknown"}'
        sess['role'] = role
        sess['program'] = program
        sess['program_id'] = program_id
        sess['jwt_token'] = 'fake-jwt'
    if not anonymous:
        app_module._LIVE_USER_ROLE_UPDATES[str(sess.get('user_id'))] = {
            'role': role,
            'program_id': program_id,
            'program': program,
            'disabled': False,
            'timestamp': time.time(),
        }


def _fill_rule(rule):
    """Return a concrete path for a rule, or None when it cannot be built."""
    path = rule.rule
    # Simple substitution for the converters used in this app.
    path = re.sub(r'<int:[^>]+>', '1', path)
    path = re.sub(r'<uuid:[^>]+>', '00000000-0000-0000-0000-000000000000', path)
    path = re.sub(r'<path:[^>]+>', 'test', path)
    path = re.sub(r'<[^>]+>', 'test', path)
    if '<' in path or '>' in path:
        return None
    return path


def _expects_json(path, method, xhr=False):
    """Mirror of app._is_ajax_or_write_request for the audit assertions."""
    lower = path.lower()
    if lower.startswith('/api/') or xhr:
        return True
    if '/import/' in lower or lower.startswith('/admin/delete_requests/'):
        return True
    if lower.startswith('/search_'):
        return True
    if lower.startswith('/backup') or lower.startswith('/restore'):
        return True
    if lower.startswith('/export') or '/export' in lower or lower.endswith(('.pdf', '.xlsx', '.csv', '.json')):
        return True
    if method not in ('GET', 'HEAD'):
        return True
    if any(lower.startswith(p) for p in ('/delete_', '/archive_', '/edit_', '/add_')):
        return True
    return False


def _assert_standard_denial(resp, path, method, xhr=False):
    raw = resp.get_data()
    # The raw Werkzeug 403 text must NEVER appear in any response body.
    assert WERKZEUG_FORBIDDEN_TEXT.encode('utf-8') not in raw, (
        f"Raw Werkzeug 403 text leaked from {method} {path}")
    if resp.status_code != 403:
        return
    text = raw.decode('utf-8', errors='ignore')
    content_type = (resp.headers.get('Content-Type') or '').lower()
    if _expects_json(path, method, xhr=xhr):
        assert 'application/json' in content_type, (
            f"{method} {path} expected JSON 403, got {content_type}")
        body = resp.get_json()
        assert isinstance(body, dict), f"{method} {path} JSON 403 had no object body"
        assert body.get('ok') is False, f"{method} {path} JSON 403 missing ok=false"
        assert body.get('error') == 'Access denied', (
            f"{method} {path} JSON 403 error must be 'Access denied', got {body.get('error')!r}")
    else:
        assert 'text/html' in content_type, (
            f"{method} {path} expected the styled 403 page, got {content_type}")
        assert 'Access Denied' in text, f"{method} {path} styled 403 page missing its title"


# ── 1. Every 403 in the whole app uses one of the two approved formats ─────────
def test_every_endpoint_denial_uses_an_approved_format(audit_client):
    rules = sorted(
        (r for r in app_module.app.url_map.iter_rules() if r.endpoint != 'static'),
        key=lambda r: r.rule,
    )
    checked = 0
    for role in (ROLE_ADMIN, ROLE_CHAIR, ROLE_VIEWER, None, 'wizard'):
        for rule in rules:
            path = _fill_rule(rule)
            if not path:
                continue
            anonymous = role is None
            _pin_role(audit_client, role, anonymous=anonymous)
            methods = (rule.methods or set()) - {'HEAD', 'OPTIONS'}
            for method in sorted(methods):
                if method == 'GET':
                    resp = audit_client.get(path)
                    _assert_standard_denial(resp, path, method)
                    # An AJAX GET must also get JSON, never HTML.
                    resp_xhr = audit_client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
                    _assert_standard_denial(resp_xhr, path, method, xhr=True)
                else:
                    resp = audit_client.open(path, method=method,
                                             headers={'X-Requested-With': 'XMLHttpRequest'})
                    _assert_standard_denial(resp, path, method, xhr=True)
                checked += 1
    assert checked > 50, "The access audit did not enumerate enough endpoints"


# ── 2. The styled 403 page renders for every role (incl. unknown) ──────────────
def test_styled_403_renders_for_every_role(audit_client):
    # /courses is Admin-only navigation -> styled page for the others.
    for role in (ROLE_ADMIN, ROLE_CHAIR, ROLE_VIEWER, 'wizard'):
        _pin_role(audit_client, role)
        resp = audit_client.get('/courses')
        if role == ROLE_ADMIN:
            assert resp.status_code != 403
            continue
        assert resp.status_code == 403
        text = resp.get_data(as_text=True)
        assert 'text/html' in (resp.headers.get('Content-Type') or '')
        assert 'Access Denied' in text
        assert WERKZEUG_FORBIDDEN_TEXT not in text
        # Sidebar + a back-home button must be present.
        assert 'sidebar' in text.lower()
        assert 'btn-back-home' in text


# ── 3. Anonymous keeps the login redirect ─────────────────────────────────────
def test_anonymous_redirected_to_login(audit_client):
    _pin_role(audit_client, None, anonymous=True)
    for path in ['/schedules', '/courses', '/users', '/professor_load', '/home', '/schedule_archive']:
        resp = audit_client.get(path)
        assert resp.status_code == 302, f"{path} should redirect anonymous users"
        assert '/login' in resp.headers.get('Location', '')


# ── 4. Role matrix spot checks (Chair == former Scheduler) ────────────────────
CHAIR_ALLOWED_PAGES = [
    '/home',
    '/professor_load',
    '/schedules',
    '/professor_schedule',
    '/room_schedule',
    '/schedule_archive',
]
CHAIR_FORBIDDEN_PAGES = [
    '/courses',
    '/rooms',
    '/working_hours',
    '/programs',
    '/users',
    '/activity_log',
]
ADMIN_FORBIDDEN_PAGES = ['/home', '/professor_load']


def test_chair_matrix_pages(audit_client):
    _pin_role(audit_client, ROLE_CHAIR)
    for path in CHAIR_ALLOWED_PAGES:
        resp = audit_client.get(path)
        assert resp.status_code != 403, f"Chair must be allowed on {path}, got {resp.status_code}"
    for path in CHAIR_FORBIDDEN_PAGES:
        resp = audit_client.get(path)
        assert resp.status_code == 403, f"Chair must be denied on {path}, got {resp.status_code}"
        assert 'Access Denied' in resp.get_data(as_text=True)


def test_admin_matrix_pages(audit_client):
    _pin_role(audit_client, ROLE_ADMIN)
    for path in ['/schedules', '/professor_schedule', '/room_schedule', '/schedule_archive']:
        assert audit_client.get(path).status_code != 403, f"Admin must read {path}"
    for path in ADMIN_FORBIDDEN_PAGES:
        assert audit_client.get(path).status_code == 403, f"Admin must be denied {path}"


def test_viewer_matrix_pages(audit_client):
    _pin_role(audit_client, ROLE_VIEWER)
    for path in ['/schedules', '/room_schedule']:
        assert audit_client.get(path).status_code != 403, f"Viewer must read {path}"
    for path in ['/schedule_archive', '/professor_load', '/courses', '/users']:
        assert audit_client.get(path).status_code == 403, f"Viewer must be denied {path}"
    # Generate Schedule (/home) is not a Viewer page. The app keeps the historic
    # behaviour of redirecting a Viewer to their Section Schedule instead of
    # showing the 403 page, which still means "no Generate Schedule access".
    resp_home = audit_client.get('/home')
    assert resp_home.status_code in (302, 403), f"Viewer /home must not render Generate Schedule"
    if resp_home.status_code == 302:
        assert '/schedules' in resp_home.headers.get('Location', '')


# ── 5. Chair write endpoints allowed, Admin/Viewer denied (JSON) ───────────────
CHAIR_WRITE_ENDPOINTS = [
    ('POST', '/restore_schedule_archive/batch-1'),
    ('POST', '/archive_schedule'),
]


def test_chair_write_endpoints_are_not_denied(audit_client):
    _pin_role(audit_client, ROLE_CHAIR)
    for method, path in CHAIR_WRITE_ENDPOINTS:
        resp = audit_client.open(path, method=method,
                                 headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code != 403, f"Chair must not be denied on {method} {path}"


def test_admin_and_viewer_cannot_write_schedules(audit_client):
    for role in (ROLE_ADMIN, ROLE_VIEWER):
        _pin_role(audit_client, role)
        for method, path in CHAIR_WRITE_ENDPOINTS:
            resp = audit_client.open(path, method=method,
                                     headers={'X-Requested-With': 'XMLHttpRequest'})
            assert resp.status_code == 403, f"{role} must be denied on {method} {path}"
            assert resp.get_json().get('error') == 'Access denied'


# ── 6. Admin-reserved endpoints are denied for Chair (no old Dean/Chair leak) ──
ADMIN_RESERVED = [
    ('POST', '/restore'),
    ('GET', '/backup'),
    ('POST', '/create_user'),
    ('POST', '/edit_user/some-id'),
    ('POST', '/delete_user/some-id'),
    ('POST', '/add_course'),
    ('POST', '/edit_course/1'),
    ('POST', '/add_room'),
    ('POST', '/edit_room/1'),
    ('POST', '/add_working_hours'),
    ('POST', '/edit_working_hours/1'),
]


def test_chair_denied_admin_reserved_endpoints(audit_client):
    _pin_role(audit_client, ROLE_CHAIR)
    for method, path in ADMIN_RESERVED:
        resp = audit_client.open(path, method=method,
                                 headers={'X-Requested-With': 'XMLHttpRequest'})
        # A removed route (404) is acceptable; a 403 must be the standard JSON.
        assert resp.status_code in (403, 404, 405), (
            f"Chair must not reach {method} {path}, got {resp.status_code}")
        if resp.status_code == 403:
            assert resp.get_json().get('error') == 'Access denied'


def test_permanent_archive_delete_denied_for_everyone(audit_client):
    """Permanent deletion of archive batches is reserved: nobody (incl. Chair) may do it."""
    for role in (ROLE_ADMIN, ROLE_CHAIR, ROLE_VIEWER):
        _pin_role(audit_client, role)
        resp = audit_client.post('/delete_schedule_archive/batch-1',
                                 headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 403
        assert resp.get_json().get('error') == 'Access denied'


# ── 7. Legacy 'scheduler' session still behaves exactly like Chair ─────────────
def test_legacy_scheduler_session_maps_to_chair(audit_client):
    assert normalize_role('scheduler') == ROLE_CHAIR
    _pin_role(audit_client, 'scheduler')
    assert audit_client.get('/home').status_code != 403
    assert audit_client.get('/courses').status_code == 403


# ── 8. Backup & Restore role sanitisation (old files, unknown roles) ───────────
def test_backup_role_sanitisation():
    from roles import role_for_restore, sanitize_stored_role
    # Old backups stored the former role name -> restore as 'chair'.
    assert role_for_restore('scheduler') == ROLE_CHAIR
    assert role_for_restore('Scheduler') == ROLE_CHAIR
    assert role_for_restore('dean') == ROLE_CHAIR
    assert role_for_restore('chair') == ROLE_CHAIR
    assert role_for_restore('admin') == ROLE_ADMIN
    assert role_for_restore('super_admin') == ROLE_ADMIN
    assert role_for_restore('viewer') == ROLE_VIEWER
    # A backup can NEVER introduce a role outside the whitelist.
    assert role_for_restore('wizard') == ROLE_VIEWER
    assert role_for_restore(None) == ROLE_VIEWER
    assert sanitize_stored_role('nonsense') == ROLE_VIEWER


def test_client_side_restore_sanitises_user_roles(audit_client, monkeypatch):
    inserted = {}

    class _UsersQuery(_FakeQuery):
        def upsert(self, payload, on_conflict=None):
            inserted['users'] = payload
            return self

        def insert(self, payload):
            inserted['users'] = payload
            return self

    class _Sb(_FakeSupabase):
        def table(self, name):
            if name == 'users':
                return _UsersQuery(name)
            return _FakeQuery(name)

    monkeypatch.setattr(app_module, 'supabase', _Sb())
    rows = [
        {'id': 'u1', 'role': 'scheduler', 'program_id': 1},
        {'id': 'u2', 'role': 'wizard', 'program_id': 3},
        {'id': 'u3', 'role': 'admin', 'program_id': 2},
    ]
    app_module._execute_client_side_restore({'users': rows}, clear_existing=False)
    restored = {r['id']: r for r in inserted['users']}
    assert restored['u1']['role'] == ROLE_CHAIR
    assert restored['u1']['program_id'] == 1
    assert restored['u2']['role'] == ROLE_VIEWER
    assert restored['u2']['program_id'] is None
    assert restored['u3']['role'] == ROLE_ADMIN
    assert restored['u3']['program_id'] is None


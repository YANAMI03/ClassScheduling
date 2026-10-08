import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from app import app

client = app.test_client()

# 1. Unauthenticated -> redirects to /login
resp = client.get('/', follow_redirects=False)
assert resp.status_code == 302, f"Expected 302 redirect, got {resp.status_code}"
assert '/login' in resp.headers.get('Location', '')

# 2. Check /login rendered HTML
resp = client.get('/login')
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'index.js' in html
assert 'btn-primary' in html
print("[OK] /login rendered with index.js")

# 3. Scheduler session
with client.session_transaction() as sess:
    sess['user'] = {'id': 'test-sched-id', 'email': 'scheduler@test.com'}
    sess['role'] = 'scheduler'
    sess['user_id'] = 'test-sched-id'
    sess['program'] = 'BSIT'

resp = client.get('/', follow_redirects=True)
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'setButtonLoading' in html
assert 'schedule-submit' in html
print("[OK] Scheduler home page rendered with setButtonLoading")

resp = client.get('/schedules', follow_redirects=True)
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'save-schedule-btn' in html
print("[OK] Scheduler /schedules rendered with save-schedule-btn")

# 4. Admin session
with client.session_transaction() as sess:
    sess['user'] = {'id': 'test-admin-id', 'email': 'admin@test.com'}
    sess['role'] = 'admin'
    sess['user_id'] = 'test-admin-id'

resp = client.get('/users', follow_redirects=True)
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'btnSubmitCreateUser' in html
assert 'btnSubmitSaveUser' in html
print("[OK] Admin /users rendered with updated create/save user buttons")

# 5. Scheduler Archive Management
with client.session_transaction() as sess:
    sess['user'] = {'id': 'test-sched-id', 'email': 'scheduler@test.com'}
    sess['role'] = 'scheduler'
    sess['user_id'] = 'test-sched-id'
    sess['program'] = 'BSIT'

resp = client.get('/schedule_archive', follow_redirects=True)
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'confirmRestoreBtn' in html
print("[OK] Scheduler /schedule_archive rendered with confirmRestoreBtn")

# 5. Viewer session (read-only)
with client.session_transaction() as sess:
    sess['user'] = {'id': 'test-viewer-id', 'email': 'viewer@test.com'}
    sess['role'] = 'viewer'
    sess['user_id'] = 'test-viewer-id'

resp = client.get('/schedules', follow_redirects=True)
assert resp.status_code == 200
html = resp.get_data(as_text=True)
assert 'Apply Filters' in html
print("[OK] Viewer /schedules rendered successfully")

print("\nALL ROLE-BASED FLOW VERIFICATIONS PASSED!")

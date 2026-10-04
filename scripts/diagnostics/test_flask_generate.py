import os
import sys
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

# Create Flask test client
test_client = app.app.test_client()

# Authenticate supabase with admin credentials
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

response = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
print("Response status:", response.status_code)

with test_client.session_transaction() as sess:
    unscheduled = sess.get('unscheduled_loads', [])
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))
    print(f"\nUnscheduled loads in session: {len(unscheduled)}")
    for u in unscheduled:
        print(f"| {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")
    print(f"\nPreview entries count: {len(preview)}")

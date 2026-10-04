import os
import sys
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

test_client = app.app.test_client()

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

response = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)

with test_client.session_transaction() as sess:
    unscheduled = sess.get('unscheduled_loads', [])
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

print(f"Total preview entries: {len(preview)}")
print(f"Total unscheduled rows: {len(unscheduled)}")
for u in unscheduled:
    print(f"| {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")

for prof_id, name in [(19, 'Tambio'), (34, 'Santos'), (35, 'Corpuz')]:
    entries = [e for e in preview if e.get('prof_id') == prof_id]
    print(f"\n=== {name} (prof_id {prof_id}) in PREVIEW ===")
    print(f"Total sessions placed: {len(entries)}")
    for e in entries:
        print(f"  LoadID: {e.get('professor_load_id')} | {e['course_name']} | Sec: {e['section']} | {e['session_type']} | {e['day']} {e['start']} - {e['end']} | Room: {e['room_name']}")

import os
import sys
import time
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

test_client = app.app.test_client()
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

runs = []
for i in range(1, 4):
    with test_client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1
        sess['access_token'] = auth_res.session.access_token

    t0 = time.time()
    resp = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
    t1 = time.time()

    with test_client.session_transaction() as sess:
        unscheduled = sess.get('unscheduled_loads', [])
        preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

    runs.append((t1 - t0, len(preview), unscheduled))
    print(f"Run {i}: Time={t1 - t0:.2f}s, Placed={len(preview)}, Unscheduled rows={len(unscheduled)}")
    for u in unscheduled:
        print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")

identical = (runs[0][1] == runs[1][1] == runs[2][1] and runs[0][2] == runs[1][2] == runs[2][2])
print(f"\nDeterministic across 3 runs: {identical}")
print(f"Total preview entries placed: {runs[0][1]}")
total_required_in_unscheduled = sum(u['required'] for u in runs[0][2])
total_placed_in_unscheduled = sum(u['placed'] for u in runs[0][2])
print(f"Unscheduled loads: Required={total_required_in_unscheduled}, Placed={total_placed_in_unscheduled}, Missing={total_required_in_unscheduled - total_placed_in_unscheduled}")

import os
import sys
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

test_client = app.app.test_client()

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

print("=== TESTING DETERMINISM (3 RUNS) ===")
results = []
for run_idx in range(1, 4):
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
        
    print(f"\n--- RUN {run_idx} ---")
    print(f"Status: {response.status_code}, Preview entries: {len(preview)}, Unscheduled count: {len(unscheduled)}")
    run_rows = []
    for u in unscheduled:
        row_str = f"{u['professor']} | {u['course']} | {u['section']} | {u['placed']}/{u['required']} | {u['reason']}"
        run_rows.append(row_str)
        print("  ", row_str)
    results.append((len(preview), run_rows))

# Check if all 3 runs produced the exact same results
all_identical = (results[0] == results[1] == results[2])
print(f"\nAre all 3 runs identical? {all_identical}")

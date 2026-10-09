import os
import sys
import time
import dotenv
from collections import defaultdict

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT_DIR)
dotenv.load_dotenv(os.path.join(ROOT_DIR, '.env'))

import app

# Set up DB call counter
db_call_count = 0
def instrument_supabase():
    global db_call_count, orig_execute
    import postgrest._sync.request_builder as rb
    orig_execute = rb.SyncQueryRequestBuilder.execute
    def counted_execute(self, *args, **kwargs):
        global db_call_count
        db_call_count += 1
        return orig_execute(self, *args, **kwargs)
    rb.SyncQueryRequestBuilder.execute = counted_execute

instrument_supabase()

print("=" * 70)
print("PHASE TIMING AND DATABASE PROFILE: BSIT 2nd Semester Generation")
print("=" * 70)

total_start = time.perf_counter()

# Auth
auth_start = time.perf_counter()
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)
auth_time = time.perf_counter() - auth_start
print(f"Auth completed in {auth_time:.4f}s (DB calls: {db_call_count})")

db_calls_before_val = db_call_count
val_start = time.perf_counter()
calc_result = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
val_time = time.perf_counter() - val_start
val_calls = db_call_count - db_calls_before_val
print(f"Phase: Validation (calculate_semester_section_counts): {val_time:.4f}s (DB calls: {val_calls})")
print(f"  Valid: {calc_result.get('valid')}, Errors: {calc_result.get('errors')}")

# Full generation via Flask test client
test_client = app.app.test_client()
with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1

db_calls_before_gen = db_call_count
gen_start = time.perf_counter()
res = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
gen_time = time.perf_counter() - gen_start
gen_calls = db_call_count - db_calls_before_gen

total_time = time.perf_counter() - total_start

print(f"\nPhase: Generation execution (/generate_schedule): {gen_time:.4f}s (DB calls: {gen_calls})")
print(f"Total end-to-end time: {total_time:.4f}s | Total DB calls: {db_call_count}")

with test_client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))
    unscheduled = sess.get('unscheduled_loads', [])
    print(f"\nResults Summary:")
    print(f"  Preview entries generated: {len(preview)}")
    sections = sorted(set(e.get('section') for e in preview))
    print(f"  Unique sections ({len(sections)}): {sections}")
    print(f"  Unscheduled loads count: {len(unscheduled)}")
    if unscheduled:
        for u in unscheduled:
            print(f"    - {u.get('course')} ({u.get('section')}): {u.get('placed')}/{u.get('required')} | {u.get('reason')}")

print("=" * 70)

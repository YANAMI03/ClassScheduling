import json, dotenv, sys, os
sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# 1. Canonical baseline mapping
with open('scripts/diagnostics/canonical_load_order.json', 'r', encoding='utf-8') as f:
    canon = json.load(f)

canonical_order_map = {}
for item in canon:
    canonical_order_map[(item['professor_key'], item['course_id'])] = item['index']
    canonical_order_map[(item['prof_id'], item['course_id'])] = item['index']

app._CACHED_BASELINE_LOAD_ORDER = canonical_order_map

# 2. Patch timeslots fallback to the exact canonical system configuration
canonical_timeslots = [
    {'day': 'Monday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '16:00:00'},
    {'day': 'Tuesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
    {'day': 'Wednesday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
    {'day': 'Thursday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
    {'day': 'Friday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00', 'professor_cutoff': '17:00:00'},
]

orig_bcs = app._build_candidate_slots
app._build_candidate_slots = lambda ts: orig_bcs(canonical_timeslots)

client = app.app.test_client()
with client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

resp = client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
with client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))
    unscheduled = sess.get('unscheduled_loads', [])

print(f"Total preview placed: {len(preview)}, unscheduled: {len(unscheduled)}")
for u in unscheduled:
    print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")

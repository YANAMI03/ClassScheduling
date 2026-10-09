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

# 2. Patch timeslots fallback to 20:00:00 (7:00 AM - 8:00 PM)
orig_bcs = app._build_candidate_slots
def patched_bcs(ts):
    ts_fixed = []
    for d in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
        ts_fixed.append({
            'day': d,
            'start_time': '07:00:00',
            'end_time': '20:00:00',
            'lunch_time': '12:00:00',
            'professor_cutoff': '17:00:00' if d != 'Monday' else '16:00:00'
        })
    return orig_bcs(ts_fixed)

app._build_candidate_slots = patched_bcs

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

baseline = json.load(open(r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'))['preview_summary']

b_by_sec = {}
for p in baseline:
    b_by_sec.setdefault(p['section'], []).append(p)

cur_by_sec = {}
for p in preview:
    cur_by_sec.setdefault(p['section'], []).append(p)

sec_order = []
for p in baseline:
    if p['section'] not in sec_order:
        sec_order.append(p['section'])

first_diff_sec = None
for sec in sec_order:
    b_slots = sorted([(p['day'], p['start'], p['course'], p['room']) for p in b_by_sec.get(sec, [])])
    c_slots = sorted([(p['day'], p['start'], p['course_name'], p.get('room_name')) for p in cur_by_sec.get(sec, [])])
    if b_slots != c_slots:
        print(f"First section with difference: {sec} (baseline count={len(b_slots)}, current count={len(c_slots)})")
        first_diff_sec = sec
        print("  Baseline slots for", sec, ":")
        for s in b_slots:
            print("   ", s)
        print("  Current slots for", sec, ":")
        for s in c_slots:
            print("   ", s)
        break

if not first_diff_sec:
    print("ALL SECTIONS MATCH BASELINE EXACTLY!")

import json

import dotenv, sys, os
sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with open('scripts/diagnostics/canonical_load_order.json', 'r', encoding='utf-8') as f:
    canon = json.load(f)

canonical_order_map = {}
for item in canon:
    canonical_order_map[(item['professor_key'], item['course_id'])] = item['index']
    canonical_order_map[(item['prof_id'], item['course_id'])] = item['index']

app._CACHED_BASELINE_LOAD_ORDER = canonical_order_map

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

olipas = [p for p in preview if 'Olipas' in p['professor_name']]
print("OLIPAS CLASSES IN CURRENT PREVIEW:")
for p in sorted(olipas, key=lambda x: (x['day'], x['start'])):
    print(f"  {p['day']:<10} {p['start']:<8} - {p['end']:<8} | {p['course_name']:<12} | Sec: {p['section']:<8} | Room: {p.get('room_name')}")

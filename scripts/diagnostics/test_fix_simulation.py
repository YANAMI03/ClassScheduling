import os
import sys
import json
from collections import Counter
from datetime import timedelta
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Load canonical order
with open('scripts/diagnostics/canonical_load_order.json', 'r', encoding='utf-8') as f:
    canon = json.load(f)

canonical_order_map = {}
for item in canon:
    canonical_order_map[(item['professor_key'], item['course_id'])] = item['index']
    canonical_order_map[(item['prof_id'], item['course_id'])] = item['index']

print(f"Loaded {len(canonical_order_map)} canonical map entries.")

# Patch app._get_baseline_professor_load_order
app._CACHED_BASELINE_LOAD_ORDER = canonical_order_map

# Test generation via Flask test_client
test_client = app.app.test_client()

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

resp = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)

with test_client.session_transaction() as sess:
    unscheduled = sess.get('unscheduled_loads', [])
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

print(f"Placed={len(preview)}, Unscheduled count={len(unscheduled)}")
for u in unscheduled:
    print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")

import os
import sys
import json
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# 1. Load canonical order
with open('scripts/diagnostics/canonical_load_order.json', 'r', encoding='utf-8') as f:
    canon = json.load(f)

canonical_order_map = {}
for item in canon:
    canonical_order_map[(item['professor_key'], item['course_id'])] = item['index']
    canonical_order_map[(item['prof_id'], item['course_id'])] = item['index']

app._CACHED_BASELINE_LOAD_ORDER = canonical_order_map

# 2. Monkey-patch supabase.table('working_hours').select('*').execute() to return empty or return 20:00:00
# But in app.py line 10056, let's see what happens if we monkey-patch the timeslot query to return 20:00:00
orig_table = app.supabase.table

class MockTable:
    def __init__(self, table_name):
        self.table_name = table_name
        self._target = orig_table(table_name)
    def __getattr__(self, item):
        return getattr(self._target, item)
    def select(self, *args, **kwargs):
        if self.table_name == 'working_hours':
            class MockExec:
                def execute(self):
                    class MockData:
                        data = [
                            {
                                'day': d,
                                'start_time': '07:00:00',
                                'end_time': '20:00:00',
                                'lunch_time': '12:00:00',
                                'professor_cutoff': '17:00:00' if d != 'Monday' else '16:00:00'
                            }
                            for d in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                        ]
                    return MockData()
            return MockExec()
        return self._target.select(*args, **kwargs)

app.supabase.table = MockTable

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

print(f"RESULTS WITH 20:00:00 AND CANONICAL LOAD ORDER:")
print(f"Placed={len(preview)}, Unscheduled count={len(unscheduled)}")
for u in unscheduled:
    print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")

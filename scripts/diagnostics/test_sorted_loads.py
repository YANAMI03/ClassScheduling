import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Load backup
with open('backups/professor_load_backup_20261003_191218.json') as f:
    backup_data = json.load(f)

backup_pls = backup_data.get('professor_load_rows', [])
# Map (prof_id, course_id) -> original index in backup
backup_order = {}
for idx, r in enumerate(backup_pls):
    backup_order[(r['prof_id'], r['course_id'])] = idx

print("Testing with pc_data sorted by backup order...")

test_client = app.app.test_client()

# Monkey-patch pc_res in app.generate_schedule by intercepting supabase.table('professor_load').select
orig_table = app.supabase.table

class PatchedTable:
    def __init__(self, table_name):
        self.table_name = table_name
        self.orig = orig_table(table_name)
    def __getattr__(self, name):
        return getattr(self.orig, name)
    def select(self, *args, **kwargs):
        res = self.orig.select(*args, **kwargs)
        if self.table_name == 'professor_load':
            orig_exec = res.execute
            def patched_execute(*a, **kw):
                r = orig_exec(*a, **kw)
                if r.data:
                    # Sort r.data by backup order if present
                    r.data.sort(key=lambda row: backup_order.get((row.get('prof_id'), row.get('course_id')), 9999))
                return r
            res.execute = patched_execute
        return res

with app.app.test_client() as client:
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1
        sess['access_token'] = auth_res.session.access_token


    app.supabase.table = PatchedTable
    try:
        resp = client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=False)
        with client.session_transaction() as sess:
            unscheduled = sess.get('unscheduled_loads', [])
            preview_id = sess.get('preview_id')
            preview = app._get_preview_for_user(user_id=1, preview_id=preview_id)
            print(f"\nRESULTS WITH BACKUP LOAD ORDER:")
            print(f"Status: {resp.status_code}, Preview entries: {len(preview)}, Unscheduled: {len(unscheduled)}")
            for u in unscheduled:
                print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")
    finally:
        app.supabase.table = orig_table

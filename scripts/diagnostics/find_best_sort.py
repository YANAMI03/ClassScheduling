import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with open('backups/professor_load_backup_20261003_191218.json') as f:
    backup_data = json.load(f)
backup_pls = backup_data.get('professor_load_rows', [])
backup_order = {(r['prof_id'], r['course_id']): idx for idx, r in enumerate(backup_pls)}

all_pls = app.supabase.table('professor_load').select('prof_id, course_id, sections').execute().data or []
prof_total_sections = {}
for pl in all_pls:
    pid = pl.get('prof_id')
    sec_cnt = int(pl.get('sections') or 1)
    prof_total_sections[pid] = prof_total_sections.get(pid, 0) + sec_cnt

# Check what the actual difference between backup_order and other sort keys is
print("Comparing course 50 (IT-IAS02):")
c50_backup = [r for r in backup_pls if r['course_id'] == 50]
print("  Backup:", [(r['prof_id'], r['sections']) for r in c50_backup])

print("Comparing course 51 (IT-CAP01):")
c51_backup = [r for r in backup_pls if r['course_id'] == 51]
print("  Backup:", [(r['prof_id'], r['sections']) for r in c51_backup])

print("Comparing course 60 (IT-NET05):")
c60_backup = [r for r in backup_pls if r['course_id'] == 60]
print("  Backup:", [(r['prof_id'], r['sections']) for r in c60_backup])

print("Comparing course 59 (IT-NET04):")
c59_backup = [r for r in backup_pls if r['course_id'] == 59]
print("  Backup:", [(r['prof_id'], r['sections']) for r in c59_backup])

# Now test generation with different candidate sort keys
def run_test_with_sort(sort_fn, label):
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
                    if r.data and 'sections' in str(args):
                        r.data.sort(key=sort_fn)
                    return r
                res.execute = patched_execute
            return res

    app.supabase.table = PatchedTable
    try:
        with app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['role'] = 'scheduler'
                sess['program'] = 'BSIT'
                sess['program_id'] = 1
                sess['access_token'] = auth_res.session.access_token

            resp = client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=False)
            with client.session_transaction() as sess:
                unscheduled = sess.get('unscheduled_loads', [])
                preview_id = sess.get('preview_id')
                preview = app._get_preview_for_user(user_id=1, preview_id=preview_id)
                print(f"[{label}] Preview: {len(preview)}, Unscheduled: {len(unscheduled)}")
                for u in unscheduled:
                    print(f"   -> {u['professor']} | {u['course']} | {u['section']} | {u['placed']}/{u['required']}")
                return len(unscheduled)
    finally:
        app.supabase.table = orig_table

print("\n--- Testing candidate sorts ---")
run_test_with_sort(lambda r: backup_order.get((r.get('prof_id'), r.get('course_id')), 9999), "Backup order")

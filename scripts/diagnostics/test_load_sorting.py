import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Calculate total teaching sections/hours per professor from professor_load
all_pls = app.supabase.table('professor_load').select('prof_id, sections').execute().data or []
prof_total_sections = {}
for pl in all_pls:
    pid = pl.get('prof_id')
    sec_cnt = int(pl.get('sections') or 1)
    prof_total_sections[pid] = prof_total_sections.get(pid, 0) + sec_cnt

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
                    # Sort loads by (-sections, -total_sections_across_all_loads, prof_id)
                    r.data.sort(key=lambda row: (
                        -int(row.get('sections') or 1),
                        -prof_total_sections.get(row.get('prof_id'), 0),
                        row.get('prof_id') or 0
                    ))
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
            print(f"\nRESULTS WITH WORKLOAD-BASED LOAD ORDER:")
            print(f"Status: {resp.status_code}, Preview entries: {len(preview)}, Unscheduled: {len(unscheduled)}")
            for u in unscheduled:
                print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")
    finally:
        app.supabase.table = orig_table

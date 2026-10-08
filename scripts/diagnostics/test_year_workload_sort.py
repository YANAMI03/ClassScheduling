import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Fetch all loads to compute prof metrics
all_pls = app.supabase.table('professor_load').select('prof_id, course_id, sections, course(year_level, lecture_hours, lab_hours, ilp_hours)').execute().data or []
courses = {c['course_id']: c for c in app.supabase.table('course').select('course_id, year_level, lecture_hours, lab_hours, ilp_hours').execute().data or []}

prof_tot_h = {}
prof_yl_h = {}
prof_tot_sec = {}
for r in all_pls:
    pid = r['prof_id']
    sec = int(r.get('sections') or 1)
    c = courses.get(r['course_id'], {})
    yl = int(c.get('year_level') or 0)
    h = (int(c.get('lecture_hours') or 0) + int(c.get('lab_hours') or 0) + int(float(c.get('ilp_hours') or 0))) * sec
    prof_tot_h[pid] = prof_tot_h.get(pid, 0) + h
    prof_yl_h[(pid, yl)] = prof_yl_h.get((pid, yl), 0) + h
    prof_tot_sec[pid] = prof_tot_sec.get(pid, 0) + sec

# Let's test patching app.generate_schedule's loads sorting
orig_table = app.supabase.table

def make_sort_key(course_cache):
    def sort_key(row):
        pid = row.get('prof_id')
        cid = row.get('course_id')
        sec = int(row.get('sections') or 1)
        c = course_cache.get(cid, {})
        yl = int(c.get('year_level') or 0)
        # Sort key:
        # 1. -sections for this course (larger section loads first)
        # 2. -prof_yl_h for this year level (professors teaching more hours in this year level first)
        # 3. -prof_tot_h (professors with higher total workload first)
        # 4. prof_id (deterministic tie-breaker)
        return (-sec, -prof_yl_h.get((pid, yl), 0), -prof_tot_h.get(pid, 0), pid or 0)
    return sort_key

sort_fn = make_sort_key(courses)

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
            print(f"RESULTS WITH YEAR-LEVEL WORKLOAD SORT:")
            print(f"Status: {resp.status_code}, Preview entries: {len(preview)}, Unscheduled: {len(unscheduled)}")
            for u in unscheduled:
                print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")
finally:
    app.supabase.table = orig_table

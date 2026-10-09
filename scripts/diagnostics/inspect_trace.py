import json, dotenv, sys, os
sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# 1. Canonical baseline mapping
r_data = json.load(open('backups/professor_load_repair_backup_20261004_023143.json'))['rows']
db_pls = app.supabase.table('professor_load').select('id, course_id, sections, professor_name').eq('program_id', 1).execute().data
db_by_id = {r['id']: r for r in db_pls}
pid_to_name = {1: 'Emilsa T. Bantug', 2: 'Ronaldin V. Bauat'}
for r in r_data:
    if r['id'] in db_by_id:
        pid_to_name[r['prof_id']] = db_by_id[r['id']]['professor_name']

b_rows = json.load(open('backups/professor_load_backup_latest.json'))['professor_load_rows']
m = {}
for idx, r in enumerate(b_rows):
    pid = r.get('prof_id')
    cid = r.get('course_id')
    pname = pid_to_name.get(pid)
    pkey = app.professor_load_importer.normalize_professor_key(pname) if pname else None
    if pkey and cid:
        m[(pkey, int(cid))] = idx

app._CACHED_BASELINE_LOAD_ORDER = m

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

# 3. Patch rooms ordering to sort lecture rooms (301-312) then lab rooms (Lab 1-Lab 7)
orig_table = app.supabase.table
class MockTable:
    def __init__(self, name):
        self.name = name
        self.orig = orig_table(name)
    def __getattr__(self, attr):
        return getattr(self.orig, attr)
    def select(self, *a, **kw):
        res = self.orig.select(*a, **kw)
        if self.name == 'room':
            orig_exec = res.execute
            def patched_execute(*args, **kwargs):
                r = orig_exec(*args, **kwargs)
                if r.data:
                    r.data.sort(key=lambda rm: (
                        0 if app._is_lecture_room_type(rm.get('room_type')) else 1,
                        rm.get('room_name') or ''
                    ))
                return r
            res.execute = patched_execute
        return res

app.supabase.table = MockTable

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

import json
import dotenv
import os
import sys

with open('backups/professor_load_backup_20261003_191218.json') as f:
    data = json.load(f)

# Load current preview
sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

test_client = app.app.test_client()
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

resp = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=False)

with test_client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

backup_sched = [s for s in data.get('full_schedule_rows', []) if not s.get('archive')]

# Also load course map and prof map from backup to know which course is which
# Look at courses in backup
courses = app.supabase.table('course').select('course_id, course_code').execute().data or []
c_map = {c['course_id']: c['course_code'] for c in courses}
backup_pls = data.get('professor_load_rows', [])
pl_to_course = {p['id']: c_map.get(p['course_id'], f"C{p['course_id']}") for p in backup_pls}
pl_to_prof = {p['id']: p['prof_id'] for p in backup_pls}

profs = app.supabase.table('professor').select('prof_id, first_name, last_name').execute().data or []
p_map = {p['prof_id']: f"{p['first_name']} {p['last_name']}" for p in profs}

print("="*80)
print("SECTION 3C-NETWORKING: BACKUP (441 rows) vs CURRENT PREVIEW (438 rows)")
print("="*80)
print("\n--- BACKUP SCHEDULE (3C-Networking) ---")
b_3c = [s for s in backup_sched if s.get('section') == '3C-Networking']
b_3c.sort(key=lambda s: (s['day'], s['class_start']))
for s in b_3c:
    cname = pl_to_course.get(s['professor_load_id'], '?')
    pname = p_map.get(pl_to_prof.get(s['professor_load_id']), '?')
    print(f"  {s['day']:<10} | {s['class_start']}-{s['class_end']} | {s['session_type']:<10} | {cname:<15} | {pname:<25} | Room {s['room_id']}")

print("\n--- CURRENT PREVIEW (3C-Networking) ---")
c_3c = [s for s in preview if s.get('section') == '3C-Networking']
c_3c.sort(key=lambda s: (s['day'], s['start']))
for s in c_3c:
    print(f"  {s['day']:<10} | {s['start']}-{s['end']} | {s['session_type']:<10} | {s['course_code']:<15} | {s['professor_name']:<25} | Room {s.get('room_name')}")

print("\n" + "="*80)
print("SECTION 3F-NETWORKING: BACKUP (441 rows) vs CURRENT PREVIEW (438 rows)")
print("="*80)
print("\n--- BACKUP SCHEDULE (3F-Networking) ---")
b_3f = [s for s in backup_sched if s.get('section') == '3F-Networking']
b_3f.sort(key=lambda s: (s['day'], s['class_start']))
for s in b_3f:
    cname = pl_to_course.get(s['professor_load_id'], '?')
    pname = p_map.get(pl_to_prof.get(s['professor_load_id']), '?')
    print(f"  {s['day']:<10} | {s['class_start']}-{s['class_end']} | {s['session_type']:<10} | {cname:<15} | {pname:<25} | Room {s['room_id']}")

print("\n--- CURRENT PREVIEW (3F-Networking) ---")
c_3f = [s for s in preview if s.get('section') == '3F-Networking']
c_3f.sort(key=lambda s: (s['day'], s['start']))
for s in c_3f:
    print(f"  {s['day']:<10} | {s['start']}-{s['end']} | {s['session_type']:<10} | {s['course_code']:<15} | {s['professor_name']:<25} | Room {s.get('room_name')}")

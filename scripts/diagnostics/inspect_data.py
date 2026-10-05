import os
import json
import dotenv
from supabase import create_client

dotenv.load_dotenv('.env')

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_PUBLISHABLE_KEY')

client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Sign in as admin to bypass RLS
try:
    auth_res = client.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
    client.postgrest.auth(auth_res.session.access_token)
except Exception as e:
    print(f"Auth notice: {e}")

print("=== PROFESSOR LOAD COLUMNS ===")
pl_sample = client.table('professor_load').select('*').limit(1).execute().data or []
if pl_sample:
    print("pl columns:", list(pl_sample[0].keys()))

print("=== ACADEMIC RANKINGS ===")
ranks = client.table('academic_ranking').select('*').execute().data or []
for r in ranks:
    print(r)

print("\n=== TARGET PROFESSORS ===")
prof_res = client.table('professor').select('prof_id, first_name, last_name, academic_ranking_id, time_designation').execute().data or []
ranks_by_id = {r['academic_ranking_id']: r for r in ranks}
target_profs = {}
for p in prof_res:
    fn = p.get('first_name', '')
    ln = p.get('last_name', '')
    name = f"{fn} {ln}".strip()
    if any(x in name for x in ['Tambio', 'Santos', 'Corpuz']):
        target_profs[p['prof_id']] = p
        rank = ranks_by_id.get(p.get('academic_ranking_id'), {})
        print(f"Prof: {name} (ID: {p['prof_id']}), time_designation: {p.get('time_designation')}, Ranking: {rank}")

print("\n=== ALL PROFESSOR LOADS FOR TARGET PROFESSORS ===")
loads = client.table('professor_load').select('id, prof_id, course_id, sections, course(course_id, course_name, semester, year_level, lecture_hours, lab_hours, ilp_hours, units, specialization), professor(first_name, last_name)').in_('prof_id', list(target_profs.keys())).execute().data or []
for l in loads:
    c = l.get('course') or {}
    p = l.get('professor') or {}
    print(f"Load ID: {l.get('id')}, Prof: {p.get('first_name')} {p.get('last_name')} (ID: {l.get('prof_id')}), Course: {c.get('course_name')} (ID: {l.get('course_id')}, Sem: {c.get('semester')}, YL: {c.get('year_level')}, Lec: {c.get('lecture_hours')}, Lab: {c.get('lab_hours')}, ILP: {c.get('ilp_hours')}, Units: {c.get('units')}, Spec: {c.get('specialization')}), Sections: {l.get('sections')}")

print("\n=== ALL PROFESSOR LOADS FOR YEAR 3 2ND SEMESTER COURSES ===")
c_res = client.table('course').select('*').eq('semester', '2nd Semester').eq('year_level', 3).execute().data or []
c_ids = [c['course_id'] for c in c_res]
y3_loads = client.table('professor_load').select('id, prof_id, course_id, sections, course(course_id, course_name, specialization, lecture_hours, lab_hours, ilp_hours), professor(first_name, last_name)').in_('course_id', c_ids).execute().data or []
for l in y3_loads:
    c = l.get('course') or {}
    p = l.get('professor') or {}
    print(f"Load ID: {l.get('id')}, Course: {c.get('course_name')} (ID: {c.get('course_id')}, Spec: {c.get('specialization')}), Prof: {p.get('first_name')} {p.get('last_name')} (ID: {l.get('prof_id')}), Sections: {l.get('sections')}")

print("\n=== ROOMS ===")
rooms = client.table('room').select('*').execute().data or []
print(f"Total rooms: {len(rooms)}")
lec_rooms = [r for r in rooms if 'lecture' in (r.get('room_type') or '').lower()]
lab_rooms = [r for r in rooms if 'lab' in (r.get('room_type') or '').lower()]
print(f"Lecture rooms ({len(lec_rooms)}): {[r['room_name'] for r in lec_rooms]}")
print(f"Lab rooms ({len(lab_rooms)}): {[r['room_name'] for r in lab_rooms]}")

print("\n=== TIMESLOTS ===")
timeslots = client.table('timeslot').select('*').execute().data or []
print(f"Total timeslots: {len(timeslots)}")
for t in timeslots:
    print(t)

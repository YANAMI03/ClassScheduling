import os
import sys
import dotenv
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
dotenv.load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

print("=== SUSPECT A: SCHEDULE PRE-OCCUPIED CHECK ===")
sched_rows = app.supabase.table('schedule').select('*').execute().data or []
active_sched = [r for r in sched_rows if r.get('archive') is False]
archived_sched = [r for r in sched_rows if r.get('archive') is True]
print(f"Total schedule rows: {len(sched_rows)}")
print(f"Active (archive=False): {len(active_sched)}")
print(f"Archived (archive=True): {len(archived_sched)}")

print("\n=== SUSPECT C: ROOMS & TIMESLOTS CHECK ===")
rooms = app.supabase.table('room').select('*').execute().data or []
print(f"Total rooms received: {len(rooms)}")
lec_rooms = [r for r in rooms if app._is_lecture_room_type(r.get('room_type'))]
lab_rooms = [r for r in rooms if app._is_lab_room_type(r.get('room_type'))]
other_rooms = [r for r in rooms if not app._is_lecture_room_type(r.get('room_type')) and not app._is_lab_room_type(r.get('room_type'))]
print(f"Lecture rooms ({len(lec_rooms)}): {[r.get('room_name') for r in lec_rooms]}")
print(f"Lab rooms ({len(lab_rooms)}): {[r.get('room_name') for r in lab_rooms]}")
if other_rooms:
    print(f"Other rooms ({len(other_rooms)}): {[(r.get('room_name'), r.get('room_type')) for r in other_rooms]}")

for r in lab_rooms:
    print(f"  Lab: id={r.get('room_id')}, name={r.get('room_name')}, type={r.get('room_type')}, program_id={r.get('program_id')}")

try:
    working_hours = app.supabase.table('working_hours').select('*').execute().data or []
except Exception as e:
    working_hours = []
    print(f"Error querying timeslot: {e}")
print(f"Timeslots in DB: {len(working_hours)}")
if not working_hours:
    print("Generator uses hardcoded fallback working_hours for Mon-Fri 07:00-19:00 with cutoffs at 16:00/17:00")

print("\n=== SUSPECT D: DISTINCT PROFESSORS & SECTIONS CHECK ===")
loads = app.supabase.table('professor_load').select('id, course_id, sections, professor_name').execute().data or []
print(f"Total professor_load rows: {len(loads)}")
distinct_names = sorted(set(l.get('professor_name') for l in loads if l.get('professor_name')))
distinct_keys = sorted(set(app.get_professor_key(l) for l in loads))
print(f"Distinct professor names: {len(distinct_names)}")
print(f"Distinct professor keys: {len(distinct_keys)}")

target_names = [
    'Christian Noli C. Tambio', 'Nino G. Herrera', 'Henry T. Roque',
    'Marcelino S. Cerin III', 'Jev D. Corpuz', 'Michelle Ann Mae G. Franco', 'Rosalie B. Sison'
]
print("\nLoads for the 7 target faculty:")
for l in loads:
    pname = l.get('professor_name')
    if any(t in pname for t in target_names):
        print(f"  ID={l.get('id')}, Prof={pname}, CourseID={l.get('course_id')}, Sections={l.get('sections')}, Key={app.get_professor_key(l)}")

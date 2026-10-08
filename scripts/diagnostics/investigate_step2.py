import os
import sys
import json
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

# Authenticate supabase
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

print("="*80)
print("INVESTIGATING STEP 2: DATA READ BY THE GENERATOR")
print("="*80)

# 1. Course definitions for IT-IAS02, IT-CAP01 (NST), IT-NET05
print("\n--- 1. COURSE DEFINITIONS ---")
target_courses = ['IT-IAS02', 'IT-CAP01 (NST)', 'IT-NET05']
courses_res = app.supabase.table('course').select('*, program:program_id(id, program_name)').in_('course_name', target_courses).execute()
for c in courses_res.data or []:
    print(f"Course: {c.get('course_name')} (ID: {c.get('course_id')})")
    print(f"  Program ID: {c.get('program_id')}, Semester: {c.get('semester')}, Year Level: {c.get('year_level')}")
    print(f"  Lec Hours: {c.get('lecture_hours')}, Lab Hours: {c.get('lab_hours')}, ILP Hours: {c.get('ilp_hours')}, Units: {c.get('units')}")
    print(f"  Specialization: {c.get('specialization')}")

# Also check course_program link table if any exists
try:
    cp_res = app.supabase.table('course_programs').select('*').execute()
    print(f"  course_programs rows count: {len(cp_res.data or [])}")
except Exception as e:
    print(f"  course_programs query exception: {e}")

# 2. ROOMS
print("\n--- 2. ROOMS ---")
all_rooms_res = app.supabase.table('room').select('*').order('room_name').execute()
all_rooms = all_rooms_res.data or []
print(f"Total rooms in 'room' table: {len(all_rooms)}")
lec_count = 0
lab_count = 0
other_count = 0
for r in all_rooms:
    rtype = r.get('room_type')
    is_lec = app._is_lecture_room_type(rtype)
    is_lab = app._is_lab_room_type(rtype)
    print(f"  Room {r.get('room_id')}: {r.get('room_name'):<10} | type='{rtype}' | is_lec={is_lec} | is_lab={is_lab} | program_id={r.get('program_id')}")
    if is_lec: lec_count += 1
    elif is_lab: lab_count += 1
    else: other_count += 1
print(f"Room counts: Lecture={lec_count}, Lab={lab_count}, Other={other_count}")

# Check what generator queries for rooms:
# Look at app.py room query
print("\nHow generator queries rooms:")
# In app.py line around 10348:
try:
    # app._get_room_pool() or whatever generator calls
    gen_rooms = app.supabase.table('room').select('*').execute().data or []
    print(f"  Generator sees {len(gen_rooms)} rooms directly")
except Exception as e:
    print(f"  Generator room query error: {e}")

# 3. TIMESLOTS
print("\n--- 3. TIMESLOTS ---")
ts_res = app.supabase.table('timeslot').select('*').order('day').order('start_time').execute()
all_ts = ts_res.data or []
print(f"Total timeslots in 'timeslot' table: {len(all_ts)}")
days = set(t.get('day') for t in all_ts)
print(f"Days: {days}")
cutoffs = {t.get('day'): t.get('professor_cutoff') for t in all_ts if t.get('professor_cutoff')}
print(f"Cutoffs per day: {cutoffs}")
# Check lunch timeslot
lunch_ts = [t for t in all_ts if '12:00' in str(t.get('start_time')) or '12:00' in str(t.get('end_time'))]
print(f"Lunch timeslots: {lunch_ts}")

# 4. LOADS
print("\n--- 4. PROFESSOR LOADS FOR TARGET PROFESSORS/COURSES ---")
pl_res = app.supabase.table('professor_load').select('*, professor(prof_id, first_name, last_name, program_id), course(course_id, course_name, program_id, lecture_hours, lab_hours, ilp_hours)').execute()
all_pls = pl_res.data or []
print(f"Total professor_load rows: {len(all_pls)}")
for pl in all_pls:
    p = pl.get('professor') or {}
    c = pl.get('course') or {}
    p_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    c_name = c.get('course_name')
    if c_name in target_courses or any(target in p_name for target in ['Tambio', 'Santos', 'Corpuz']):
        print(f"Load ID {pl.get('id')}: Prof='{p_name}' (prof_id={pl.get('prof_id')}) | Course='{c_name}' (c_id={pl.get('course_id')}) | Sections={pl.get('sections')} | load.program_id={pl.get('program_id')}")

# 5. CROSS-PROGRAM OCCUPANCY
print("\n--- 5. CROSS-PROGRAM OCCUPANCY ---")
sched_res = app.supabase.table('schedule').select('*').execute()
all_sched = sched_res.data or []
print(f"Total rows in 'schedule' table: {len(all_sched)}")
sched_active = [s for s in all_sched if not s.get('archive')]
sched_archived = [s for s in all_sched if s.get('archive')]
print(f"Active schedule rows: {len(sched_active)}, Archived schedule rows: {len(sched_archived)}")
for s in sched_active[:10]:
    print(f"  Active schedule row: id={s.get('id')}, program_id={s.get('program_id')}, sec={s.get('section')}, course_id={s.get('course_id')}, day={s.get('day')}, time={s.get('start_time')}-{s.get('end_time')}")

# Check how cross-program occupancy is computed in app.py
print("\nChecking cross-program occupancy logic in app.py:")
user_prog_id = 1
active_query = app.supabase.table('schedule').select('*').eq('archive', False)
active_schedules = active_query.execute().data or []
other_prog_schedules = [s for s in active_schedules if s.get('program_id') and s.get('program_id') != user_prog_id]
print(f"Total active schedules across all programs: {len(active_schedules)}")
print(f"Active schedules for OTHER programs (program_id != 1): {len(other_prog_schedules)}")
null_prog_schedules = [s for s in active_schedules if s.get('program_id') is None]
print(f"Active schedules with NULL program_id: {len(null_prog_schedules)}")

# 6. SECTIONS
print("\n--- 6. SECTION IDENTITY ---")
calc_result = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester', department=app._get_department())
print("Calculation breakdown:")
for b in calc_result.get('breakdown', []):
    print(f"  Year {b['year_level']}: count={b['count']}, is_spec={b.get('is_specialized')}, sections={b.get('section_names')}")
    if b.get('specialization_groups'):
        for sg in b['specialization_groups']:
            print(f"    Spec {sg['specialization']}: count={sg['count']}, sections={sg['section_names']}")

# 7. LIMITS & ACADEMIC RANKINGS
print("\n--- 7. PROFESSOR LIMITS & ACADEMIC RANKINGS ---")
profs_res = app.supabase.table('professor').select('*, academic_ranking(*)').execute()
for p in profs_res.data or []:
    p_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    if any(target in p_name for target in ['Tambio', 'Santos', 'Corpuz']):
        print(f"Prof: {p_name} | time_desig={p.get('time_designation')} | ranking_id={p.get('academic_ranking_id')} | ranking={p.get('academic_ranking')}")

# 8. DAILY CUTOFF
print("\n--- 8. DAILY CUTOFF DEFINITION ---")
for ts in all_ts:
    if ts.get('professor_cutoff'):
        print(f"Timeslot cutoff: Day={ts.get('day')}, cutoff={ts.get('professor_cutoff')}")

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

print("--- 6. SECTION IDENTITY ---")
calc_result = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester', department=app._get_department())
print("Calculation breakdown:")
for b in calc_result.get('breakdown', []):
    print(f"  Year {b['year_level']}: num_sections={b.get('num_sections') or b.get('total_sections')}, is_spec={b.get('is_specialized')}, sections={b.get('section_names')}")
    if b.get('specialization_groups'):
        for sg in b['specialization_groups']:
            print(f"    Spec {sg['specialization']}: sections={sg.get('section_names')}")

print("\n--- 7. PROFESSOR LIMITS & ACADEMIC RANKINGS ---")
profs_res = app.supabase.table('professor').select('*, academic_ranking(*)').execute()
for p in profs_res.data or []:
    p_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    if any(target in p_name for target in ['Tambio', 'Santos', 'Corpuz']):
        print(f"Prof: {p_name} (ID: {p.get('prof_id')}) | time_desig={p.get('time_designation')} | ranking_id={p.get('academic_ranking_id')} | ranking={p.get('academic_ranking')}")

print("\n--- 8. TIMESLOT DETAILS ---")
ts_res = app.supabase.table('timeslot').select('*').order('day').execute()
for ts in ts_res.data or []:
    print(f"Timeslot ID {ts.get('timeslot_id')}: Day={ts.get('day')}, Start={ts.get('start_time')}, End={ts.get('end_time')}, Cutoff={ts.get('professor_cutoff')}, ProgramID={ts.get('program_id')}")

print("\n--- 9. ALL YEAR 3 COURSES IN 2nd SEMESTER ---")
y3_courses = app.supabase.table('course').select('*').eq('program_id', 1).eq('semester', '2nd Semester').eq('year_level', 3).execute().data or []
for c in y3_courses:
    print(f"Course {c.get('course_id')}: {c.get('course_name'):<20} | Spec: {c.get('specialization'):<15} | Lec: {c.get('lecture_hours')}, Lab: {c.get('lab_hours')}, ILP: {c.get('ilp_hours')}")

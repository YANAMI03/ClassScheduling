import os, sys
sys.path.insert(0, os.path.abspath('.'))
import dotenv
from datetime import timedelta
from collections import Counter
import json
import re

dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

def get_professor_key(load):
    if not load:
        return ""
    pkey = load.get('professor_key')
    if pkey:
        return pkey.strip().lower()
    pname = load.get('professor_name') or load.get('name') or ""
    return app.professor_load_importer.normalize_professor_key(pname)

def get_professor_name(load):
    if not load:
        return ""
    pname = load.get('professor_name') or load.get('name')
    if pname:
        return pname.strip()
    return ""

# Let's run a test simulation with the helpers applied
user_prog_id = 1
standard_semester = '2nd Semester'
program = 'BSIT'

calc_result = app.calculate_semester_section_counts(program_id=user_prog_id, semester=standard_semester)
all_courses = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', standard_semester).eq('program_id', user_prog_id).order('year_level').order('course_name').execute().data or []
for c in all_courses:
    p_rel = app._rel(c, 'program') or {}
    c['program_name'] = p_rel.get('program_name') or program or ''
    c['program'] = c['program_name']

courses_by_year = {1: [], 2: [], 3: [], 4: []}
for c in all_courses:
    try:
        yl = int(c.get('year_level') or 1)
    except:
        yl = 1
    courses_by_year.setdefault(yl, []).append(c)

all_sections = []
for yl_info in calc_result.get('breakdown', []):
    yl = yl_info['year_level']
    is_spec = yl_info.get('is_specialized', False)
    if not is_spec:
        for s_name in yl_info.get('section_names', []):
            all_sections.append({'section': s_name, 'section_name': s_name, 'year_level': str(yl), 'student_count': 40, 'semester': standard_semester, 'major': None})
    else:
        for grp in yl_info.get('specialization_groups', []):
            spec_name = grp['specialization']
            for s_name in grp.get('section_names', []):
                all_sections.append({'section': s_name, 'section_name': s_name, 'year_level': str(yl), 'student_count': 40, 'semester': standard_semester, 'major': spec_name})

pc_res = app.supabase.table('professor_load').select('*').eq('program_id', user_prog_id).execute()
pc_data = pc_res.data or []
for r in pc_data:
    r['professor_key'] = get_professor_key(r)
    r['professor_name'] = get_professor_name(r)

# Test sorting: how is pc_data sorted?
# Stable order: (-sections, pkey, load_id) or baseline_order
base_order = app._get_baseline_professor_load_order()

def sort_key(row):
    cid = int(row.get('course_id') or 0)
    pkey = row.get('professor_key')
    lid = int(row.get('id') or 0)
    sec = int(row.get('sections') or 1)
    rank = base_order.get((pkey, cid), 9999)
    return (rank, -sec, pkey, lid)

pc_data.sort(key=sort_key)

print(f"Total sections: {len(all_sections)}, Total courses: {len(all_courses)}, Total loads: {len(pc_data)}")

import os
import sys
import dotenv
from collections import Counter
import json

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Let's see what happens if we simulate generate_schedule with:
# 1. prof_track_affinity using l['professor_key']
# 2. assigned_secs using l_item.get('professor_load_id') == pcid

with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['role'] = 'scheduler'
    app.session['program'] = 'BSIT'
    app.session['program_id'] = 1
    app.session['access_token'] = auth_res.session.access_token

    # Let's inspect what happens to section_course_assignment under both:
    # We can inspect calculate_semester_section_counts
    calc_res = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    all_courses = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', '2nd Semester').eq('program_id', 1).execute().data or []
    pc_data = app.supabase.table('professor_load').select('*').eq('program_id', 1).execute().data or []
    for r in pc_data:
        r['professor_key'] = app.professor_load_importer.normalize_professor_key(r.get('professor_name') or '')

    # Check course 50 (IT-IAS02)
    c50_loads = [r for r in pc_data if r['course_id'] == 50]
    print(f"IT-IAS02 loads count: {len(c50_loads)}")
    for l in c50_loads:
        print(f"  Load ID {l['id']}: Prof '{l['professor_name']}' ({l['professor_key']}), sections={l['sections']}")

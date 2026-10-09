import os
import sys
import copy
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

# Authenticate supabase with admin credentials
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

def run_and_trace():
    with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
        app.session['user_id'] = 1
        app.session['role'] = 'scheduler'
        app.session['program'] = 'BSIT'
        app.session['program_id'] = 1
        app.session['access_token'] = auth_res.session.access_token

        user_prog_id = 1
        standard_semester = '2nd Semester'
        department = app._get_department()
        program = 'BSIT'

        calc_result = app.calculate_semester_section_counts(program_id=user_prog_id, semester=standard_semester, department=department)
        
        query = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', standard_semester)
        if user_prog_id:
            query = query.eq('program_id', user_prog_id)
        all_courses = query.order('year_level').order('course_name').execute().data or []
        for c in all_courses:
            p_rel = app._rel(c, 'program') or {}
            c['program_name'] = p_rel.get('program_name') or program or ''
            c['program'] = c['program_name']

        courses_by_year = {1: [], 2: [], 3: [], 4: []}
        for c in all_courses:
            try:
                yl = int(c.get('year_level') or 1)
            except (ValueError, TypeError):
                yl = 1
            courses_by_year.setdefault(yl, []).append(c)

        for yl in courses_by_year:
            seen_cids = set()
            deduped = []
            for c in courses_by_year[yl]:
                cid = c.get('course_id') or c.get('course_name')
                if cid not in seen_cids:
                    seen_cids.add(cid)
                    deduped.append(c)
            courses_by_year[yl] = deduped

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

        # Load from professor_load directly
        pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, sections, professor_name').execute()
        pc_data = pc_res.data or []
        for r in pc_data:
            if not r.get('professor_key') and r.get('professor_name'):
                r['professor_key'] = app.professor_load_importer.normalize_professor_key(r['professor_name'])

        professor_load_map = {}
        professors_by_course = {}

        for row in pc_data:
            pcid = row.get('professor_load_id') or row.get('id') or 1
            cid = row.get('course_id')
            pkey = row.get('professor_key')
            prof_display_name = row.get('professor_name') or "Professor"

            if pkey and cid:
                professor_load_map[(pkey, cid)] = pcid

            sec_val = int(row.get('sections') or 0)
            if sec_val == 0:
                sec_val = 999
            professors_by_course.setdefault(cid, []).append({
                'professor_load_id': pcid,
                'course_id': cid,
                'prof_id': pkey,
                'professor_key': pkey,
                'professor_name': prof_display_name,
                'first_name': prof_display_name.split()[0],
                'last_name': prof_display_name.split()[-1],
                'time_designation': 5,
                'max_hours': 40,
                'sections': sec_val,
                'quota': sec_val,
            })

        rooms_res = app.supabase.table('room').select('*').execute()
        all_rooms = rooms_res.data or []
        lecture_rooms = [r for r in all_rooms if app._is_lecture_room_type(r.get('room_type'))]
        lab_rooms = [r for r in all_rooms if app._is_lab_room_type(r.get('room_type'))]

        timeslots = (app.supabase.table('timeslot').select('*').execute().data) or []
        timeslots.sort(key=lambda t: str(t.get('start_time') or ''))
        candidate_slots = app._build_candidate_slots(timeslots)

        _day_cutoff_map = {}
        for _ts in timeslots:
            _d = (_ts.get('day') or '').strip()
            _cutoff = _ts.get('professor_cutoff')
            if _d and _cutoff and _d not in _day_cutoff_map:
                _day_cutoff_map[_d] = _cutoff

        _prof_cutoff_map = {} # All default to True

        day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
        slot_groups = {}
        for slot in candidate_slots:
            slot_groups.setdefault(slot['day'], []).append(slot)

        section_bookings = {(sec['section'], sec.get('major')): [] for sec in all_sections}
        room_bookings = {}
        room_usage = {r['room_id']: 0 for r in all_rooms if r.get('room_id') is not None}
        room_last_used = {r['room_id']: 0 for r in all_rooms if r.get('room_id') is not None}
        room_order = {r['room_id']: idx for idx, r in enumerate(all_rooms) if r.get('room_id') is not None}
        assignment_step = 0
        professor_bookings = {}
        professor_hours = {}
        prof_day_hours = {}
        prof_scheduled_days = {}
        preview_entries = []
        generation_warnings = []
        total_sessions_scheduled = 0
        total_sessions_required = 0

        # Run with current app.py section_course_assignment vs fixed!
        # First let's test what app.py currently does:
        prof_track_affinity = {}
        for c in all_courses:
            c_yl = int(c.get('year_level') or 1)
            c_spec = c.get('specialization') or c.get('major')
            if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
                for l in professors_by_course.get(c['course_id'], []):
                    # In app.py line 10389, it did: l['prof_id']
                    # Let's see what happens if l['prof_id'] was None vs l['professor_key']!
                    prof_track_affinity[(l['professor_key'], c_yl)] = str(c_spec).strip()

        print("Track affinity populated for professors:", len(prof_track_affinity))

run_and_trace()

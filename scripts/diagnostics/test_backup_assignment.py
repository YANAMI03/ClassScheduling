import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

# Authenticate supabase
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with open('backups/professor_load_backup_20261003_191218.json') as f:
    backup_data = json.load(f)

backup_sched = [s for s in backup_data.get('full_schedule_rows', []) if not s.get('archive')]
backup_pls = {p['id']: p for p in backup_data.get('professor_load_rows', [])}

backup_section_prof = {}
for s in backup_sched:
    sec = s.get('section')
    pl_id = s.get('professor_load_id')
    pl = backup_pls.get(pl_id)
    if pl:
        cid = pl.get('course_id')
        pid = pl.get('prof_id')
        backup_section_prof[(sec, cid)] = pid

print(f"Total (section, course) mappings in working backup: {len(backup_section_prof)}")

# Now run simulation from reproduce_generation, but override section_course_assignment
# Let's inspect reproduce_generation.simulate_generation
from scripts.diagnostics import reproduce_generation

# We can monkeypatch or adapt reproduce_generation:
import copy

# Let's run a custom simulation function that does this:
def test_sim_with_backup_assignment():
    user_prog_id = 1
    standard_semester = '2nd Semester'
    program = 'BSIT'
    department = app._get_department()

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
        try: yl = int(c.get('year_level') or 1)
        except: yl = 1
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

    pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, prof_id, sections, professor(first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff))').execute()
    all_profs_res = app.supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff)').execute()
    pc_data = pc_res.data or []
    professors_by_course = {}
    _all_profs_by_id = {ap.get('prof_id'): ap for ap in (all_profs_res.data or []) if ap.get('prof_id')}

    for row in pc_data:
        pcid = row.get('professor_load_id') or row.get('id') or 1
        cid = row.get('course_id')
        pid = row.get('prof_id')
        p = app._rel(row, 'professor') or _all_profs_by_id.get(pid)
        if p:
            sec_val = int(row.get('sections') or 0)
            if sec_val == 0: sec_val = 999
            professors_by_course.setdefault(cid, []).append({
                'professor_load_id': pcid,
                'course_id': cid,
                'prof_id': pid,
                'first_name': p.get('first_name'),
                'last_name': p.get('last_name'),
                'time_designation': int(p.get('time_designation') or 5),
                'max_hours': int(app._ranking_constraints(p)['max_hours']),
                'sections': sec_val,
                'quota': sec_val,
            })

    # Here is the KEY: map section_course_assignment using the backup's prof_id mapping!
    section_course_assignment = {}
    for sec in all_sections:
        s_name = sec['section']
        yr = int(sec.get('year_level') or 1)
        for c in courses_by_year.get(yr, []):
            cid = c.get('course_id')
            # Check if this course applies to section
            if not app._major_matches(c.get('specialization') or c.get('major'), sec.get('major')):
                continue
            # Look up which prof was assigned in backup
            target_pid = backup_section_prof.get((s_name, cid))
            # Find matching load in professors_by_course[cid]
            loads = professors_by_course.get(cid, [])
            matched_load = next((l for l in loads if l['prof_id'] == target_pid), None)
            if not matched_load and loads:
                matched_load = loads[0]
            if matched_load:
                section_course_assignment[(s_name, cid)] = matched_load

    print(f"Built section_course_assignment with {len(section_course_assignment)} entries")

    # Now let's run simulate_generation with this section_course_assignment
    # We can inspect reproduce_generation
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

    _prof_cutoff_map = {}
    for _pr in (all_profs_res.data or []):
        _pid = _pr.get('prof_id')
        if _pid is None: continue
        _rank = _pr.get('academic_ranking') or {}
        if isinstance(_rank, list): _rank = _rank[0] if _rank else {}
        _prof_cutoff_map[_pid] = _rank.get('has_cutoff', True)

    slot_groups = {}
    for slot in candidate_slots:
        slot_groups.setdefault(slot['day'], []).append(slot)

    # Let's run generation with the exact app.py scheduling function
    # Let's import the scheduling logic from reproduce_generation or test_flask_generate
    # We can patch section_course_assignment in reproduce_generation!

test_sim_with_backup_assignment()

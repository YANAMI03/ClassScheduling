import os, sys, json
sys.path.insert(0, os.path.abspath('.'))
import dotenv
dotenv.load_dotenv('.env')
import app

auth=app.supabase.auth.sign_in_with_password({'email':'admin@example.com','password':'password123'})
app.supabase.postgrest.auth(auth.session.access_token)

# 1. Load backup assignment
with open('backups/professor_load_backup_latest.json') as f:
    d = json.load(f)
pls = {p['id']: p for p in d.get('professor_load_rows', [])}
active_rows = [r for r in d.get('full_schedule_rows', []) if not r.get('archive')]

with open('backups/professor_load_repair_backup_20261004_023143.json') as f:
    repair_data = json.load(f)
r_rows = repair_data.get('rows', [])

db_pls = app.supabase.table('professor_load').select('id, course_id, sections, professor_name').eq('program_id', 1).execute().data or []
db_by_id = {r['id']: r for r in db_pls}
prof_id_to_name = {1: 'Emilsa T. Bantug', 2: 'Ronaldin V. Bauat'}
for r in r_rows:
    lid = r['id']
    pid = r['prof_id']
    if lid in db_by_id:
        prof_id_to_name[pid] = db_by_id[lid]['professor_name']

backup_sc = {}
for r in active_rows:
    pl = pls.get(r['professor_load_id'], {})
    cid = pl.get('course_id')
    pid = pl.get('prof_id')
    sec = r.get('section')
    if sec and cid:
        backup_sc[(sec, cid)] = prof_id_to_name.get(pid, f'Prof {pid}')

# 2. Get current generator assignment
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['program'] = 'BSIT'
    app.session['role'] = 'scheduler'
    app.session['program_id'] = 1

    # Let's inspect how app.py builds section_course_assignment:
    # Run the exact code from app.py
    user_prog_id = 1
    standard_semester = '2nd Semester'
    calc_result = app.calculate_semester_section_counts(program_id=user_prog_id, semester=standard_semester)
    all_courses = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', standard_semester).eq('program_id', user_prog_id).order('year_level').order('course_name').execute().data or []
    for c in all_courses:
        p_rel = app._rel(c, 'program') or {}
        c['program_name'] = p_rel.get('program_name') or 'BSIT'
        c['program'] = c['program_name']

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

    pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, sections, professor_name').eq('program_id', user_prog_id).execute()
    pc_data = pc_res.data or []
    for r in pc_data:
        r['professor_key'] = app.get_professor_key(r)
        r['professor_name'] = app.get_professor_name(r)

    baseline_order = app._get_baseline_professor_load_order()
    def _get_pc_sort_key(row):
        cid = int(row.get('course_id') or 0)
        pkey = row.get('professor_key') or app.get_professor_key(row)
        order_rank = baseline_order.get((pkey, cid))
        if order_rank is None and row.get('prof_id'):
            order_rank = baseline_order.get((int(row['prof_id']), cid))
        if order_rank is None:
            order_rank = 9999
        return (order_rank, -int(row.get('sections') or 1), pkey or '', int(row.get('professor_load_id') or row.get('id') or 0))
    pc_data.sort(key=_get_pc_sort_key)

    professors_by_course = {}
    for row in pc_data:
        pcid = row.get('professor_load_id') or row.get('id') or 1
        cid = row.get('course_id')
        pkey = row.get('professor_key')
        pname = row.get('professor_name')
        sec_val = int(row.get('sections') or 0)
        if sec_val == 0: sec_val = 999
        professors_by_course.setdefault(cid, []).append({
            'professor_load_id': pcid,
            'course_id': cid,
            'prof_id': pkey,
            'professor_key': pkey,
            'professor_name': pname,
            'sections': sec_val,
        })

    prof_track_affinity = {}
    for c in all_courses:
        c_yl = int(c.get('year_level') or 1)
        c_spec = c.get('specialization') or c.get('major')
        if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
            for l in professors_by_course.get(c['course_id'], []):
                pkey = l.get('professor_key') or l.get('prof_id')
                if pkey:
                    prof_track_affinity[(pkey, c_yl)] = str(c_spec).strip()

    section_course_assignment = {}
    for course in all_courses:
        cid = course['course_id']
        yl = int(course.get('year_level') or 1)
        cmajor = course.get('specialization') or course.get('major')
        matching_secs = [s for s in all_sections if int(s.get('year_level') or 1) == yl and app._major_matches(cmajor, s.get('major'))]
        loads = professors_by_course.get(cid, [])
        if not loads: continue
        is_general_course = (not cmajor or str(cmajor).strip().lower() in ('general', 'none', ''))

        if is_general_course and any(s.get('major') for s in matching_secs):
            assigned_sec_names = set()
            unassigned_loads = []
            for l in loads:
                pkey = l.get('professor_key') or l.get('prof_id')
                aff = prof_track_affinity.get((pkey, yl))
                cnt = l.get('sections') or 1
                placed_cnt = 0
                if aff:
                    for s in matching_secs:
                        if placed_cnt >= cnt: break
                        if s['section'] not in assigned_sec_names and app._major_matches(aff, s.get('major')):
                            section_course_assignment[(s['section'], cid)] = l
                            assigned_sec_names.add(s['section'])
                            placed_cnt += 1
                rem = cnt - placed_cnt
                if rem > 0: unassigned_loads.append((l, rem))

            rem_secs = [s for s in matching_secs if s['section'] not in assigned_sec_names]
            unassigned_loads.sort(key=lambda item: item[0].get('sections', 1))
            for s in reversed(rem_secs):
                if unassigned_loads:
                    l, rem = unassigned_loads[0]
                    section_course_assignment[(s['section'], cid)] = l
                    assigned_sec_names.add(s['section'])
                    if rem == 1: unassigned_loads.pop(0)
                    else: unassigned_loads[0] = (l, rem - 1)
        else:
            sec_idx = 0
            for l in loads:
                cnt = l.get('sections') or 1
                for _ in range(cnt):
                    if sec_idx < len(matching_secs):
                        sec = matching_secs[sec_idx]
                        section_course_assignment[(sec['section'], cid)] = l
                        sec_idx += 1
                    else: break

    print("="*80)
    print("COMPARING (SECTION, COURSE) ASSIGNMENTS FOR YEAR 3 NETWORKING SECTIONS:")
    print("="*80)
    for sec_name in ['3A-Networking', '3B-Networking', '3C-Networking', '3D-Networking', '3E-Networking', '3F-Networking']:
        print(f"\n{sec_name}:")
        for cid in [50, 51, 58, 59, 60]:
            b_prof = backup_sc.get((sec_name, cid), 'None')
            c_prof = section_course_assignment.get((sec_name, cid), {}).get('professor_name', 'None')
            diff_flag = "  <-- MISMATCH!" if b_prof != c_prof else "  [MATCH]"
            print(f"  Course {cid}: Backup={b_prof:<28} | Current={c_prof:<28} {diff_flag}")

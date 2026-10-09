import os
import sys
import copy
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

user_prog_id = 1
standard_semester = '2nd Semester'
program = 'BSIT'

calc_result = app.calculate_semester_section_counts(program_id=user_prog_id, semester=standard_semester)
query = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', standard_semester).eq('program_id', user_prog_id)
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
    seen = set()
    dedup = []
    for c in courses_by_year[yl]:
        cid = c.get('course_id')
        if cid not in seen:
            seen.add(cid)
            dedup.append(c)
    courses_by_year[yl] = dedup

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

def get_professor_key(load):
    if not load: return ""
    k = load.get('professor_key')
    if k: return k.strip().lower()
    n = load.get('professor_name') or load.get('name') or ""
    return app.professor_load_importer.normalize_professor_key(n)

def get_professor_name(load):
    if not load: return ""
    n = load.get('professor_name') or load.get('name')
    return n.strip() if n else ""

for r in pc_data:
    r['professor_key'] = get_professor_key(r)
    r['professor_name'] = get_professor_name(r)

baseline_order = app._get_baseline_professor_load_order()
def _get_pc_sort_key(row):
    cid = int(row.get('course_id') or 0)
    pkey = row.get('professor_key')
    order_rank = baseline_order.get((pkey, cid), 9999)
    return (order_rank, -int(row.get('sections') or 1), pkey or '')

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
        'first_name': pname.split()[0],
        'last_name': pname.split()[-1],
        'time_designation': 5,
        'max_hours': 40,
        'sections': sec_val,
        'quota': sec_val
    })

rooms_res = app.supabase.table('room').select('*').execute()
all_rooms = rooms_res.data or []
lecture_rooms = [r for r in all_rooms if app._is_lecture_room_type(r.get('room_type'))]
lab_rooms = [r for r in all_rooms if app._is_lab_room_type(r.get('room_type'))]

try:
    timeslots = (app.supabase.table('timeslot').select('*').execute().data) or []
except Exception:
    timeslots = []

if not timeslots:
    for d in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
        timeslots.append({
            'day': d,
            'start_time': '07:00:00',
            'end_time': '19:00:00',
            'lunch_time': '12:00:00',
            'professor_cutoff': '17:00:00' if d != 'Monday' else '16:00:00'
        })

timeslots.sort(key=lambda t: str(t.get('start_time') or ''))
candidate_slots = app._build_candidate_slots(timeslots)

_day_cutoff_map = {}
for _ts in timeslots:
    _d = (_ts.get('day') or '').strip()
    _cutoff = _ts.get('professor_cutoff')
    if _d and _cutoff and _d not in _day_cutoff_map:
        _day_cutoff_map[_d] = _cutoff

_prof_cutoff_map = {}

day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
slot_groups = {}
for slot in candidate_slots:
    slot_groups.setdefault(slot['day'], []).append(slot)

def run_simulation(use_fixed_affinity=True):
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

    # Map each professor to any specialized track they teach in this year level
    prof_track_affinity = {}
    for c in all_courses:
        c_yl = int(c.get('year_level') or 1)
        c_spec = c.get('specialization') or c.get('major')
        if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
            for l in professors_by_course.get(c['course_id'], []):
                if use_fixed_affinity:
                    prof_track_affinity[(l['professor_key'], c_yl)] = str(c_spec).strip()
                else:
                    prof_track_affinity[(l.get('old_prof_id'), c_yl)] = str(c_spec).strip()

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
                aff_key = (l['professor_key'], yl) if use_fixed_affinity else (l.get('old_prof_id'), yl)
                aff = prof_track_affinity.get(aff_key)
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

    def _can_prof_teach_on_day(prof_dict, check_day):
        pid = prof_dict.get('professor_key')
        if not pid: return True
        days_set = prof_scheduled_days.get(pid, set())
        if check_day in days_set: return True
        max_days = int(prof_dict.get('time_designation') or 5)
        return len(days_set) < max_days

    def _schedule_single_session(session_type, duration, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
        nonlocal assignment_step
        if duration <= 0: return True
        course_id = course['course_id']
        if not assigned_prof: return False
        pk = assigned_prof.get('professor_key')
        assigned_professor_load_id = assigned_prof.get('professor_load_id')
        cand_rooms = lecture_rooms if session_type in ('Lecture', 'ILP') else lab_rooms
        passes = [{'strict_rules': True}, {'strict_rules': False}]
        for p_config in passes:
            all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
            scored_days = []
            for day in all_days:
                day_slots = slot_groups.get(day, [])
                if len(day_slots) < duration: continue
                test_slot = day_slots[0] if day_slots else None
                slot_is_late = app._is_late_slot(test_slot) if test_slot else False
                s = app._score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used, strict=p_config['strict_rules'])
                if s >= 0:
                    min_prof_day_h = prof_day_hours.get((pk, day), 0.0)
                    s -= int(min_prof_day_h * 15)
                    scored_days.append((s, day))
            scored_days.sort(key=lambda x: x[0], reverse=True)
            late_threshold = timedelta(hours=17)
            for _, day in scored_days:
                day_slots = slot_groups[day]
                if len(day_slots) < duration: continue
                start_indices = list(range(0, len(day_slots) - duration + 1))
                if session_type == 'ILP':
                    start_indices.sort(key=lambda idx: -100 if day == 'Monday' and idx == 0 else -idx)
                for start_index in start_indices:
                    block_slots = day_slots[start_index:start_index + duration]
                    if not app._is_contiguous_block(block_slots): continue
                    slot_is_late = app._is_late_slot(block_slots[0], late_threshold)
                    if p_config['strict_rules'] and slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days: continue
                    block_start = block_slots[0]['start_time']
                    block_end = block_slots[-1]['end_time']
                    if app._has_conflict(day, block_start, block_end, section_bookings[sec_key]): continue
                    if block_start < timedelta(hours=8) and session_type != 'ILP': continue
                    if app._has_conflict(day, block_start, block_end, professor_bookings.get(pk, [])): continue
                    if p_config['strict_rules'] and not _can_prof_teach_on_day(assigned_prof, day): continue
                    if app._check_professor_cutoff_conflict(pk, day, block_start, block_end, session_type, prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None: continue
                    assigned_room = app._select_least_used_room(cand_rooms, day, block_start, block_end, room_bookings, room_usage, room_last_used, room_order)
                    if not assigned_room: continue
                    rk = assigned_room['room_id']
                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id, 'prof_id': pk, 'professor_key': pk,
                        'professor_name': assigned_prof.get('professor_name'),
                        'section': section_name, 'room_id': rk, 'day': day,
                        'start': block_start, 'end': block_end, 'session_type': session_type
                    })
                    section_bookings[sec_key].append((day, block_start, block_end))
                    room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                    assignment_step += 1
                    room_usage[rk] = room_usage.get(rk, 0) + 1
                    room_last_used[rk] = assignment_step
                    professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                    professor_hours[pk] = professor_hours.get(pk, 0.0) + duration
                    prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + duration
                    prof_scheduled_days.setdefault(pk, set()).add(day)
                    courses_per_day[day] = courses_per_day.get(day, 0) + 1
                    days_tried[day] = days_tried.get(day, 0) + 1
                    if slot_is_late: late_days.add(day)
                    return True
        return False

    def _schedule_paired_block(lec_dur, lab_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
        nonlocal assignment_step
        total_dur = lec_dur + lab_dur
        course_id = course['course_id']
        if not assigned_prof: return False
        pk = assigned_prof.get('professor_key')
        assigned_professor_load_id = assigned_prof.get('professor_load_id')
        passes = [{'strict_rules': True}, {'strict_rules': False}]
        for p_config in passes:
            all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
            scored_days = []
            for day in all_days:
                day_slots = slot_groups.get(day, [])
                if len(day_slots) < total_dur: continue
                test_slot = day_slots[0] if day_slots else None
                slot_is_late = app._is_late_slot(test_slot) if test_slot else False
                s = app._score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used, strict=p_config['strict_rules'])
                if s >= 0:
                    min_prof_day_h = prof_day_hours.get((pk, day), 0.0)
                    s -= int(min_prof_day_h * 15)
                    scored_days.append((s, day))
            scored_days.sort(key=lambda x: x[0], reverse=True)
            late_threshold = timedelta(hours=17)
            for _, day in scored_days:
                day_slots = slot_groups[day]
                if len(day_slots) < total_dur: continue
                for start_index in range(0, len(day_slots) - total_dur + 1):
                    full_block = day_slots[start_index:start_index + total_dur]
                    if not app._is_contiguous_block(full_block): continue
                    slot_is_late = app._is_late_slot(full_block[0], late_threshold)
                    if p_config['strict_rules'] and slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days: continue
                    lec_start = full_block[0]['start_time']
                    lec_end = full_block[lec_dur - 1]['end_time'] if lec_dur > 0 else lec_start
                    lab_start = full_block[lec_dur]['start_time'] if lab_dur > 0 else lec_end
                    lab_end = full_block[-1]['end_time']
                    if app._has_conflict(day, lec_start, lab_end, section_bookings[sec_key]): continue
                    if lec_start < timedelta(hours=8): continue
                    if app._has_conflict(day, lec_start, lab_end, professor_bookings.get(pk, [])): continue
                    if p_config['strict_rules'] and not _can_prof_teach_on_day(assigned_prof, day): continue
                    if app._check_professor_cutoff_conflict(pk, day, lec_start, lab_end, 'Lecture', prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None: continue
                    assigned_lec_room = app._select_least_used_room(lecture_rooms, day, lec_start, lec_end, room_bookings, room_usage, room_last_used, room_order)
                    if not assigned_lec_room: continue
                    assigned_lab_room = app._select_least_used_room(lab_rooms, day, lab_start, lab_end, room_bookings, room_usage, room_last_used, room_order)
                    if not assigned_lab_room: continue
                    lec_rk = assigned_lec_room['room_id']
                    lab_rk = assigned_lab_room['room_id']
                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id, 'prof_id': pk, 'professor_key': pk,
                        'professor_name': assigned_prof.get('professor_name'),
                        'section': section_name, 'room_id': lec_rk, 'day': day,
                        'start': lec_start, 'end': lec_end, 'session_type': 'Lecture'
                    })
                    section_bookings[sec_key].append((day, lec_start, lec_end))
                    room_bookings.setdefault(lec_rk, []).append((day, lec_start, lec_end))
                    assignment_step += 1
                    room_usage[lec_rk] = room_usage.get(lec_rk, 0) + 1
                    room_last_used[lec_rk] = assignment_step
                    professor_bookings.setdefault(pk, []).append((day, lec_start, lec_end))

                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id, 'prof_id': pk, 'professor_key': pk,
                        'professor_name': assigned_prof.get('professor_name'),
                        'section': section_name, 'room_id': lab_rk, 'day': day,
                        'start': lab_start, 'end': lab_end, 'session_type': 'Laboratory'
                    })
                    section_bookings[sec_key].append((day, lab_start, lab_end))
                    room_bookings.setdefault(lab_rk, []).append((day, lab_start, lab_end))
                    assignment_step += 1
                    room_usage[lab_rk] = room_usage.get(lab_rk, 0) + 1
                    professor_bookings.setdefault(pk, []).append((day, lab_start, lab_end))
                    professor_hours[pk] = professor_hours.get(pk, 0.0) + total_dur
                    prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + total_dur
                    prof_scheduled_days.setdefault(pk, set()).add(day)
                    courses_per_day[day] = courses_per_day.get(day, 0) + 1
                    days_tried[day] = days_tried.get(day, 0) + 1
                    if slot_is_late: late_days.add(day)
                    return True
        ok_lab = _schedule_single_session('Laboratory', lab_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
        ok_lec = _schedule_single_session('Lecture', lec_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
        return ok_lec and ok_lab

    for section in all_sections:
        section_name = section['section']
        yr = int(section.get('year_level') or 1)
        sec_major = section.get('major')
        sec_key = (section_name, sec_major)
        section_courses = [c for c in courses_by_year.get(yr, []) if (section_name, c.get('course_id')) in section_course_assignment]
        section_courses.sort(key=lambda c: (-int(c.get('lecture_hours') or 0) if int(c.get('lab_hours') or 0) == 0 else 0, len(professors_by_course.get(c.get('course_id'), [])), c.get('course_id', 0)))
        section_bookings.setdefault(sec_key, [])
        courses_per_day = {}
        two_course_day_used = False
        late_days = set()
        days_tried = {}
        for course in section_courses:
            assigned_prof = section_course_assignment.get((section_name, course['course_id']))
            queue = app._build_subject_session_queue(course)
            for session_item in queue:
                if session_item.get('paired'):
                    _schedule_paired_block(session_item['lec_duration'], session_item['lab_duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
                else:
                    _schedule_single_session(session_item['session_type'], session_item['duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)

    courses_by_id = {c['course_id']: c for c in all_courses}
    sched_by_lid = Counter(e.get('professor_load_id') for e in preview_entries if e.get('professor_load_id'))
    unscheduled = []
    for pl_row in pc_data:
        pcid = pl_row.get('professor_load_id') or pl_row.get('id')
        cid = pl_row.get('course_id')
        c_info = courses_by_id.get(cid)
        if not c_info: continue
        sec_count = int(pl_row.get('sections') or 0)
        lec_h = int(c_info.get('lecture_hours') or 0)
        lab_h = int(c_info.get('lab_hours') or 0)
        ilp_h = int(float(c_info.get('ilp_hours') or 0))
        expected_per_sec = (1 if lec_h > 0 else 0) + (1 if lab_h > 0 else 0) + (1 if ilp_h > 0 else 0)
        total_expected_sessions = expected_per_sec * sec_count
        actual_sessions = sched_by_lid.get(pcid, 0)
        if actual_sessions < total_expected_sessions or (total_expected_sessions == 0 and sec_count > 0):
            assigned_secs = [
                sec_name for (sec_name, sec_cid), l_item in section_course_assignment.items()
                if sec_cid == cid and (l_item.get('professor_load_id') == pcid or (get_professor_key(l_item) == get_professor_key(pl_row) and l_item.get('course_id') == cid))
            ]
            sec_disp = ", ".join(sorted(set(assigned_secs))) if assigned_secs else "Unassigned (No matching section)"
            unscheduled.append({
                'professor': get_professor_name(pl_row),
                'course': c_info.get('course_name'),
                'section': sec_disp,
                'placed': actual_sessions,
                'required': total_expected_sessions,
            })
    return len(preview_entries), unscheduled

print("\n--- Running simulation WITHOUT fixed affinity ---")
cnt_unfixed, unsched_unfixed = run_simulation(use_fixed_affinity=False)
print(f"Entries placed: {cnt_unfixed}, Unscheduled loads count: {len(unsched_unfixed)}")
for u in unsched_unfixed:
    print(f"  {u['professor']} | {u['course']} | {u['section']} | {u['placed']}/{u['required']}")

print("\n--- Running simulation WITH fixed affinity ---")
cnt_fixed, unsched_fixed = run_simulation(use_fixed_affinity=True)
print(f"Entries placed: {cnt_fixed}, Unscheduled loads count: {len(unsched_fixed)}")
for u in unsched_fixed:
    print(f"  {u['professor']} | {u['course']} | {u['section']} | {u['placed']}/{u['required']}")

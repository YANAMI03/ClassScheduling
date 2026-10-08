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

# Re-implement the generator loop with tracing for the 3 target sessions
def run_and_trace():
    # Use Flask app test context so session and everything works
    with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
        app.session['user_id'] = 1
        app.session['role'] = 'scheduler'
        app.session['program'] = 'BSIT'
        app.session['program_id'] = 1
        app.session['access_token'] = auth_res.session.access_token

        # Let's inspect generate_schedule by extracting its core data structures
        # We can run simulate_generation logic matching app.py 100%
        # Let's load everything as app.py does:
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

        pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, prof_id, sections, professor(first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff))').execute()
        all_profs_res = app.supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff)').execute()
        pc_data = pc_res.data or []
        professor_load_map = {}
        professors_by_course = {}
        _all_profs_by_id = {ap.get('prof_id'): ap for ap in (all_profs_res.data or []) if ap.get('prof_id')}

        for row in pc_data:
            pcid = row.get('professor_load_id') or row.get('id') or 1
            cid = row.get('course_id')
            pid = row.get('prof_id')
            if pid and cid:
                professor_load_map[(pid, cid)] = pcid
            p = app._rel(row, 'professor') or _all_profs_by_id.get(pid)
            if p:
                sec_val = int(row.get('sections') or 0)
                if sec_val == 0:
                    sec_val = 999
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
            if _pid is None:
                continue
            _rank = _pr.get('academic_ranking') or {}
            if isinstance(_rank, list):
                _rank = _rank[0] if _rank else {}
            _prof_cutoff_map[_pid] = _rank.get('has_cutoff', True)

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

        # Pre-assign (section, course)
        section_course_assignment = {}
        prof_track_affinity = {}
        for c in all_courses:
            c_yl = int(c.get('year_level') or 1)
            c_spec = c.get('specialization') or c.get('major')
            if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
                for l in professors_by_course.get(c['course_id'], []):
                    prof_track_affinity[(l['prof_id'], c_yl)] = str(c_spec).strip()

        for course in all_courses:
            cid = course['course_id']
            yl = int(course.get('year_level') or 1)
            cmajor = course.get('specialization') or course.get('major')
            matching_secs = [s for s in all_sections if int(s.get('year_level') or 1) == yl and app._major_matches(cmajor, s.get('major'))]
            loads = professors_by_course.get(cid, [])
            if not loads: continue
            is_general = (not cmajor or str(cmajor).strip().lower() in ('general', 'none', ''))
            if is_general and any(s.get('major') for s in matching_secs):
                assigned_sec_names = set()
                unassigned_loads = []
                for l in loads:
                    aff = prof_track_affinity.get((l['prof_id'], yl))
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
                    if rem > 0:
                        unassigned_loads.append((l, rem))
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

        # Audit logger function for candidate blocks at time of failure
        def audit_candidate_grid(target_sec, target_course_name, session_type, duration, assigned_prof, cand_rooms):
            print("\n" + "="*80)
            print(f"REJECTION AUDIT: {assigned_prof.get('first_name')} {assigned_prof.get('last_name')} | {target_course_name} | {target_sec} | {session_type} ({duration}h)")
            print("="*80)
            pk = assigned_prof.get('prof_id')
            sec_key = (target_sec, next((s.get('major') for s in all_sections if s['section'] == target_sec), None))
            rejection_counter = Counter()
            valid_list = []
            grid_details = []

            for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
                day_slots = slot_groups[day]
                if len(day_slots) < duration: continue
                for start_index in range(len(day_slots) - duration + 1):
                    block_slots = day_slots[start_index:start_index + duration]
                    if not app._is_contiguous_block(block_slots): continue
                    st = block_slots[0]['start_time']
                    et = block_slots[-1]['end_time']
                    st_sec = app._to_seconds(st)
                    et_sec = app._to_seconds(et)

                    # Rules evaluation:
                    # 1. 8am rule for non-ILP
                    if st < timedelta(hours=8) and session_type != 'ILP':
                        for rm in cand_rooms:
                            rejection_counter['rejected: outside 8am-8pm range'] += 1
                        continue

                    # 2. Section conflict
                    sec_conflict = app._has_conflict(day, st, et, section_bookings[sec_key])
                    # 3. Prof conflict
                    prof_conflict = app._has_conflict(day, st, et, professor_bookings.get(pk, []))
                    # 4. Daily cutoff
                    cutoff_res = app._check_professor_cutoff_conflict(pk, day, st, et, session_type, prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map)

                    for rm in cand_rooms:
                        rk = rm['room_id']
                        rname = rm['room_name']
                        room_conflict = app._has_conflict(day, st, et, room_bookings.get(rk, []))

                        if cutoff_res:
                            r = f"rejected: past daily cutoff ({_day_cutoff_map.get(day)})"
                        elif sec_conflict:
                            r = "rejected: section already has a class"
                        elif prof_conflict:
                            r = "rejected: professor already teaching"
                        elif room_conflict:
                            r = "rejected: room occupied"
                        else:
                            r = "VALID"
                            valid_list.append((day, str(st), str(et), rname))

                        rejection_counter[r] += 1
                        grid_details.append((day, str(st), str(et), rname, r))

            print(f"Total candidate combinations evaluated: {sum(rejection_counter.values())}")
            for k, v in rejection_counter.most_common():
                print(f"  - {k}: {v}")
            print(f"VALID CANDIDATES: {len(valid_list)}")
            if valid_list:
                for v in valid_list:
                    print(f"  * {v}")
            return rejection_counter, valid_list

        def _can_prof_teach_on_day(prof_dict, check_day):
            pid = prof_dict.get('prof_id')
            if not pid:
                return True
            days_set = prof_scheduled_days.get(pid, set())
            if check_day in days_set:
                return True
            max_days = int(prof_dict.get('time_designation') or 5)
            return len(days_set) < max_days

        # Run section scheduling loop exactly as app.py
        nonlocal_assignment_step = 0
        for section in all_sections:
            section_name = section['section']
            yr = int(section.get('year_level') or 1)
            sec_major = section.get('major')
            sec_key = (section_name, sec_major)
            section_courses = [c for c in courses_by_year.get(yr, []) if (section_name, c.get('course_id')) in section_course_assignment]
            section_courses.sort(
                key=lambda c: (
                    -int(c.get('lecture_hours') or 0) if int(c.get('lab_hours') or 0) == 0 else 0,
                    len(professors_by_course.get(c.get('course_id'), [])),
                    c.get('course_id', 0)
                )
            )
            section_bookings.setdefault(sec_key, [])
            courses_per_day = {}
            two_course_day_used = False
            late_days = set()
            days_tried = {}

            def _inner_schedule_single(session_type, duration, course, assigned_prof):
                nonlocal nonlocal_assignment_step
                pk = assigned_prof.get('prof_id')
                course_id = course['course_id']
                cand_rooms = lab_rooms if session_type == 'Laboratory' else lecture_rooms
                
                # Check day scores
                scored_days = []
                for day in slot_groups.keys():
                    s = 0
                    if courses_per_day.get(day, 0) == 0: s += 50
                    elif courses_per_day.get(day, 0) == 1: s += 20
                    s -= days_tried.get(day, 0) * 10
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
                        if slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days:
                            continue
                        block_start = block_slots[0]['start_time']
                        block_end = block_slots[-1]['end_time']
                        if app._has_conflict(day, block_start, block_end, section_bookings[sec_key]): continue
                        if block_start < timedelta(hours=8) and session_type != 'ILP': continue
                        if app._has_conflict(day, block_start, block_end, professor_bookings.get(pk, [])): continue
                        if not _can_prof_teach_on_day(assigned_prof, day): continue
                        if app._check_professor_cutoff_conflict(pk, day, block_start, block_end, session_type, prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None:
                            continue
                        assigned_room = app._select_least_used_room(cand_rooms, day, block_start, block_end, room_bookings, room_usage, room_last_used, room_order)
                        if not assigned_room: continue

                        # Success
                        rk = assigned_room['room_id']
                        rname = assigned_room.get('room_name') or ''
                        pname = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()
                        preview_entries.append({
                            'professor_load_id': assigned_prof['professor_load_id'],
                            'course_id': course_id, 'course_name': course.get('course_name'),
                            'prof_id': pk, 'professor_name': pname, 'section': section_name,
                            'room_id': rk, 'room_name': rname, 'day': day, 'start': block_start, 'end': block_end,
                            'session_type': session_type, 'semester': standard_semester, 'major': sec_major or course.get('major')
                        })
                        section_bookings[sec_key].append((day, block_start, block_end))
                        room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                        nonlocal_assignment_step += 1
                        room_usage[rk] = room_usage.get(rk, 0) + 1
                        room_last_used[rk] = nonlocal_assignment_step
                        professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                        professor_hours[pk] = professor_hours.get(pk, 0.0) + duration
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + duration
                        prof_scheduled_days.setdefault(pk, set()).add(day)
                        courses_per_day[day] = courses_per_day.get(day, 0) + 1
                        days_tried[day] = days_tried.get(day, 0) + 1
                        if slot_is_late: late_days.add(day)
                        return True
                return False

            for course in section_courses:
                assigned_prof = section_course_assignment.get((section_name, course['course_id']))
                queue = app._build_subject_session_queue(course)
                for session_item in queue:
                    if session_item.get('paired'):
                        # Try paired, else split
                        # We won't re-type paired, just do split if not paired
                        lec_dur = session_item['lec_duration']
                        lab_dur = session_item['lab_duration']
                        total_dur = lec_dur + lab_dur
                        pk = assigned_prof.get('prof_id')
                        paired_ok = False
                        
                        # Paired search
                        scored_days = []
                        for day in slot_groups.keys():
                            s = 0
                            if courses_per_day.get(day, 0) == 0: s += 50
                            elif courses_per_day.get(day, 0) == 1: s += 20
                            s -= days_tried.get(day, 0) * 10
                            scored_days.append((s, day))
                        scored_days.sort(key=lambda x: x[0], reverse=True)

                        late_threshold = timedelta(hours=17)
                        for _, day in scored_days:
                            day_slots = slot_groups[day]
                            if len(day_slots) < total_dur: continue
                            for start_index in range(len(day_slots) - total_dur + 1):
                                full_block = day_slots[start_index:start_index + total_dur]
                                if not app._is_contiguous_block(full_block): continue
                                slot_is_late = app._is_late_slot(full_block[0], late_threshold)
                                if slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days: continue
                                lec_start = full_block[0]['start_time']
                                lec_end = full_block[lec_dur - 1]['end_time'] if lec_dur > 0 else lec_start
                                lab_start = full_block[lec_dur]['start_time'] if lab_dur > 0 else lec_end
                                lab_end = full_block[-1]['end_time']
                                if app._has_conflict(day, lec_start, lab_end, section_bookings[sec_key]): continue
                                if lec_start < timedelta(hours=8): continue
                                if app._has_conflict(day, lec_start, lab_end, professor_bookings.get(pk, [])): continue
                                if not _can_prof_teach_on_day(assigned_prof, day): continue
                                if app._check_professor_cutoff_conflict(pk, day, lec_start, lab_end, 'Lecture', prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None: continue
                                assigned_lec = app._select_least_used_room(lecture_rooms, day, lec_start, lec_end, room_bookings, room_usage, room_last_used, room_order)
                                if not assigned_lec: continue
                                assigned_lab = app._select_least_used_room(lab_rooms, day, lab_start, lab_end, room_bookings, room_usage, room_last_used, room_order)
                                if not assigned_lab: continue

                                # Paired success
                                lec_rk = assigned_lec['room_id']
                                lab_rk = assigned_lab['room_id']
                                pname = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()
                                preview_entries.append({
                                    'professor_load_id': assigned_prof['professor_load_id'],
                                    'course_id': course['course_id'], 'course_name': course.get('course_name'),
                                    'prof_id': pk, 'professor_name': pname, 'section': section_name,
                                    'room_id': lec_rk, 'room_name': assigned_lec.get('room_name'),
                                    'day': day, 'start': lec_start, 'end': lec_end, 'session_type': 'Lecture',
                                    'semester': standard_semester, 'major': sec_major or course.get('major')
                                })
                                section_bookings[sec_key].append((day, lec_start, lec_end))
                                room_bookings.setdefault(lec_rk, []).append((day, lec_start, lec_end))
                                nonlocal_assignment_step += 1
                                room_usage[lec_rk] = room_usage.get(lec_rk, 0) + 1
                                room_last_used[lec_rk] = nonlocal_assignment_step
                                professor_bookings.setdefault(pk, []).append((day, lec_start, lec_end))

                                preview_entries.append({
                                    'professor_load_id': assigned_prof['professor_load_id'],
                                    'course_id': course['course_id'], 'course_name': course.get('course_name'),
                                    'prof_id': pk, 'professor_name': pname, 'section': section_name,
                                    'room_id': lab_rk, 'room_name': assigned_lab.get('room_name'),
                                    'day': day, 'start': lab_start, 'end': lab_end, 'session_type': 'Laboratory',
                                    'semester': standard_semester, 'major': sec_major or course.get('major')
                                })
                                section_bookings[sec_key].append((day, lab_start, lab_end))
                                room_bookings.setdefault(lab_rk, []).append((day, lab_start, lab_end))
                                nonlocal_assignment_step += 1
                                room_usage[lab_rk] = room_usage.get(lab_rk, 0) + 1
                                room_last_used[lab_rk] = nonlocal_assignment_step
                                professor_bookings.setdefault(pk, []).append((day, lab_start, lab_end))
                                professor_hours[pk] = professor_hours.get(pk, 0.0) + total_dur
                                prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + total_dur
                                prof_scheduled_days.setdefault(pk, set()).add(day)
                                courses_per_day[day] = courses_per_day.get(day, 0) + 1
                                days_tried[day] = days_tried.get(day, 0) + 1
                                if slot_is_late: late_days.add(day)
                                paired_ok = True
                                break
                            if paired_ok: break

                        if not paired_ok:
                            # Split
                            ok_lab = _inner_schedule_single('Laboratory', lab_dur, course, assigned_prof)
                            if not ok_lab:
                                audit_candidate_grid(section_name, course.get('course_name'), 'Laboratory', lab_dur, assigned_prof, lab_rooms)
                            ok_lec = _inner_schedule_single('Lecture', lec_dur, course, assigned_prof)
                            if not ok_lec:
                                audit_candidate_grid(section_name, course.get('course_name'), 'Lecture', lec_dur, assigned_prof, lecture_rooms)
                    else:
                        ok_single = _inner_schedule_single(session_item['session_type'], session_item['duration'], course, assigned_prof)
                        if not ok_single:
                            cand_rms = lab_rooms if session_item['session_type'] == 'Laboratory' else lecture_rooms
                            audit_candidate_grid(section_name, course.get('course_name'), session_item['session_type'], session_item['duration'], assigned_prof, cand_rms)

        print("\nAll sections processed! Total entries placed:", len(preview_entries))

if __name__ == '__main__':
    run_and_trace()

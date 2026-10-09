import os
import sys
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# We want to trace candidate evaluations for each unplaced session in run_simulation
from test_full_fixed_generator import (
    all_courses, all_sections, pc_data, professors_by_course, courses_by_year,
    lecture_rooms, lab_rooms, slot_groups, day_order, _day_cutoff_map, _prof_cutoff_map,
    get_professor_key, get_professor_name
)

# Instrument the placement loop to audit rejections when a session fails to place
def audit_failures():
    section_bookings = {(sec['section'], sec.get('major')): [] for sec in all_sections}
    room_bookings = {}
    room_usage = {r['room_id']: 0 for r in lecture_rooms + lab_rooms if r.get('room_id') is not None}
    room_last_used = {r['room_id']: 0 for r in lecture_rooms + lab_rooms if r.get('room_id') is not None}
    room_order = {r['room_id']: idx for idx, r in enumerate(lecture_rooms + lab_rooms) if r.get('room_id') is not None}
    assignment_step = 0
    professor_bookings = {}
    professor_hours = {}
    prof_day_hours = {}
    prof_scheduled_days = {}

    prof_track_affinity = {}
    for c in all_courses:
        c_yl = int(c.get('year_level') or 1)
        c_spec = c.get('specialization') or c.get('major')
        if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
            for l in professors_by_course.get(c['course_id'], []):
                prof_track_affinity[(l['professor_key'], c_yl)] = str(c_spec).strip()

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
                aff = prof_track_affinity.get((l['professor_key'], yl))
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

    def audit_session_rejections(session_type, duration, course, assigned_prof, section_name, yr, sec_key):
        pk = assigned_prof.get('professor_key')
        cand_rooms = lecture_rooms if session_type in ('Lecture', 'ILP') else lab_rooms
        reasons = Counter()
        for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
            day_slots = slot_groups[day]
            if len(day_slots) < duration:
                reasons['day too short'] += 1
                continue
            for start_index in range(len(day_slots) - duration + 1):
                block_slots = day_slots[start_index:start_index + duration]
                if not app._is_contiguous_block(block_slots):
                    reasons['non-contiguous slots'] += 1
                    continue
                block_start = block_slots[0]['start_time']
                block_end = block_slots[-1]['end_time']
                if block_start < timedelta(hours=8) and session_type != 'ILP':
                    reasons['before 8:00 AM'] += 1
                    continue
                sec_busy = app._has_conflict(day, block_start, block_end, section_bookings[sec_key])
                prof_busy = app._has_conflict(day, block_start, block_end, professor_bookings.get(pk, []))
                cutoff_err = app._check_professor_cutoff_conflict(pk, day, block_start, block_end, session_type, prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map)
                
                # Check rooms
                room_avail = False
                for r in cand_rooms:
                    rk = r['room_id']
                    if not app._has_conflict(day, block_start, block_end, room_bookings.get(rk, [])):
                        room_avail = True
                        break
                
                if sec_busy:
                    reasons['section already has class'] += 1
                elif prof_busy:
                    reasons['professor already teaching'] += 1
                elif cutoff_err:
                    reasons['past daily professor cutoff'] += 1
                elif not room_avail:
                    reasons[f'all {len(cand_rooms)} {session_type.lower()} rooms occupied'] += 1
                else:
                    reasons['candidate was valid (skipped by ranking/earlier pass)'] += 1
        return reasons

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
                    section_bookings[sec_key].append((day, lec_start, lec_end))
                    room_bookings.setdefault(lec_rk, []).append((day, lec_start, lec_end))
                    assignment_step += 1
                    room_usage[lec_rk] = room_usage.get(lec_rk, 0) + 1
                    room_last_used[lec_rk] = assignment_step
                    professor_bookings.setdefault(pk, []).append((day, lec_start, lec_end))
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

    failed_sessions = []
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
                    ok = _schedule_paired_block(session_item['lec_duration'], session_item['lab_duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
                    if not ok:
                        failed_sessions.append(('Paired (Lec+Lab)', course, assigned_prof, section_name, yr, sec_key))
                else:
                    ok = _schedule_single_session(session_item['session_type'], session_item['duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
                    if not ok:
                        failed_sessions.append((session_item['session_type'], course, assigned_prof, section_name, yr, sec_key))

    print(f"\nTotal sessions that failed: {len(failed_sessions)}")
    for s_type, course, assigned_prof, section_name, yr, sec_key in failed_sessions:
        print(f"\n--- AUDIT: {course.get('course_name')} ({s_type}) for Section {section_name} ---")
        print(f"    Assigned Professor: {assigned_prof.get('professor_name')} (key: {assigned_prof.get('professor_key')})")
        reasons = audit_session_rejections('Laboratory' if 'Lab' in s_type else 'Lecture', 2, course, assigned_prof, section_name, yr, sec_key)
        for r, cnt in reasons.most_common():
            print(f"      - {r}: {cnt}")

audit_failures()

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
        backup_sc[(sec, cid)] = (pid, prof_id_to_name.get(pid, f'Prof {pid}'))

print(f"Total backup (section, course) pairs: {len(backup_sc)}")

# Now run simulation from test_full_fixed_generator, but force section_course_assignment to match backup_sc!
from test_full_fixed_generator import (
    all_courses, all_sections, pc_data, professors_by_course, courses_by_year,
    lecture_rooms, lab_rooms, slot_groups, day_order, _day_cutoff_map, _prof_cutoff_map,
    get_professor_key, get_professor_name
)
_is_late_slot = app._is_late_slot
_select_least_used_room = app._select_least_used_room
_score_day_for_section = app._score_day_for_section
_has_conflict = app._has_conflict
_is_contiguous_block = app._is_contiguous_block
_check_professor_cutoff_conflict = app._check_professor_cutoff_conflict

from datetime import timedelta
from collections import Counter

# Build forced section_course_assignment:
section_course_assignment = {}
for sec in all_sections:
    s_name = sec['section']
    yr = int(sec.get('year_level') or 1)
    for c in courses_by_year.get(yr, []):
        cid = c.get('course_id')
        if (s_name, cid) in backup_sc:
            pid, pname = backup_sc[(s_name, cid)]
            pkey = app.professor_load_importer.normalize_professor_key(pname)
            # Find in professors_by_course[cid]
            cand = [l for l in professors_by_course.get(cid, []) if l.get('professor_key') == pkey]
            if cand:
                section_course_assignment[(s_name, cid)] = cand[0]
            else:
                print(f"Warning: could not find load for {pname} (course {cid})")

print(f"Built section_course_assignment: {len(section_course_assignment)}")

# Now run simulation loop!
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
preview_entries = []
total_sessions_required = 0

def _schedule_single_session(session_type, duration, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
    global assignment_step
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
            slot_is_late = _is_late_slot(test_slot) if test_slot else False
            s = _score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used, strict=p_config['strict_rules'])
            if s >= 0:
                s -= int(prof_day_hours.get((pk, day), 0.0) * 15)
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
                if not _is_contiguous_block(block_slots): continue
                slot_is_late = _is_late_slot(block_slots[0], late_threshold)
                if p_config['strict_rules'] and slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days:
                    continue
                block_start = block_slots[0]['start_time']
                block_end = block_slots[-1]['end_time']
                if _has_conflict(day, block_start, block_end, section_bookings[sec_key]): continue
                if block_start < timedelta(hours=8) and session_type != 'ILP': continue
                if professor_hours.get(pk, 0.0) + duration > 40: continue
                if _has_conflict(day, block_start, block_end, professor_bookings.get(pk, [])): continue
                if _check_professor_cutoff_conflict(pk, day, block_start, block_end, session_type, prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None:
                    continue
                assigned_room = _select_least_used_room(cand_rooms, day, block_start, block_end, room_bookings, room_usage, room_last_used, room_order)
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
    global assignment_step
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
            slot_is_late = _is_late_slot(test_slot) if test_slot else False
            s = _score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used, strict=p_config['strict_rules'])
            if s >= 0:
                s -= int(prof_day_hours.get((pk, day), 0.0) * 15)
                scored_days.append((s, day))
        scored_days.sort(key=lambda x: x[0], reverse=True)
        late_threshold = timedelta(hours=17)
        for _, day in scored_days:
            day_slots = slot_groups[day]
            if len(day_slots) < total_dur: continue
            for start_index in range(0, len(day_slots) - total_dur + 1):
                full_block = day_slots[start_index:start_index + total_dur]
                if not _is_contiguous_block(full_block): continue
                slot_is_late = _is_late_slot(full_block[0], late_threshold)
                if p_config['strict_rules'] and slot_is_late and len(late_days) >= app._get_year_rules(yr)['max_late_days'] and day not in late_days:
                    continue
                lec_start = full_block[0]['start_time']
                lec_end = full_block[lec_dur - 1]['end_time']
                lab_start = full_block[lec_dur]['start_time']
                lab_end = full_block[-1]['end_time']
                if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]): continue
                if lec_start < timedelta(hours=8): continue
                if professor_hours.get(pk, 0.0) + total_dur > 40: continue
                if _has_conflict(day, lec_start, lab_end, professor_bookings.get(pk, [])): continue
                if _check_professor_cutoff_conflict(pk, day, lec_start, lab_end, 'Lecture', prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None:
                    continue
                assigned_lec_room = _select_least_used_room(lecture_rooms, day, lec_start, lec_end, room_bookings, room_usage, room_last_used, room_order)
                if not assigned_lec_room: continue
                assigned_lab_room = _select_least_used_room(lab_rooms, day, lab_start, lab_end, room_bookings, room_usage, room_last_used, room_order)
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
                room_last_used[lab_rk] = assignment_step
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
            total_sessions_required += 1
            if session_item.get('paired'):
                _schedule_paired_block(session_item['lec_duration'], session_item['lab_duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
            else:
                _schedule_single_session(session_item['session_type'], session_item['duration'], course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)

print(f"\n==================== SIMULATION RESULT ====================")
print(f"Total sessions required: {total_sessions_required}")
print(f"Total sessions placed: {len(preview_entries)}")
print(f"Unscheduled sessions count: {total_sessions_required - len(preview_entries)}")

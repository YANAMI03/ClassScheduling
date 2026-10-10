import re

new_func = """        def _schedule_ilp_session(course, section_name, yr, sec_major, sec_key, courses_per_day, days_tried):
            nonlocal total_sessions_scheduled, prof_tba_count, room_tba_count, assignment_step
            course_id = course['course_id']
            primary_profs = list(professors_by_course.get(course_id, []))
            desig_pid = course_section_prof_map.get((course_id, section_name))
            if desig_pid:
                desig_p = next((p for p in primary_profs if p.get('prof_id') == desig_pid), None)
                if desig_p:
                    primary_profs = [desig_p] + [p for p in primary_profs if p.get('prof_id') != desig_pid]
            
            ilp_day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
            ordered_days = sorted(slot_groups.keys(), key=lambda d: ilp_day_order.get(d, 99))

            eight_am = timedelta(hours=8)
            first_hour_early_slots = []
            for d in ordered_days:
                d_slots = slot_groups.get(d, [])
                if d_slots and d_slots[0]['start_time'] < eight_am:
                    first_hour_early_slots.append((d, d_slots[0]))

            last_hour_slots_raw = []
            for d in ordered_days:
                d_slots = slot_groups.get(d, [])
                if d_slots:
                    last_hour_slots_raw.append((d, d_slots[-1]))

            def _sort_last_hour_slots(prof=None):
                pid = prof.get('prof_id') if prof else None
                def _day_load_score(item):
                    day, slot = item
                    sec_day_load = sum(1 for d, _, _ in section_bookings.get(sec_key, []) if d == day)
                    prof_day_load = prof_day_hours.get((pid, day), 0.0) if pid else 0.0
                    ilp_on_day = sum(1 for e in preview_entries if e.get('session_type') == 'ILP' and e.get('day') == day)
                    courses_on_day = courses_per_day.get(day, 0)
                    return (sec_day_load, prof_day_load, ilp_on_day, courses_on_day, ilp_day_order.get(day, 99))
                return sorted(last_hour_slots_raw, key=_day_load_score)

            def _commit_ilp_entry(day, slot, prof):
                nonlocal total_sessions_scheduled, prof_tba_count, room_tba_count, assignment_step
                block_start = slot['start_time']
                block_end = slot['end_time']
                pk = prof.get('prof_id') if prof else None
                prof_name = f"{prof.get('first_name', '')} {prof.get('last_name', '')}".strip() if prof else 'TBA'
                assigned_load_id = _resolve_load_id(prof, course_id) if (prof and pk) else None

                assigned_room = _select_least_used_room(
                    lecture_rooms, day, block_start, block_end,
                    room_bookings, room_usage, room_last_used, room_order
                )
                rk = assigned_room['room_id'] if assigned_room else None
                room_name = assigned_room.get('room_name') if assigned_room else 'TBA'

                if not pk:
                    prof_tba_count += 1
                if not rk:
                    room_tba_count += 1

                preview_entries.append({
                    'professor_load_id': assigned_load_id,
                    'course_id': course_id,
                    'course_code': course.get('course_code'),
                    'prof_id': pk,
                    'professor_name': prof_name,
                    'section': section_name,
                    'room_id': rk,
                    'room_name': room_name,
                    'day': day,
                    'start': block_start,
                    'end': block_end,
                    'session_type': 'ILP',
                    'semester': standard_semester,
                    'major': sec_major or course.get('major'),
                    'program': course.get('program') or program or session.get('program', ''),
                })

                section_bookings[sec_key].append((day, block_start, block_end))
                if rk:
                    room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                    assignment_step += 1
                    room_usage[rk] = room_usage.get(rk, 0) + 1
                    room_last_used[rk] = assignment_step
                if pk:
                    professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                    professor_hours[pk] = professor_hours.get(pk, 0.0) + 1
                    prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + 1
                    prof_scheduled_days.setdefault(pk, set()).add(day)
                    prof_section_count[pk] = prof_section_count.get(pk, 0) + 1

                courses_per_day[day] = courses_per_day.get(day, 0) + 1
                days_tried[day] = days_tried.get(day, 0) + 1
                total_sessions_scheduled += 1
                return True

            for prof in primary_profs:
                pk = prof.get('prof_id')
                for day, slot in first_hour_early_slots:
                    if not _has_conflict(day, slot['start_time'], slot['end_time'], section_bookings[sec_key]) and \\
                       not _has_conflict(day, slot['start_time'], slot['end_time'], professor_bookings.get(pk, [])):
                        return _commit_ilp_entry(day, slot, prof)

                for day, slot in _sort_last_hour_slots(prof):
                    if not _has_conflict(day, slot['start_time'], slot['end_time'], section_bookings[sec_key]) and \\
                       not _has_conflict(day, slot['start_time'], slot['end_time'], professor_bookings.get(pk, [])):
                        return _commit_ilp_entry(day, slot, prof)

            # If we reach here, we must assign to TBA
            default_day, default_slot = first_hour_early_slots[0] if first_hour_early_slots else (last_hour_slots_raw[0] if last_hour_slots_raw else ('Monday', {'start_time': timedelta(hours=7), 'end_time': timedelta(hours=8)}))
            
            # Find conflict free slot for section
            found_sec_slot = False
            for day, slot in first_hour_early_slots + _sort_last_hour_slots():
                if not _has_conflict(day, slot['start_time'], slot['end_time'], section_bookings[sec_key]):
                    default_day, default_slot = day, slot
                    found_sec_slot = True
                    break
            
            prof_name_fallback = primary_profs[0].get('last_name', 'X') if primary_profs else 'X'
            generation_warnings.append(
                f"ILP for {course.get('course_code')} could not be placed for Professor {prof_name_fallback} and was set to TBA"
            )
            return _commit_ilp_entry(default_day, default_slot, None)"""

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

pattern = re.compile(r'        def _schedule_ilp_session\(course, section_name, yr, sec_major, sec_key, courses_per_day, days_tried\):.*?return _commit_ilp_entry\(default_day, default_slot, None, is_fallback=True\)', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(new_func, content)
else:
    # If the previous regex fails due to intermediate edits, try a looser pattern
    pattern = re.compile(r'        def _schedule_ilp_session\(course, section_name, yr, sec_major, sec_key, courses_per_day, days_tried\):.*?return _commit_ilp_entry\(default_day, default_slot, None\)', re.DOTALL)
    if pattern.search(content):
        content = pattern.sub(new_func, content)
    else:
        pattern = re.compile(r'        def _schedule_ilp_session\(course, section_name, yr, sec_major, sec_key, courses_per_day, days_tried\):.*?(?=\n        def _schedule_single_session)', re.DOTALL)
        content = pattern.sub(new_func + '\n', content)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

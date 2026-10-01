import re

def process_file():
    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    single_fallback = """
            # If all slots failed, assign TBA to the first slot where the section and a room are free
            for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
                for start_index in range(0, len(slot_groups[day]) - duration + 1):
                    block_slots = slot_groups[day][start_index:start_index + duration]
                    if not _is_contiguous_block(block_slots):
                        continue
                    block_start = block_slots[0]['start_time']
                    block_end = block_slots[-1]['end_time']
                    
                    if _has_conflict(day, block_start, block_end, section_bookings[sec_key]):
                        continue
                    if block_start < timedelta(hours=8) and session_type != 'ILP':
                        continue
                        
                    assigned_room = _select_least_used_room(
                        cand_rooms, day, block_start, block_end,
                        room_bookings, room_usage, room_last_used, room_order
                    )
                    
                    rk = assigned_room['room_id'] if assigned_room else None
                    if not rk: continue
                    
                    room_name = assigned_room.get('room_name')
                    pk = None
                    prof_name = 'TBA'
                    prof_tba_count += 1
                    assigned_professor_load_id = None
                    
                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id,
                        'course_name': course.get('course_name'),
                        'prof_id': pk,
                        'professor_name': prof_name,
                        'section': section_name,
                        'room_id': rk,
                        'room_name': room_name,
                        'day': day,
                        'start': block_start,
                        'end': block_end,
                        'session_type': session_type,
                        'semester': standard_semester,
                        'major': sec_major or course.get('major'),
                        'program': course.get('program') or program or session.get('program', ''),
                    })
                    
                    section_bookings[sec_key].append((day, block_start, block_end))
                    room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                    assignment_step += 1
                    room_usage[rk] = room_usage.get(rk, 0) + 1
                    room_last_used[rk] = assignment_step
                    
                    courses_per_day[day] = courses_per_day.get(day, 0) + 1
                    days_tried[day] = days_tried.get(day, 0) + 1
                    total_sessions_scheduled += 1
                    
                    generation_warnings.append(
                        f"{session_type} for {course.get('course_name')} could not be placed for Professor {primary_profs[0].get('last_name', 'X') if primary_profs else 'X'} and was set to TBA"
                    )
                    return True

            return False"""

    content = re.sub(
        r'                    courses_per_day\[day\] = courses_per_day\.get\(day, 0\) \+ 1\n                    days_tried\[day\] = days_tried\.get\(day, 0\) \+ 1\n                    total_sessions_scheduled \+= 1\n                    return True\n\n            return False',
        r'                    courses_per_day[day] = courses_per_day.get(day, 0) + 1\n                    days_tried[day] = days_tried.get(day, 0) + 1\n                    total_sessions_scheduled += 1\n                    return True\n' + single_fallback,
        content
    )

    paired_fallback = """
            # If all paired slots failed, assign TBA
            for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
                day_slots = slot_groups[day]
                if len(day_slots) < total_dur: continue
                for start_index in range(0, len(day_slots) - total_dur + 1):
                    block_slots = day_slots[start_index:start_index + total_dur]
                    if not _is_contiguous_block(block_slots): continue
                    
                    lec_start = block_slots[0]['start_time']
                    lec_end = block_slots[lec_dur - 1]['end_time']
                    lab_start = block_slots[lec_dur]['start_time']
                    lab_end = block_slots[-1]['end_time']
                    
                    if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]): continue
                    if lec_start < timedelta(hours=8): continue
                    
                    assigned_lec_room = _select_least_used_room(lecture_rooms, day, lec_start, lec_end, room_bookings, room_usage, room_last_used, room_order)
                    if not assigned_lec_room: continue
                    assigned_lab_room = _select_least_used_room(lab_rooms, day, lab_start, lab_end, room_bookings, room_usage, room_last_used, room_order)
                    if not assigned_lab_room: continue
                    
                    pk = None
                    prof_name = 'TBA'
                    prof_tba_count += 2
                    assigned_professor_load_id = None
                    lec_rk = assigned_lec_room['room_id']
                    lab_rk = assigned_lab_room['room_id']
                    
                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id,
                        'course_name': course.get('course_name'),
                        'prof_id': pk,
                        'professor_name': prof_name,
                        'section': section_name,
                        'room_id': lec_rk,
                        'room_name': assigned_lec_room.get('room_name'),
                        'day': day,
                        'start': lec_start,
                        'end': lec_end,
                        'session_type': 'Lecture',
                        'semester': standard_semester,
                        'major': sec_major or course.get('major'),
                        'program': course.get('program') or program or session.get('program', ''),
                    })
                    
                    preview_entries.append({
                        'professor_load_id': assigned_professor_load_id,
                        'course_id': course_id,
                        'course_name': course.get('course_name'),
                        'prof_id': pk,
                        'professor_name': prof_name,
                        'section': section_name,
                        'room_id': lab_rk,
                        'room_name': assigned_lab_room.get('room_name'),
                        'day': day,
                        'start': lab_start,
                        'end': lab_end,
                        'session_type': 'Laboratory',
                        'semester': standard_semester,
                        'major': sec_major or course.get('major'),
                        'program': course.get('program') or program or session.get('program', ''),
                    })
                    
                    section_bookings[sec_key].append((day, lec_start, lab_end))
                    room_bookings.setdefault(lec_rk, []).append((day, lec_start, lec_end))
                    room_bookings.setdefault(lab_rk, []).append((day, lab_start, lab_end))
                    assignment_step += 1
                    room_usage[lec_rk] = room_usage.get(lec_rk, 0) + 1
                    room_last_used[lec_rk] = assignment_step
                    assignment_step += 1
                    room_usage[lab_rk] = room_usage.get(lab_rk, 0) + 1
                    room_last_used[lab_rk] = assignment_step
                    
                    courses_per_day[day] = courses_per_day.get(day, 0) + 1
                    days_tried[day] = days_tried.get(day, 0) + 1
                    total_sessions_scheduled += 2
                    
                    generation_warnings.append(
                        f"Lecture/Lab for {course.get('course_name')} could not be placed for Professor {primary_profs[0].get('last_name', 'X') if primary_profs else 'X'} and was set to TBA"
                    )
                    return True

            return False"""

    content = re.sub(
        r'                    courses_per_day\[day\] = courses_per_day\.get\(day, 0\) \+ 1\n                    days_tried\[day\] = days_tried\.get\(day, 0\) \+ 1\n                    total_sessions_scheduled \+= 2\n                    return True\n\n            return False',
        r'                    courses_per_day[day] = courses_per_day.get(day, 0) + 1\n                    days_tried[day] = days_tried.get(day, 0) + 1\n                    total_sessions_scheduled += 2\n                    return True\n' + paired_fallback,
        content
    )

    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    process_file()

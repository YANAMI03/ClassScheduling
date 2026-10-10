import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'cand_profs = [' in l:
        lines.insert(i, '''
                        print(f"DEBUG CHECKING PROFS FOR {course['course_code']} {session_type} on {day} {block_start}-{block_end} PASS {p_config['strict_rules']}")
                        for p in prof_pool:
                            print(f"  Prof {p.get('last_name')}:")
                            print(f"    max_hours check: {professor_hours.get(p['prof_id'], 0.0) + duration} <= {p.get('max_hours', 40)}")
                            print(f"    conflict check: {not _has_conflict(day, block_start, block_end, professor_bookings.get(p['prof_id'], []))}")
                            print(f"    quota check: {professor_load_count.get((p['prof_id'], course_id), 0)} < {p.get('quota', 999)}")
                            print(f"    day check: {not p_config['strict_rules'] or _can_prof_teach_on_day(p, day)}")
''')
        break

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

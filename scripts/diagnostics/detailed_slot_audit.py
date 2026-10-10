import os
import sys
import copy
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

# Authenticate supabase with admin credentials
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Run Flask test client to execute generate_schedule with intercepting hooks
test_client = app.app.test_client()

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

response = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)

with test_client.session_transaction() as sess:
    unscheduled = sess.get('unscheduled_loads', [])
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

print(f"Generated {len(preview)} entries. Unscheduled: {len(unscheduled)}")

# Let's inspect the final preview entries
# Build booking tables from preview:
# section -> list of (day, start, end, course, session_type, prof)
# prof_id -> list of (day, start, end, course, section, room)
# room_id -> list of (day, start, end, section, course)

sec_sched = {}
prof_sched = {}
room_sched = {}
prof_hours = {}

for e in preview:
    d = e['day']
    st = app._parse_time(e['start']) if isinstance(e['start'], str) else e['start']
    et = app._parse_time(e['end']) if isinstance(e['end'], str) else e['end']
    dur = (app._to_seconds(et) - app._to_seconds(st)) / 3600.0
    sec = e['section']
    pid = e['prof_id']
    rid = e['room_id']

    sec_sched.setdefault(sec, []).append((d, st, et, e['course_code'], e['session_type']))
    prof_sched.setdefault(pid, []).append((d, st, et, e['course_code'], sec, e.get('room_name')))
    room_sched.setdefault(rid, []).append((d, st, et, sec, e['course_code']))
    prof_hours[pid] = prof_hours.get(pid, 0.0) + dur

# Fetch rooms & working_hours
rooms = app.supabase.table('room').select('*').eq('program_id', 1).execute().data or []
working_hours = app.supabase.table('working_hours').select('*').execute().data or []
working_hours.sort(key=lambda t: str(t.get('start_time') or ''))
candidate_slots = app._build_candidate_slots(working_hours)
slot_groups = {}
for slot in candidate_slots:
    slot_groups.setdefault(slot['day'], []).append(slot)

day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}

_day_cutoff_map = {}
for _ts in working_hours:
    _d = (_ts.get('day') or '').strip()
    _cutoff = _ts.get('professor_cutoff')
    if _d and _cutoff and _d not in _day_cutoff_map:
        _day_cutoff_map[_d] = _cutoff

# Helper to enumerate all candidate blocks of given duration
def get_candidate_blocks(duration):
    blocks = []
    for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
        day_slots = slot_groups[day]
        for i in range(len(day_slots) - duration + 1):
            sub = day_slots[i:i + duration]
            if app._is_contiguous_block(sub):
                st = sub[0]['start_time']
                et = sub[-1]['end_time']
                if st >= timedelta(hours=8): # non-ILP
                    blocks.append((day, st, et))
    return blocks

lec_rooms = [r for r in rooms if app._is_lecture_room_type(r.get('room_type'))]
lab_rooms = [r for r in rooms if app._is_lab_room_type(r.get('room_type'))]

print(f"\nTotal candidate 2h blocks: {len(get_candidate_blocks(2))}")
print(f"Total candidate 3h blocks: {len(get_candidate_blocks(3))}")
print(f"Lecture rooms: {len(lec_rooms)}, Lab rooms: {len(lab_rooms)}")

# Function to audit a session
def audit_session(target_prof_id, prof_name, target_sec, target_course, session_type, duration, target_rooms):
    cand_blocks = get_candidate_blocks(duration)
    rejection_counts = Counter()
    detailed_results = []
    
    # Check max hours constraint first
    prof_current_h = prof_hours.get(target_prof_id, 0.0)
    # Academic ranking max hours
    prof_max_h = 25 # All three are Regular (25h)
    
    hours_exceeded = (prof_current_h + duration > prof_max_h)

    valid_slots = []

    for day, st, et in cand_blocks:
        st_sec = app._to_seconds(st)
        et_sec = app._to_seconds(et)
        
        # 1. Section conflict
        sec_has_conflict = False
        sec_conflicting_class = None
        for (sd, s_st, s_et, cname, stype) in sec_sched.get(target_sec, []):
            if sd == day and max(st_sec, app._to_seconds(s_st)) < min(et_sec, app._to_seconds(s_et)):
                sec_has_conflict = True
                sec_conflicting_class = f"{cname} ({stype} {s_st}-{s_et})"
                break
                
        # 2. Professor cutoff conflict
        cutoff = _day_cutoff_map.get(day)
        is_cutoff_conflict = False
        if cutoff and et_sec > app._to_seconds(cutoff):
            is_cutoff_conflict = True

        # 3. Professor booking conflict
        prof_has_conflict = False
        prof_conflicting_class = None
        for (pd, p_st, p_et, cname, sec, rm) in prof_sched.get(target_prof_id, []):
            if pd == day and max(st_sec, app._to_seconds(p_st)) < min(et_sec, app._to_seconds(p_et)):
                prof_has_conflict = True
                prof_conflicting_class = f"{cname} in {sec} ({p_st}-{p_et})"
                break

        # Check for each room
        for rm in target_rooms:
            rid = rm['room_id']
            rname = rm['room_name']
            
            # Check room conflict
            room_has_conflict = False
            room_conflicting_class = None
            for (rd, r_st, r_et, rsec, rcname) in room_sched.get(rid, []):
                if rd == day and max(st_sec, app._to_seconds(r_st)) < min(et_sec, app._to_seconds(r_et)):
                    room_has_conflict = True
                    room_conflicting_class = f"{rcname} ({rsec} {r_st}-{r_et})"
                    break

            # Now determine first rejection reason according to standard precedence
            # 1. Academic ranking limit
            if hours_exceeded:
                first_reason = "rejected: academic ranking limit (hours exceeded)"
            # 2. Outside 7am-8pm or past daily cutoff
            elif is_cutoff_conflict:
                first_reason = f"rejected: past daily cutoff ({cutoff})"
            # 3. Section conflict
            elif sec_has_conflict:
                first_reason = f"rejected: section already has a class ({sec_conflicting_class})"
            # 4. Professor conflict
            elif prof_has_conflict:
                first_reason = f"rejected: professor already teaching ({prof_conflicting_class})"
            # 5. Room occupied
            elif room_has_conflict:
                first_reason = f"rejected: room occupied ({room_conflicting_class})"
            else:
                first_reason = "VALID"
                valid_slots.append((day, str(st), str(et), rname))

            rejection_counts[first_reason.split(' (')[0]] += 1
            detailed_results.append({
                'day': day, 'start': str(st), 'end': str(et), 'room': rname,
                'first_reason': first_reason
            })

    return rejection_counts, valid_slots, detailed_results

print("\n" + "="*80)
print("AUDIT 1: Prof Tambio | IT-IAS02 | Section 3C-Networking | Laboratory (2h)")
print("="*80)
print(f"Prof Tambio current placed hours: {prof_hours.get(19)}h / 25h max")
counts1, valids1, details1 = audit_session(19, 'Christian Noli C. Tambio', '3C-Networking', 'IT-IAS02', 'Laboratory', 2, lab_rooms)
print("\nRejection Breakdown across all candidate (block, room) combinations (total =", sum(counts1.values()), "):")
for k, v in counts1.most_common():
    print(f"  - {k}: {v}")
print(f"\nVALID SLOTS: {len(valids1)}")
for vs in valids1[:10]:
    print(f"  * {vs}")

print("\n" + "="*80)
print("AUDIT 2: Prof Ronald Santos | IT-CAP01 (NST) | Section 3F-Networking | Lecture (3h)")
print("="*80)
print(f"Prof Santos current placed hours: {prof_hours.get(34)}h / 25h max")
counts2, valids2, details2 = audit_session(34, 'Ronald S. Santos', '3F-Networking', 'IT-CAP01 (NST)', 'Lecture', 3, lec_rooms)
print("\nRejection Breakdown across all candidate (block, room) combinations (total =", sum(counts2.values()), "):")
for k, v in counts2.most_common():
    print(f"  - {k}: {v}")
print(f"\nVALID SLOTS: {len(valids2)}")
for vs in valids2[:10]:
    print(f"  * {vs}")

print("\n" + "="*80)
print("AUDIT 3: Prof Jev Corpuz | IT-NET05 | Section 3F-Networking | Laboratory (2h)")
print("="*80)
print(f"Prof Corpuz current placed hours: {prof_hours.get(35)}h / 25h max")
counts3, valids3, details3 = audit_session(35, 'Jev D. Corpuz', '3F-Networking', 'IT-NET05', 'Laboratory', 2, lab_rooms)
print("\nRejection Breakdown across all candidate (block, room) combinations (total =", sum(counts3.values()), "):")
for k, v in counts3.most_common():
    print(f"  - {k}: {v}")
print(f"\nVALID SLOTS: {len(valids3)}")
for vs in valids3[:10]:
    print(f"  * {vs}")

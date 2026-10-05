import os
import sys
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

test_client = app.app.test_client()

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

response = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)

with test_client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))
    unscheduled = sess.get('unscheduled_loads', [])

# 1. Total room-hours available per room type per week
# Operating hours per week:
# Monday: 07:00 to 19:00 (12 hours) - 1 hour lunch = 11 teaching hours
# Tuesday: 08:00 to 20:00 (12 hours) - 1 hour lunch = 11 teaching hours
# Wednesday: 08:00 to 20:00 (12 hours) - 1 hour lunch = 11 teaching hours
# Thursday: 08:00 to 20:00 (12 hours) - 1 hour lunch = 11 teaching hours
# Friday: 08:00 to 20:00 (12 hours) - 1 hour lunch = 11 teaching hours
# Total weekly teaching hours per room = 11 * 5 = 55 hours per room!
#
# But for classes ending before professor cutoff (regular faculty):
# Monday: 08:00 to 16:00 - 1h lunch = 7 teaching hours
# Tue-Fri: 08:00 to 17:00 - 1h lunch = 8 teaching hours per day * 4 = 32 hours
# Total cutoff-compliant hours per room = 7 + 32 = 39 hours per room!

rooms = app.supabase.table('room').select('*').eq('program_id', 1).execute().data or []
lec_rooms = [r for r in rooms if app._is_lecture_room_type(r.get('room_type'))]
lab_rooms = [r for r in rooms if app._is_lab_room_type(r.get('room_type'))]

num_lec_rooms = len(lec_rooms) # 12
num_lab_rooms = len(lab_rooms) # 7

total_avail_lec_room_hours = num_lec_rooms * 55 # 660 hours
total_avail_lab_room_hours = num_lab_rooms * 55 # 385 hours

cutoff_avail_lec_room_hours = num_lec_rooms * 39 # 468 hours
cutoff_avail_lab_room_hours = num_lab_rooms * 39 # 273 hours

# 2. Total room-hours required by all loads across the whole semester
calc_result = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester')

# Calculate required room hours across all sections
# For each year level:
total_req_lec_hours = 0
total_req_lab_hours = 0

# Y3 Networking specific
y3_net_req_lec_hours = 0
y3_net_req_lab_hours = 0

for b in calc_result.get('breakdown', []):
    yl = b['year_level']
    if not b.get('is_specialized'):
        sec_cnt = b['section_count']
        for c in b['courses']:
            cid = c['course_id']
            # fetch c info
            c_info = app.supabase.table('course').select('lecture_hours, lab_hours, ilp_hours').eq('course_id', cid).single().execute().data
            lec_h = int(c_info.get('lecture_hours') or 0)
            lab_h = int(c_info.get('lab_hours') or 0)
            ilp_h = int(float(c_info.get('ilp_hours') or 0))
            # Lecture rooms host Lecture + ILP
            total_req_lec_hours += (lec_h + ilp_h) * sec_cnt
            total_req_lab_hours += lab_h * sec_cnt
    else:
        for grp in b.get('specialization_groups', []):
            spec = grp['specialization']
            sec_cnt = grp['section_count']
            for c in grp['courses']:
                cid = c['course_id']
                c_info = app.supabase.table('course').select('lecture_hours, lab_hours, ilp_hours').eq('course_id', cid).single().execute().data
                lec_h = int(c_info.get('lecture_hours') or 0)
                lab_h = int(c_info.get('lab_hours') or 0)
                ilp_h = int(float(c_info.get('ilp_hours') or 0))
                total_req_lec_hours += (lec_h + ilp_h) * sec_cnt
                total_req_lab_hours += lab_h * sec_cnt
                if spec == 'Networking':
                    y3_net_req_lec_hours += (lec_h + ilp_h) * sec_cnt
                    y3_net_req_lab_hours += lab_h * sec_cnt
        # General courses
        for gc in b.get('general_courses', []):
            cid = gc['course_id']
            sec_cnt = gc['sections']
            c_info = app.supabase.table('course').select('lecture_hours, lab_hours, ilp_hours').eq('course_id', cid).single().execute().data
            lec_h = int(c_info.get('lecture_hours') or 0)
            lab_h = int(c_info.get('lab_hours') or 0)
            ilp_h = int(float(c_info.get('ilp_hours') or 0))
            total_req_lec_hours += (lec_h + ilp_h) * sec_cnt
            total_req_lab_hours += lab_h * sec_cnt
            # For Networking (6 sections of general course)
            y3_net_req_lec_hours += (lec_h + ilp_h) * 6
            y3_net_req_lab_hours += lab_h * 6

print(f"=== TOTAL ROOM CAPACITY VS DEMAND ===")
print(f"Lecture Rooms: {num_lec_rooms}")
print(f"  Available Total Room-Hours (7am-8pm): {total_avail_lec_room_hours}h")
print(f"  Available Pre-Cutoff Room-Hours (8am-4/5pm): {cutoff_avail_lec_room_hours}h")
print(f"  Total Required Lecture Room-Hours: {total_req_lec_hours}h")
print(f"  Lecture Utilization (Pre-cutoff): {total_req_lec_hours / cutoff_avail_lec_room_hours * 100:.1f}%")

print(f"\nLaboratory Rooms: {num_lab_rooms}")
print(f"  Available Total Room-Hours (7am-8pm): {total_avail_lab_room_hours}h")
print(f"  Available Pre-Cutoff Room-Hours (8am-4/5pm): {cutoff_avail_lab_room_hours}h")
print(f"  Total Required Lab Room-Hours: {total_req_lab_hours}h")
print(f"  Lab Utilization (Total): {total_req_lab_hours / total_avail_lab_room_hours * 100:.1f}%")
print(f"  Lab Utilization (Pre-cutoff): {total_req_lab_hours / cutoff_avail_lab_room_hours * 100:.1f}%")

print(f"\n=== YEAR 3 NETWORKING TRACK DEMAND ===")
print(f"Networking Sections: 6 (3A-3F)")
print(f"  Required Lecture Room-Hours: {y3_net_req_lec_hours}h")
print(f"  Required Lab Room-Hours: {y3_net_req_lab_hours}h")

# Actual room hours booked in preview
booked_lec_hours = sum((app._to_seconds(app._parse_time(e['end'])) - app._to_seconds(app._parse_time(e['start']))) / 3600.0 for e in preview if app._is_lecture_room_type(e.get('session_type')))
# Wait, room type
booked_lec_room_hours = 0
booked_lab_room_hours = 0
for e in preview:
    dur = (app._to_seconds(app._parse_time(e['end'])) - app._to_seconds(app._parse_time(e['start']))) / 3600.0
    r_type = next((r['room_type'] for r in rooms if r['room_id'] == e['room_id']), None)
    if app._is_lecture_room_type(r_type):
        booked_lec_room_hours += dur
    else:
        booked_lab_room_hours += dur

print(f"\n=== ACTUAL BOOKED ROOM HOURS IN GENERATED PREVIEW ===")
print(f"Booked Lecture Room-Hours: {booked_lec_room_hours}h / {total_avail_lec_room_hours}h ({booked_lec_room_hours/total_avail_lec_room_hours*100:.1f}%)")
print(f"Booked Lab Room-Hours: {booked_lab_room_hours}h / {total_avail_lab_room_hours}h ({booked_lab_room_hours/total_avail_lab_room_hours*100:.1f}%)")
print(f"Booked Lab Room-Hours vs Pre-Cutoff ({cutoff_avail_lab_room_hours}h): {booked_lab_room_hours/cutoff_avail_lab_room_hours*100:.1f}%")

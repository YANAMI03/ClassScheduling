import os
import sys
from datetime import timedelta
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

# Let's inspect Lab rooms usage during candidate slots for Tambio (3C-Networking, 2h Lab)
# Potential slots:
# Monday 08:00-10:00
# Tuesday 10:00-12:00
# Wednesday 08:00-10:00
# Wednesday 10:00-12:00
# Thursday 08:00-10:00
# Friday 08:00-10:00
# Friday 15:00-17:00

slots_to_check = [
    ('Monday', timedelta(hours=8), timedelta(hours=10)),
    ('Tuesday', timedelta(hours=10), timedelta(hours=12)),
    ('Wednesday', timedelta(hours=8), timedelta(hours=10)),
    ('Wednesday', timedelta(hours=10), timedelta(hours=12)),
    ('Thursday', timedelta(hours=8), timedelta(hours=10)),
    ('Friday', timedelta(hours=8), timedelta(hours=10)),
    ('Friday', timedelta(hours=15), timedelta(hours=17)),
]

print("=== LAB ROOM OCCUPANCY DURING CANDIDATE SLOTS FOR 3C-NETWORKING LAB ===")
lab_rooms = [r for r in app.supabase.table('room').select('*').eq('program_id', 1).execute().data or [] if app._is_lab_room_type(r.get('room_type'))]

for d, st, et in slots_to_check:
    st_sec = app._to_seconds(st)
    et_sec = app._to_seconds(et)
    print(f"\n--- {d} {st} - {et} ---")
    
    # Check 3C-Networking conflict
    s3c_entries = [e for e in preview if e['section'] == '3C-Networking' and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
    print(f"  3C-Networking has class? {bool(s3c_entries)} -> {[e['course_name'] for e in s3c_entries]}")
    
    # Check Prof Tambio conflict
    tambio_entries = [e for e in preview if e.get('prof_id') == 19 and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
    print(f"  Prof Tambio has class? {bool(tambio_entries)} -> {[e['course_name'] + ' in ' + e['section'] for e in tambio_entries]}")
    
    # Check each lab room
    booked_labs = []
    free_labs = []
    for lr in lab_rooms:
        rid = lr['room_id']
        rname = lr['room_name']
        occupants = [e for e in preview if e.get('room_id') == rid and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
        if occupants:
            booked_labs.append(f"{rname} (booked by {occupants[0]['section']} {occupants[0]['course_name']})")
        else:
            free_labs.append(rname)
    print(f"  Free labs ({len(free_labs)}/7): {free_labs}")
    print(f"  Booked labs ({len(booked_labs)}/7): {booked_labs}")

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

# Let's inspect candidate 3h blocks for Ronald Santos (3F-Networking, IT-CAP01 Lecture 3h)
# Prof Santos has cutoff: Monday 16:00, Tue-Fri 17:00
# Possible 3h blocks before cutoff:
# Monday: 08:00-11:00, 09:00-12:00, 13:00-16:00
# Tue-Fri: 08:00-11:00, 09:00-12:00, 13:00-16:00, 14:00-17:00

slots_3h = [
    ('Monday', timedelta(hours=8), timedelta(hours=11)),
    ('Monday', timedelta(hours=9), timedelta(hours=12)),
    ('Monday', timedelta(hours=13), timedelta(hours=16)),
    ('Tuesday', timedelta(hours=8), timedelta(hours=11)),
    ('Tuesday', timedelta(hours=9), timedelta(hours=12)),
    ('Tuesday', timedelta(hours=13), timedelta(hours=16)),
    ('Tuesday', timedelta(hours=14), timedelta(hours=17)),
    ('Wednesday', timedelta(hours=8), timedelta(hours=11)),
    ('Wednesday', timedelta(hours=9), timedelta(hours=12)),
    ('Wednesday', timedelta(hours=13), timedelta(hours=16)),
    ('Wednesday', timedelta(hours=14), timedelta(hours=17)),
    ('Thursday', timedelta(hours=8), timedelta(hours=11)),
    ('Thursday', timedelta(hours=9), timedelta(hours=12)),
    ('Thursday', timedelta(hours=13), timedelta(hours=16)),
    ('Thursday', timedelta(hours=14), timedelta(hours=17)),
    ('Friday', timedelta(hours=8), timedelta(hours=11)),
    ('Friday', timedelta(hours=9), timedelta(hours=12)),
    ('Friday', timedelta(hours=13), timedelta(hours=16)),
    ('Friday', timedelta(hours=14), timedelta(hours=17)),
]

print("=== CANDIDATE 3H SLOTS FOR PROF SANTOS | 3F-NETWORKING | IT-CAP01 LECTURE ===")
lec_rooms = [r for r in app.supabase.table('room').select('*').eq('program_id', 1).execute().data or [] if app._is_lecture_room_type(r.get('room_type'))]

for d, st, et in slots_3h:
    st_sec = app._to_seconds(st)
    et_sec = app._to_seconds(et)
    print(f"\n--- {d} {st} - {et} ---")
    
    # Check 3F conflict
    s3f_entries = [e for e in preview if e['section'] == '3F-Networking' and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
    print(f"  3F-Networking has class? {bool(s3f_entries)} -> {[e['course_name'] + ' (' + str(e['start']) + '-' + str(e['end']) + ')' for e in s3f_entries]}")
    
    # Check Prof Santos conflict
    santos_entries = [e for e in preview if e.get('prof_id') == 34 and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
    print(f"  Prof Santos has class? {bool(santos_entries)} -> {[e['course_name'] + ' in ' + e['section'] + ' (' + str(e['start']) + '-' + str(e['end']) + ')' for e in santos_entries]}")

    # Check free lecture rooms
    free_lecs = []
    for lr in lec_rooms:
        rid = lr['room_id']
        rname = lr['room_name']
        occ = [e for e in preview if e.get('room_id') == rid and e['day'] == d and max(st_sec, app._to_seconds(app._parse_time(e['start']))) < min(et_sec, app._to_seconds(app._parse_time(e['end'])))]
        if not occ:
            free_lecs.append(rname)
    print(f"  Free lecture rooms ({len(free_lecs)}/12): {free_lecs}")

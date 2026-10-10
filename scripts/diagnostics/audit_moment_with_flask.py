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

test_client = app.app.test_client()

with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

# Let's inspect the exact timetable for Section 3C-Networking and Section 3F-Networking
# from the generation preview
response = test_client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)

with test_client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))
    unscheduled = sess.get('unscheduled_loads', [])

print(f"Total preview entries: {len(preview)}")
print(f"Unscheduled loads: {len(unscheduled)}")

# 1. Print all scheduled classes for Section 3C-Networking
print("\n" + "="*80)
print("SECTION 3C-NETWORKING: CURRENT SCHEDULE")
print("="*80)
s3c = [e for e in preview if e['section'] == '3C-Networking']
s3c.sort(key=lambda e: (e['day'], e['start']))
for e in s3c:
    print(f"  {e['day']:<10} | {e['start']} - {e['end']} | {e['session_type']:<10} | {e['course_code']:<15} | Prof: {e['professor_name']:<25} | Room: {e['room_name']}")

# 2. Print all scheduled classes for Section 3F-Networking
print("\n" + "="*80)
print("SECTION 3F-NETWORKING: CURRENT SCHEDULE")
print("="*80)
s3f = [e for e in preview if e['section'] == '3F-Networking']
s3f.sort(key=lambda e: (e['day'], e['start']))
for e in s3f:
    print(f"  {e['day']:<10} | {e['start']} - {e['end']} | {e['session_type']:<10} | {e['course_code']:<15} | Prof: {e['professor_name']:<25} | Room: {e['room_name']}")

# 3. Print schedule of Prof Tambio
print("\n" + "="*80)
print("PROF CHRISTIAN NOLI C. TAMBIO: CURRENT SCHEDULE")
print("="*80)
pt = [e for e in preview if e.get('prof_id') == 19]
pt.sort(key=lambda e: (e['day'], e['start']))
for e in pt:
    print(f"  {e['day']:<10} | {e['start']} - {e['end']} | {e['session_type']:<10} | {e['course_code']:<15} | Sec: {e['section']:<15} | Room: {e['room_name']}")

# 4. Print schedule of Prof Ronald Santos
print("\n" + "="*80)
print("PROF RONALD S. SANTOS: CURRENT SCHEDULE")
print("="*80)
ps = [e for e in preview if e.get('prof_id') == 34]
ps.sort(key=lambda e: (e['day'], e['start']))
for e in ps:
    print(f"  {e['day']:<10} | {e['start']} - {e['end']} | {e['session_type']:<10} | {e['course_code']:<15} | Sec: {e['section']:<15} | Room: {e['room_name']}")

# 5. Print schedule of Prof Jev Corpuz
print("\n" + "="*80)
print("PROF JEV D. CORPUZ: CURRENT SCHEDULE")
print("="*80)
pj = [e for e in preview if e.get('prof_id') == 35]
pj.sort(key=lambda e: (e['day'], e['start']))
for e in pj:
    print(f"  {e['day']:<10} | {e['start']} - {e['end']} | {e['session_type']:<10} | {e['course_code']:<15} | Sec: {e['section']:<15} | Room: {e['room_name']}")

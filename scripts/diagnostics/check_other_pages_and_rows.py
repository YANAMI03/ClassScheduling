import os, sys
sys.path.insert(0, os.path.abspath('.'))
import dotenv
from bs4 import BeautifulSoup
import json

dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

client = app.app.test_client()

with client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'Scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['jwt_token'] = auth_res.session.access_token
    sess['refresh_token'] = auth_res.session.refresh_token

# 1. Run generation preview
resp = client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
print("Generate Schedule preview response status:", resp.status_code)

with client.session_transaction() as sess:
    preview = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id')) or []
    print(f"Preview entries count: {len(preview)}")
    if preview:
        sample = preview[0]
        print("Sample preview entry keys:", list(sample.keys()))
        print("Sample professor fields in preview:", {
            'prof_id': sample.get('prof_id'),
            'professor_key': sample.get('professor_key'),
            'professor_name': sample.get('professor_name'),
            'professor_load_id': sample.get('professor_load_id')
        })
        
        # Check if all preview entries carry professor_name
        missing_names = [e for e in preview if not e.get('professor_name')]
        print(f"Preview entries with missing professor_name: {len(missing_names)}")

# 2. Check Professor Schedule page: /professor_schedule
resp_prof_sched = client.get('/professor_schedule')
print("\nProfessor Schedule page (/professor_schedule) status:", resp_prof_sched.status_code)
if resp_prof_sched.status_code == 200:
    soup = BeautifulSoup(resp_prof_sched.data.decode('utf-8'), 'html.parser')
    # Look for professors listed
    cards = soup.find_all('a', href=lambda h: h and '/professor_schedule/' in h)
    print(f"Professors listed on professor_schedule page: {len(cards)}")
    for c in cards[:5]:
        print("  Prof link:", c.get('href'), "Text:", c.get_text(strip=True))

# 3. Check Section Schedule page: /schedules
resp_sec = client.get('/schedules')
print("\nSchedules page (/schedules) status:", resp_sec.status_code)

# 4. Check Room Utilization on preview page
soup_prev = BeautifulSoup(resp.data.decode('utf-8'), 'html.parser')
util_table = soup_prev.find('table', id=lambda i: i and 'room' in i.lower()) or soup_prev.find('div', class_=lambda c: c and 'room' in str(c).lower())
print("Preview page has room utilization panel:", util_table is not None)

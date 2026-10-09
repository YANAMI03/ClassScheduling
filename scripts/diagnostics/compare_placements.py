import json
import dotenv, os, sys
sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

with open('backups/professor_load_backup_latest.json') as f:
    bdata = json.load(f)
b_rows = [r for r in bdata.get('full_schedule_rows', []) if not r.get('archive')]

auth = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth.session.access_token)

client = app.app.test_client()
with client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth.session.access_token

resp = client.post('/generate_schedule', data={'semester': '2nd Semester'}, follow_redirects=True)
with client.session_transaction() as sess:
    c_rows = app._get_preview_for_user(user_id=1, preview_id=sess.get('preview_id'))

# Let's compare 1A:
print("=== SECTION 1A: BACKUP vs CURRENT ===")
b_1a = [r for r in b_rows if r['section'] == '1A']
c_1a = [r for r in c_rows if r['section'] == '1A']

b_1a.sort(key=lambda x: (x['day'], x['class_start']))
c_1a.sort(key=lambda x: (x['day'], x['start']))

print(f"Backup 1A ({len(b_1a)}):")
for r in b_1a:
    print(f"  {r['day']:<10} {r['class_start']}-{r['class_end']} | {r['session_type']:<10} | PL {r['professor_load_id']} | Room {r['room_id']}")

print(f"\nCurrent 1A ({len(c_1a)}):")
for r in c_1a:
    print(f"  {r['day']:<10} {r['start']}-{r['end']} | {r['session_type']:<10} | PL {r['professor_load_id']} | Room {r['room_id']} ({r.get('room_name')})")

# Check if ANY section has identical placements:
identical_sections = []
different_sections = []
for sec in sorted(set(r['section'] for r in b_rows)):
    bs = sorted([(r['day'], str(r['class_start']), str(r['class_end']), r['session_type'], r['room_id']) for r in b_rows if r['section'] == sec])
    cs = sorted([(r['day'], str(r['start']), str(r['end']), r['session_type'], r['room_id']) for r in c_rows if r['section'] == sec])
    if bs == cs:
        identical_sections.append(sec)
    else:
        different_sections.append(sec)

print(f"\nTotal identical sections: {len(identical_sections)}: {identical_sections}")
print(f"Total different sections: {len(different_sections)}: {different_sections}")

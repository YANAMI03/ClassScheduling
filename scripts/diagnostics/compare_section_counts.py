import json
from collections import Counter
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

b_counts = Counter(r['section'] for r in b_rows)
c_counts = Counter(r['section'] for r in c_rows)

print(f"{'Section':<22} | {'Backup':<7} | {'Current':<7} | {'Diff'}")
print("-" * 50)
for sec in sorted(set(list(b_counts.keys()) + list(c_counts.keys()))):
    bc = b_counts.get(sec, 0)
    cc = c_counts.get(sec, 0)
    diff = cc - bc
    diff_str = f"{diff:+d}" if diff != 0 else " 0"
    marker = "  <-- MISMATCH!" if diff != 0 else ""
    print(f"{sec:<22} | {bc:<7} | {cc:<7} | {diff_str}{marker}")

print(f"\nTotal Backup: {len(b_rows)} | Total Current: {len(c_rows)} | Diff: {len(c_rows) - len(b_rows)}")

import json
import dotenv
import os
import sys

dotenv.load_dotenv('.env')
import app
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

d1 = json.load(open('backups/professor_load_backup_latest.json'))
backup_rows = d1['professor_load_rows']

profs = {p['prof_id']: p for p in app.supabase.table('professor').select('*').execute().data or []}
courses = {c['course_id']: c for c in app.supabase.table('course').select('*').execute().data or []}

for i, r in enumerate(backup_rows):
    p = profs.get(r['prof_id'], {})
    c = courses.get(r['course_id'], {})
    pname = f"{p.get('first_name')} {p.get('last_name')}"
    cname = c.get('course_name')
    yl = c.get('year_level')
    print(f"{i:2d}: id={r['id']:3d} | Prof: {pname:<25} (id={r['prof_id']:2d}) | Course: {cname:<15} (id={r['course_id']:2d}, yr={yl}) | Secs: {r['sections']}")
